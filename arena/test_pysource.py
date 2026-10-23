from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from pysource import backends, parallel
from pysource.engine import Document, FileFinder, Processor, atomic_write, backup_path
from pysource.models import BackendError, HintOptions, Outcome, StripOptions, UnsupportedOperation
from pysource.text import LineIndex, apply_edits, is_directive, is_type_comment, normalize_newlines
from pysource.models import TextEdit

TRICKY = '''#!/usr/bin/env python3
"""Module docstring."""
from __future__ import annotations

import os  # trailing comment
# standalone comment
CONST: int = 3  # type: int
EMPTY: str


def plain(a, b=2):
    """Function docstring."""
    # body comment
    return a + b


def typed(a: int, b: str = "x", *args: float, **kwargs: bool) -> dict[str, int]:
    """Typed."""
    data: list[int] = []
    return {"a": 1}


def only_doc():
    """Sole statement."""


class Thing:
    """Class doc."""

    attr: int = 0

    def method(self, other: "Thing") -> None:
        """M."""
        text = "# not a comment"
        raw = \'\'\'hash # inside\'\'\'
        return None


if True:
    # only a comment
    pass
'''

EDGE = '''x = 1 + \\
    2  # continuation
y = (1 +
     # inner comment
     2)
s = "-- not a comment --"
z = f"{x!r} # no"
d = {"a": 1,  # dict
     "b": 2}
'''

FULL = StripOptions(comments=True, docstrings=True, module_docstring=True, annotations=True)
COMMENTS = StripOptions(comments=True)


def stripped(text: str, options: StripOptions = FULL, backend: str = "ast") -> str:
    return backends.resolve(backend).strip(text, options).code


def fold(tree: ast.AST) -> str:
    transformer = Normalizer()
    return ast.dump(transformer.visit(tree), include_attributes=False)


class Normalizer(ast.NodeTransformer):
    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST:
        if node.value is None:
            target = ast.unparse(node.target)
            return ast.copy_location(ast.Expr(value=ast.parse(target, mode="eval").body), node)
        return ast.copy_location(ast.Assign(targets=[node.target], value=node.value), node)

    def generic_visit(self, node: ast.AST) -> ast.AST:
        node = super().generic_visit(node)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            first = body[0] if body else None
            docstring = (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            )
            if docstring:
                node.body = body[1:] or [ast.Pass()]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.returns = None
            arguments = node.args
            for group in (arguments.posonlyargs, arguments.args, arguments.kwonlyargs):
                for item in group:
                    item.annotation = None
            for extra in (arguments.vararg, arguments.kwarg):
                if extra is not None:
                    extra.annotation = None
        return node


class TextHelpersTest(unittest.TestCase):
    def test_line_index_offsets(self) -> None:
        index = LineIndex.build("ab\ncd\n")
        self.assertEqual(index.offset(1, 0), 0)
        self.assertEqual(index.offset(2, 1), 4)
        self.assertEqual(index.line_of(4), 2)
        self.assertEqual(index.line_end(1), 3)

    def test_apply_edits_is_order_independent(self) -> None:
        source = "abcdef"
        edits = [TextEdit(0, 1, "X"), TextEdit(4, 6, "Y"), TextEdit(2, 3)]
        self.assertEqual(apply_edits(source, edits), "XbdY")
        self.assertEqual(apply_edits(source, list(reversed(edits))), "XbdY")

    def test_apply_edits_drops_overlaps(self) -> None:
        self.assertEqual(apply_edits("abcdef", [TextEdit(2, 5, "Z"), TextEdit(0, 4, "Q")]), "abZf")
        self.assertEqual(apply_edits("abcdef", [TextEdit(2, 5, "Z"), TextEdit(0, 1, "Q")]), "QbZf")

    def test_directive_detection(self) -> None:
        for comment in ("#!/usr/bin/env python", "# type: int", "# fmt: off", "# noqa", "# -*- coding: utf-8 -*-"):
            self.assertTrue(is_directive(comment), comment)
        self.assertFalse(is_directive("# a real comment"))
        self.assertTrue(is_type_comment("# type: ignore"))
        self.assertFalse(is_type_comment("# typewriter"))

    def test_newline_normalization(self) -> None:
        self.assertEqual(normalize_newlines("a\r\nb\rc\n"), "a\nb\nc\n")


