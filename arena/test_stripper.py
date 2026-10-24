#!/usr/bin/env python3
"""Tests for ../stripper.py.

The regex backend is exercised directly so the suite runs without tree-sitter
grammars; the tree-sitter path is checked only for availability reporting.

    python3 arena/test_stripper.py
    python3 arena/test_stripper.py -v
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STRIPPER = REPO_ROOT / "stripper.py"
sys.path.insert(0, str(REPO_ROOT))

import stripper  # noqa: E402

FIXTURES: dict[str, tuple[str, str]] = {
    "a.js": (
        '// leading\nconst url = "http://x//y"; // trailing\n/* block */\nfunction f() {\n    return 1;\n}\n',
        'const url = "http://x//y";\nfunction f() {\n    return 1;\n}\n',
    ),
    "a.rs": (
        '// docs\nuse std::fmt; // trailing\nfn f<\'a>(s: &\'a str) -> &\'a str {\n    s\n}\n',
        "use std::fmt;\nfn f<'a>(s: &'a str) -> &'a str {\n    s\n}\n",
    ),
    "a.css": (
        "/* head */\nbody { color: red; } /* trailing */\n",
        "body { color: red; }\n",
    ),
    "a.html": (
        "<!-- comment -->\n<p>hi</p>\n",
        "<p>hi</p>\n",
    ),
    "a.sh": (
        '#!/usr/bin/env bash\n# comment\necho "http://x//y" # trailing\necho \'# kept\'\n',
        '#!/usr/bin/env bash\necho "http://x//y"\necho \'# kept\'\n',
    ),
    "a.lua": (
        '-- line\nlocal s = "http://x//y" -- trailing\n--[[ block ]]\nprint(s)\n',
        'local s = "http://x//y"\nprint(s)\n',
    ),
    "a.toml": (
        '# comment\nname = "p" # trailing\ndesc = """\n# kept\n"""\n',
        'name = "p"\ndesc = """\n# kept\n"""\n',
    ),
    "a.vim": (
        '" comment\nlet s = \'a "q" b\'\nset nu " trailing\n',
        "let s = 'a \"q\" b'\nset nu\n",
    ),
    "Dockerfile": (
        "# base\nFROM python:3.11\n",
        "FROM python:3.11\n",
    ),
    "a.yaml": (
        "# head\nkey: value # trailing\n",
        "key: value\n",
    ),
    "a.sql": (
        "-- head\nSELECT 1; -- trailing\n/* block */\n",
        "SELECT 1;\n",
    ),
}


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(STRIPPER), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )


class RegexBackendTest(unittest.TestCase):
    def test_languages(self) -> None:
        backend = stripper.RegexBackend()
        for name, (source, expected) in FIXTURES.items():
            suffix = Path(name).suffix
            language = next(
                item
                for item in stripper.LANGUAGES.values()
                if (suffix and suffix in item.extensions) or name in item.filenames
            )
            spans = backend.spans(source, language)
            cleaned, removed = stripper.TextSpans.apply(source, spans)
            cleaned = stripper.TextSpans.collapse(cleaned)
            with self.subTest(file=name, language=language.name):
                self.assertEqual(cleaned, expected)
                self.assertGreater(removed, 0)

    def test_shebang_is_preserved(self) -> None:
        language = stripper.LANGUAGES["bash"]
        spans = stripper.RegexBackend().spans("#!/bin/sh\n# x\necho hi\n", language)
        self.assertEqual([text for text in ("#!/bin/sh\n# x\necho hi\n",) if spans], ["#!/bin/sh\n# x\necho hi\n"])
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0][0], len("#!/bin/sh\n"))

    def test_string_with_comment_markers_is_untouched(self) -> None:
        language = stripper.LANGUAGES["js"]
        source = 'const a = "/* not a comment */";\nconst b = `// nope`;\n'
        spans = stripper.RegexBackend().spans(source, language)
        self.assertEqual(spans, [])

    def test_already_clean_file_reports_nothing(self) -> None:
        language = stripper.LANGUAGES["rs"]
        self.assertEqual(stripper.RegexBackend().spans("fn main() {}\n", language), [])

    def test_regex_literals_are_not_comments(self) -> None:
        backend = stripper.RegexBackend()
        source = "const a = /ab+c/; // trailing\nconst b = /[//]/;\nconst c = 4 / 2; // div\n"
        cleaned, removed = stripper.TextSpans.apply(source, backend.spans(source, stripper.LANGUAGES["js"]))
        self.assertEqual(removed, 2)
        self.assertEqual(cleaned, "const a = /ab+c/;\nconst b = /[//]/;\nconst c = 4 / 2;\n")

    def test_lua_long_string_keeps_dashes(self) -> None:
        backend = stripper.RegexBackend()
        source = "local s = [[ -- not a comment ]]\n-- real\n"
        spans = backend.spans(source, stripper.LANGUAGES["lua"])
        self.assertEqual([source[start:end] for start, end in spans], ["-- real"])

    def test_ruby_regex_with_hash_is_kept(self) -> None:
        backend = stripper.RegexBackend()
        source = "re = /[#]/ # trailing\n"
        cleaned, removed = stripper.TextSpans.apply(source, backend.spans(source, stripper.LANGUAGES["rb"]))
        self.assertEqual(removed, 1)
        self.assertEqual(cleaned, "re = /[#]/\n")