class BackendTest(unittest.TestCase):
    def test_all_backends_agree_on_tricky(self) -> None:
        outputs = {name: stripped(TRICKY, FULL, name) for name in backends.available_backends()}
        self.assertEqual(len(set(outputs.values())), 1, outputs)

    def test_all_backends_agree_on_edge(self) -> None:
        outputs = {name: stripped(EDGE, FULL, name) for name in backends.available_backends()}
        self.assertEqual(len(set(outputs.values())), 1, outputs)

    def test_comments_only_preserves_ast(self) -> None:
        expected = ast.dump(ast.parse(TRICKY), include_attributes=False)
        for name in backends.available_backends():
            self.assertEqual(ast.dump(ast.parse(stripped(TRICKY, COMMENTS, name)), include_attributes=False), expected)

    def test_full_strip_preserves_semantics(self) -> None:
        expected = fold(ast.parse(TRICKY))
        for name in backends.available_backends():
            self.assertEqual(fold(ast.parse(stripped(TRICKY, FULL, name))), expected)

    def test_backends_are_idempotent(self) -> None:
        for name in backends.available_backends():
            once = stripped(TRICKY, FULL, name)
            self.assertEqual(stripped(once, FULL, name), once)

    def test_shebang_and_directives_survive(self) -> None:
        output = stripped(TRICKY, StripOptions(comments=True))
        self.assertTrue(output.startswith("#!/usr/bin/env python3"))
        self.assertIn("# type: int", output)

    def test_neutralize_keep_directives(self) -> None:
        options = StripOptions(comments=True, keep_directives=False, keep_shebang=False)
        output = stripped(TRICKY, options)
        self.assertNotIn("#!/usr/bin/env python3", output)
        self.assertNotIn("# type: int", output)

    def test_annotations_remove_type_comments(self) -> None:
        output = stripped(TRICKY, StripOptions(comments=False, annotations=True))
        self.assertNotIn("# type: int", output)
        self.assertIn("CONST = 3", output)

    def test_docstring_only_removal_keeps_comments(self) -> None:
        output = stripped(TRICKY, StripOptions(comments=False, docstrings=True))
        self.assertIn("# trailing comment", output)
        self.assertNotIn("Module docstring.", output)

    def test_module_docstring_option_leaves_function_docs(self) -> None:
        output = stripped(TRICKY, StripOptions(comments=False, module_docstring=True))
        self.assertNotIn("Module docstring.", output)
        self.assertIn("Function docstring.", output)

    def test_empty_body_gets_pass(self) -> None:
        source = "def f():\n    '''doc'''\n"
        output = stripped(source)
        self.assertEqual(output, "def f():\n    pass\n")

    def test_class_body_keeps_pass(self) -> None:
        source = "class C:\n    '''doc'''\n"
        self.assertEqual(stripped(source), "class C:\n    pass\n")

    def test_strings_are_not_comments(self) -> None:
        output = stripped(TRICKY, COMMENTS)
        self.assertIn('"# not a comment"', output)
        self.assertIn("'''hash # inside'''", output)

    def test_continuous_expression_survives(self) -> None:
        output = stripped(EDGE, COMMENTS)
        ast.parse(output)
        self.assertIn('"-- not a comment --"', output)

    def test_crlf_input_is_normalized(self) -> None:
        output = stripped("x = 1  # c\r\ny = 2\r\n", COMMENTS)
        self.assertEqual(output, "x = 1\ny = 2\n")

    def test_outcome_counters(self) -> None:
        outcome = backends.resolve("ast").strip(TRICKY, FULL)
        self.assertGreater(outcome.comments, 0)
        self.assertEqual(outcome.docstrings, 6)
        self.assertGreater(outcome.annotations, 0)
        self.assertTrue(outcome.changed)

    def test_empty_source(self) -> None:
        for name in backends.available_backends():
            self.assertEqual(stripped("", FULL, name), "")

    def test_unknown_backend_message(self) -> None:
        with self.assertRaises(BackendError):
            backends.resolve("nope")

    def test_ast_backend_cannot_add_hints(self) -> None:
        with self.assertRaises(UnsupportedOperation):
            backends.resolve("ast").add_hints("x = 1\n", "x: int\n", HintOptions())

    def test_adding_a_backend(self) -> None:
        class EchoBackend(backends.Backend):
            name = "echo-test"
            aliases = ("echo-alias",)

            def strip(self, source: str, options: StripOptions):
                return Outcome(source.upper(), backend=self.name)

        backends.register(EchoBackend)
        try:
            self.assertIsInstance(backends.resolve("echo-alias"), EchoBackend)
            self.assertEqual(backends.resolve("echo-test").strip("# c\nx = 1\n", COMMENTS).code, "# C\nX = 1\n")
        finally:
            backends.BACKENDS.pop("echo-test", None)
            backends.BACKENDS.pop("echo-alias", None)

    def test_hints_round_trip(self) -> None:
        if "libcst" not in backends.available_backends():
            self.skipTest("libcst missing")
        source = "def f(a, b):\n    return a + b\n"
        stub = "def f(a: int, b: int) -> int: ...\n"
        outcome = backends.resolve("libcst").add_hints(source, stub, HintOptions())
        self.assertIn("a: int", outcome.code)
        self.assertIn("-> int", outcome.code)


def _square(value: int) -> int:
    return value * value


def _pair(node: str, path: Path) -> tuple[str, str]:
    return node, path.name


class ParallelTest(unittest.TestCase):
    payload = [(1,), (2,), (3,), (4,)]

    def test_every_mode_returns_the_same_result(self) -> None:
        expected = [1, 4, 9, 16]
        for mode in ("serial", "mp", "starmap", "cf", "cf_map", "cf_submit", "joblib"):
            if mode == "joblib" and not _has("joblib"):
                continue
            with parallel.make_runner(mode, 2) as runner:
                self.assertEqual(sorted(runner.starmap(_square, self.payload)), expected, mode)

    def test_imap_unordered_streams(self) -> None:
        with parallel.MultiprocessingRunner(2) as runner:
            self.assertEqual(sorted(runner.imap_unordered(_square, self.payload)), [1, 4, 9, 16])

    def test_apply_async(self) -> None:
        with parallel.MultiprocessingRunner(2) as runner:
            self.assertEqual(runner.apply_async(_square, 5).get(), 25)

    def test_serial_runner_ignores_workers(self) -> None:
        runner = parallel.SerialRunner(8)
        self.assertEqual(runner.workers, 1)
        self.assertEqual(runner.map(_square, [2, 3]), [4, 9])

    def test_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            parallel.make_runner("nonsense")

    def test_runner_is_usable_as_plain_object(self) -> None:
        runner = parallel.make_runner("mp", 1)
        self.assertEqual(runner.starmap(_pair, [("a", Path("b.py"))]), [("a", "b.py")])

    def test_joblib_runner_falls_back_to_serial(self) -> None:
        if not _has("joblib"):
            self.skipTest("joblib missing")
        self.assertEqual(parallel.JoblibRunner(1).starmap(_square, self.payload), [1, 4, 9, 16])


class FileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def write(self, name: str, text: str, encoding: str = "utf-8") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding=encoding)
        return path

    def test_document_round_trip_preserves_crlf(self) -> None:
        path = self.write("crlf.py", "x = 1  # c\r\ny = 2\r\n")
        document = Document.load(path)
        self.assertTrue(document.crlf)
        self.assertEqual(document.text, "x = 1  # c\ny = 2\n")
        document.save(path, "x = 1\ny = 2\n")
        self.assertEqual(path.read_bytes(), b"x = 1\r\ny = 2\r\n")

    def test_document_detects_latin1(self) -> None:
        path = self.write("latin.py", "# -*- coding: latin-1 -*-\nname = 'caf\xe9'\n", encoding="latin-1")
        document = Document.load(path)
        self.assertIn(document.encoding, {"iso-8859-1", "latin-1"})
        self.assertIn("caf\xe9", document.text)

    def test_bom_is_preserved(self) -> None:
        path = self.root / "bom.py"
        path.write_bytes("\ufeffx = 1  # c\n".encode("utf-8"))
        document = Document.load(path)
        self.assertTrue(document.with_bom)
        Processor(backend_name="ast", strip=COMMENTS).process(path)
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(path.read_bytes()[3:], b"x = 1\n")

    def test_empty_source(self) -> None:
        path = self.write("empty.py", "")
        result = Processor(backend_name="ast", strip=COMMENTS).process(path)
        self.assertEqual(result.error, "")

    def test_atomic_write_keeps_mode(self) -> None:
        path = self.write("mode.py", "x = 1\n")
        os.chmod(path, 0o640)
        atomic_write(path, b"y = 2\n")
        self.assertEqual(path.read_bytes(), b"y = 2\n")
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_backup_names_are_unique(self) -> None:
        path = self.write("back.py", "x = 1\n")
        first = backup_path(path)
        first.write_text("old")
        second = backup_path(path)
        self.assertNotEqual(first, second)

    def test_processor_writes_and_counts(self) -> None:
        path = self.write("one.py", 'def f():\n    """doc"""\n    return 1  # c\n')
        result = Processor(backend_name="ast", strip=FULL).process(path)
        self.assertTrue(result.changed)
        self.assertEqual(result.comments, 1)
        self.assertEqual(result.docstrings, 1)
        self.assertIn("return 1\n", path.read_text())

    def test_processor_dry_run_does_not_write(self) -> None:
        source = "x = 1  # c\n"
        path = self.write("dry.py", source)
        result = Processor(backend_name="ast", strip=COMMENTS, write=False, diff=True).process(path)
        self.assertTrue(result.changed)
        self.assertEqual(path.read_text(), source)
        self.assertIn("-x = 1  # c", result.diff)

    def test_processor_backs_up(self) -> None:
        path = self.write("bak.py", "x = 1  # c\n")
        Processor(backend_name="ast", strip=COMMENTS, backup=True).process(path)
        self.assertTrue((self.root / "bak.py.bak").exists())

    def test_invalid_python_is_reported(self) -> None:
        path = self.write("broken.py", "def f(:  # c\n")
        result = Processor(backend_name="ast", strip=COMMENTS).process(path)
        self.assertNotEqual(result.error, "")
        self.assertEqual(path.read_text(), "def f(:  # c\n")

    def test_invalid_python_without_edits_is_left_alone(self) -> None:
        path = self.write("broken2.py", "def f(:\n")
        result = Processor(backend_name="ast", strip=COMMENTS).process(path)
        self.assertEqual(result.error, "")
        self.assertEqual(path.read_text(), "def f(:\n")

    def test_verify_flag_can_be_disabled(self) -> None:
        processor = Processor(backend_name="ast", strip=COMMENTS, verify=False)
        self.assertFalse(processor.verify)
        self.assertEqual(processor.strip_text("x = 1  # c\n").code, "x = 1\n")

    def test_nothing_to_do_is_an_error(self) -> None:
        path = self.write("noop.py", "x = 1\n")
        result = Processor(backend_name="ast", strip=StripOptions(comments=False)).process(path)
        self.assertIn("nothing to do", result.error)

    def test_parallel_run_matches_serial(self) -> None:
        source = 'def f():\n    """doc"""\n    return 1  # c\n'
        serial_paths = [self.write(f"serial{i}.py", source) for i in range(4)]
        pool_paths = [self.write(f"pool{i}.py", source) for i in range(4)]
        serial = Processor(backend_name="ast", strip=FULL).run(serial_paths)
        with parallel.make_runner("mp", 2) as runner:
            pooled = Processor(backend_name="ast", strip=FULL).run(pool_paths, runner)
        self.assertEqual([result.comments for result in serial], [result.comments for result in pooled])
        self.assertEqual(
            [path.read_text() for path in serial_paths],
            [path.read_text() for path in pool_paths],
        )

    def test_finder_skips_noise(self) -> None:
        self.write("keep.py", "x = 1\n")
        self.write(".git/ignored.py", "x = 1\n")
        self.write("pkg/__pycache__/cached.py", "x = 1\n")
        self.write("notes.txt", "hi\n")
        found = FileFinder().discover([self.root])
        self.assertEqual([path.name for path in found], ["keep.py"])

    def test_finder_respects_exclude(self) -> None:
        self.write("pkg/keep.py", "x = 1\n")
        self.write("vendor/skip.py", "x = 1\n")
        found = FileFinder(exclude=["vendor"]).discover([self.root])
        self.assertEqual([path.name for path in found], ["keep.py"])