class CliTest(unittest.TestCase):
    def make_tree(self, directory: Path) -> None:
        for name, (source, _) in FIXTURES.items():
            (directory / name).write_text(source, encoding="utf-8")
        (directory / "code.py").write_text("x = 1  # python\n", encoding="utf-8")

    def test_all_autodetects_and_leaves_python_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            result = run_cli("-a", str(root), "-b", "regex", "-j", "2")
            self.assertEqual(result.returncode, 0, result.stderr)
            for name, (_, expected) in FIXTURES.items():
                with self.subTest(file=name):
                    self.assertEqual((root / name).read_text(encoding="utf-8"), expected)
            self.assertEqual((root / "code.py").read_text(encoding="utf-8"), "x = 1  # python\n")

    def test_single_language_flag_only_touches_that_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            result = run_cli("--js", str(root), "-b", "regex")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "a.js").read_text(encoding="utf-8"), FIXTURES["a.js"][1])
            self.assertEqual((root / "a.rs").read_text(encoding="utf-8"), FIXTURES["a.rs"][0])

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            result = run_cli("-a", str(root), "-b", "regex", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("would strip", result.stdout)
            self.assertIn("---", result.stdout)
            for name, (source, _) in FIXTURES.items():
                self.assertEqual((root / name).read_text(encoding="utf-8"), source)

    def test_backup_creates_bak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.js").write_text(FIXTURES["a.js"][0], encoding="utf-8")
            result = run_cli("--js", str(root), "-b", "regex", "--backup", "-q")
            self.assertEqual(result.returncode, 0, result.stderr)
            backup = root / "a.js.bak"
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(encoding="utf-8"), FIXTURES["a.js"][0])

    def test_second_run_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            run_cli("-a", str(root), "-b", "regex")
            again = run_cli("-a", str(root), "-b", "regex")
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("stripped 0/", again.stdout)

    def test_requires_a_language(self) -> None:
        result = run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("pick a language flag", result.stderr)

    def test_list_languages(self) -> None:
        result = run_cli("--list-languages")
        self.assertEqual(result.returncode, 0)
        for name in ("--js", "--rs", "--cpp", "--bash", "--vim"):
            self.assertIn(name, result.stdout)

    def test_keep_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.c").write_text("// one\n\n\nint x;\n", encoding="utf-8")
            run_cli("--c", str(root), "-b", "regex")
            self.assertEqual((root / "a.c").read_text(encoding="utf-8"), "int x;\n")
            (root / "b.c").write_text("// one\n\n\nint x;\n", encoding="utf-8")
            run_cli("--c", str(root), "-b", "regex", "--keep-blank-lines")
            self.assertEqual((root / "b.c").read_text(encoding="utf-8"), "\n\nint x;\n")


class LayoutTest(unittest.TestCase):
    def test_cleaners_live_in_subfolder(self) -> None:
        cleaners = REPO_ROOT / "cleaners"
        self.assertTrue(cleaners.is_dir())
        language_scripts = sorted(path.name for path in cleaners.glob("clean*.py"))
        self.assertIn("clean-rs.py", language_scripts)
        self.assertIn("cleants2.py", language_scripts)

    def test_python_tool_replaces_the_old_root_scripts(self) -> None:
        self.assertTrue((REPO_ROOT / "pystrip.py").is_file())
        self.assertTrue((REPO_ROOT / "pysource" / "backends.py").is_file())
        for name in ("cleanpy.py", "clean_type_annotations.py", "py_clean.py", "pyclean.py"):
            self.assertFalse((REPO_ROOT / name).exists(), name)
            self.assertTrue((REPO_ROOT / "pysource" / "orig" / "repo" / name).is_file(), name)

    def test_no_python_flag(self) -> None:
        self.assertNotIn("py", stripper.LANGUAGES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