def _has(module: str) -> bool:
    try:
        __import__(module)
    except ImportError:
        return False
    return True


class CommandLineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(REPO / "pystrip.py"), *args],
            capture_output=True,
            text=True,
            cwd=str(REPO),
            check=False,
        )

    def test_list_command(self) -> None:
        result = self.run_cli("list")
        self.assertEqual(result.returncode, 0)
        self.assertIn("ast", result.stdout)
        self.assertIn("joblib", result.stdout)

    def test_strip_writes_files(self) -> None:
        path = self.root / "a.py"
        path.write_text("x = 1  # c\n")
        result = self.run_cli("strip", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_text(), "x = 1\n")

    def test_json_report(self) -> None:
        path = self.root / "a.py"
        path.write_text('def f():\n    """d"""\n    return 1  # c\n')
        result = self.run_cli("strip", str(path), "-n", "--json", "--docstrings")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["totals"]["files"], 1)
        self.assertEqual(payload["totals"]["changed"], 1)
        self.assertEqual(payload["files"][0]["comments"], 1)

    def test_check_mode_exit_code(self) -> None:
        path = self.root / "a.py"
        path.write_text("x = 1  # c\n")
        self.assertEqual(self.run_cli("strip", str(path), "-n", "--check").returncode, 1)
        path.write_text("x = 1\n")
        self.assertEqual(self.run_cli("strip", str(path), "--check").returncode, 0)

    def test_find_command(self) -> None:
        (self.root / "sub").mkdir()
        (self.root / "sub" / "b.py").write_text("x = 1\n")
        result = self.run_cli("find", "-a", str(self.root))
        self.assertIn("b.py", result.stdout)

    def test_missing_selection_is_an_error(self) -> None:
        self.assertEqual(self.run_cli("strip", str(self.root)).returncode, 2)

    def test_hints_need_a_source(self) -> None:
        path = self.root / "a.py"
        path.write_text("x = 1\n")
        self.assertEqual(self.run_cli("annotate", str(path)).returncode, 2)

    def test_stub_file_round_trip(self) -> None:
        if not _has("libcst"):
            self.skipTest("libcst missing")
        source = self.root / "a.py"
        source.write_text("def add(a, b):\n    return a + b\n")
        stub = self.root / "a.pyi"
        stub.write_text("def add(a: int, b: int) -> int: ...\n")
        result = self.run_cli("annotate", str(source), "-s", str(stub))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a: int", source.read_text())


class ReferenceCorpusTest(unittest.TestCase):
    def test_originals_are_archived(self) -> None:
        originals = sorted(path.name for path in (REPO / "pysource" / "orig" / "upstream").glob("*.py"))
        self.assertEqual(len(originals), 29)
        self.assertIn("drmc.py", originals)
        self.assertIn("annotate.py", originals)

    def test_every_archived_script_is_accounted_for(self) -> None:
        readme = (REPO / "pysource" / "orig" / "README.md").read_text()
        for path in (REPO / "pysource" / "orig" / "upstream").glob("*.py"):
            self.assertIn(path.name, readme)

    def test_consolidated_scripts_are_gone_from_root(self) -> None:
        for name in ("cleanpy.py", "clean_type_annotations.py", "py_clean.py", "pyclean.py"):
            self.assertFalse((REPO / name).exists(), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
