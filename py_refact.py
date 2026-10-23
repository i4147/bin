#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
import argparse
import ast
import hashlib
from pathlib import Path
import re
import sys

WORKERS: int = 6
FILE_SENTINEL = re.compile("^#\\s*File:\\s*(.+?)\\s*$")
SIX_MOVES_MAP: dict[str, str] = {
    "six.moves.copyreg": "copyreg",
    "six.moves.urllib": "urllib",
    "six.moves.http_client": "http.client",
    "six.moves.cPickle": "pickle",
    "six.moves.input": "input",
    "six.moves.zip": "zip",
    "six.moves.map": "map",
    "six.moves.range": "range",
}
TEXT_REWRITES: list[tuple[re.Pattern, str]] = [
    (re.compile("^\\s*from __future__ import [^\\n]+\\n", re.MULTILINE), ""),
    (re.compile("\\bunicode\\s*\\(([^,()]+),\\s*['\\\"]utf-8['\\\"]\\s*\\)"), "\\1.decode('utf-8')"),
    (re.compile("(?<!\\.)\\bunicode\\b(?!\\s*=\\s*['\\\"])"), "str"),
    (re.compile("\\bbasestring\\b"), "(str, bytes)"),
    (re.compile("\\bstring_types\\b"), "str"),
    (re.compile("\\bbinary_type\\b"), "bytes"),
    (re.compile("\\bxrange\\b"), "range"),
    (re.compile("(\\w+)\\.iteritems\\(\\)"), "\\1.items()"),
    (re.compile("(\\w+)\\.iterkeys\\(\\)"), "\\1.keys()"),
    (re.compile("(\\w+)\\.itervalues\\(\\)"), "\\1.values()"),
    (re.compile("\\bsuper\\(\\s*\\w+\\s*,\\s*self\\s*\\)"), "super()"),
    (re.compile("\\bdef __unicode__\\b"), "def __str__"),
    (re.compile("^\\s*@implements_to_string\\s*\\n", re.MULTILINE), ""),
]
OS_PATH_REWRITES: list[tuple[re.Pattern, str]] = [
    (re.compile("\\bos\\.path\\.exists\\(([^()]*)\\)"), "Path(\\1).exists()"),
    (re.compile("\\bos\\.path\\.isdir\\(([^()]*)\\)"), "Path(\\1).is_dir()"),
    (re.compile("\\bos\\.path\\.isfile\\(([^()]*)\\)"), "Path(\\1).is_file()"),
    (re.compile("\\bos\\.makedirs\\(([^,()]*)\\)"), "Path(\\1).mkdir(parents=True, exist_ok=True)"),
    (re.compile("\\bos\\.remove\\(([^()]*)\\)"), "Path(\\1).unlink()"),
    (re.compile("\\bos\\.listdir\\(([^()]*)\\)"), "list(Path(\\1).iterdir())"),
    (re.compile("\\bos\\.path\\.abspath\\(([^()]*)\\)"), "Path(\\1).resolve()"),
    (re.compile("\\bos\\.path\\.expanduser\\(([^()]*)\\)"), "Path(\\1).expanduser()"),
    (re.compile("\\bos\\.path\\.basename\\(([^()]*)\\)"), "Path(\\1).name"),
    (re.compile("\\bos\\.path\\.dirname\\(([^()]*)\\)"), "str(Path(\\1).parent)"),
    (re.compile("\\bos\\.path\\.splitext\\(([^()]*)\\)"), "(Path(\\1).stem, Path(\\1).suffix)"),
]
AMBIGUOUS_MARKERS: list[re.Pattern] = [
    re.compile("\\bos\\.path\\.join\\("),
    re.compile("\\bos\\.path\\.split\\("),
]
POOL_BLOCK_RE = re.compile(
    "with\\s+(?:ProcessPoolExecutor|ThreadPoolExecutor)\\s*\\(\\s*(?:max_workers\\s*=\\s*[^)]*)?\\)\\s+as\\s+(\\w+)\\s*:\\s*\\n((?:[ \\t]+.*\\n)*?)[ \\t]*for\\s+(\\w+)\\s+in\\s+\\1\\.map\\(([^,]+),\\s*([^)]+)\\)\\s*:"
)
LOGGING_IMPORT_RE = re.compile("^import logging\\n", re.MULTILINE)
LOGGER_GETLOGGER_RE = re.compile("^\\s*\\w+\\s*=\\s*logging\\.getLogger\\([^)]*\\)\\n", re.MULTILINE)
BASICCONFIG_RE = re.compile("logging\\.basicConfig\\([^)]*\\)")
WORKERS_ARG_RE = re.compile("(?:workers\\s*=\\s*[\\w.]+\\s*,?\\s*)")
WORKERS_CLI_LINE_RE = re.compile("^.*add_argument\\(\\s*['\\\"](-w|--workers)['\\\"].*\\n", re.MULTILINE)
PICKLE_METHOD_BLOCK_RE = re.compile(
    "^\\s*copyreg\\.pickle\\(types\\.MethodType,.*?\\)\\s*\\n(?:^\\s*def _(?:un)?pickle_method\\b.*(?:\\n(?:[ \\t].*)?)*\\n?)*",
    re.MULTILINE,
)


class PyRefact:
    @staticmethod
    def find_source_files(root: Path) -> list[Path]:
        return sorted((p for p in root.rglob("*.py") if p.is_file()))

    @staticmethod
    def parse_merged_file(merged_path: Path) -> dict[str, str]:
        files: dict[str, list[str]] = {}
        current: str | None = None
        for line in merged_path.read_text(encoding="utf-8").splitlines():
            match = FILE_SENTINEL.match(line)
            if match:
                current = match.group(1)
                files[current] = []
                continue
            if current is not None:
                files[current].append(line)
        return {name: "\n".join(lines) for name, lines in files.items()}

    @staticmethod
    def load_sources(args: argparse.Namespace) -> dict[str, str]:
        if args.file:
            merged_path = Path(args.file).expanduser().resolve()
            if not merged_path.is_file():
                sys.exit(f"error: -f target not found: {merged_path}")
            sources = PyRefact.parse_merged_file(merged_path)
            if not sources:
                sys.exit("error: no '# File: ...' sentinels found in -f input")
            return sources
        root = Path.cwd()
        py_files = PyRefact.find_source_files(root)
        if not py_files:
            sys.exit(f"error: no .py files found under {root}")
        return {str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in py_files}

    @staticmethod
    def strip_six_and_py2(text: str) -> str:
        for pattern, repl in TEXT_REWRITES:
            text = pattern.sub(repl, text)
        for six_path, native in SIX_MOVES_MAP.items():
            text = text.replace(six_path, native)
        text = PICKLE_METHOD_BLOCK_RE.sub("", text)
        return text

    @staticmethod
    def flag_ambiguous_paths(text: str) -> str:
        lines = text.splitlines()
        out = []
        for line in lines:
            if any((p.search(line) for p in AMBIGUOUS_MARKERS)) and "TODO(manual-review)" not in line:
                out.append(
                    line
                    + "  # TODO(manual-review): verify this is a filesystem path, not a URL, before trusting the pathlib rewrite"
                )
            else:
                out.append(line)
        return "\n".join(out)

    @staticmethod
    def rewrite_os_path(text: str) -> str:
        for pattern, repl in OS_PATH_REWRITES:
            text = pattern.sub(repl, text)
        return PyRefact.flag_ambiguous_paths(text)

    @staticmethod
    def rewrite_parallelism(text: str) -> str:
        text = re.sub("^from concurrent\\.futures import[^\\n]*\\n", "", text, flags=re.MULTILINE)

        def _replace_pool_block(match: re.Match) -> str:
            _loop_var, _body, item_var, fn_name, iterable = match.groups()
            return f"with mp.Pool(WORKERS) as pool:\n    for {item_var} in pool.imap_unordered({fn_name.strip()}, {iterable.strip()}):"

        text = POOL_BLOCK_RE.sub(_replace_pool_block, text)
        text = WORKERS_CLI_LINE_RE.sub("", text)
        text = WORKERS_ARG_RE.sub("", text)
        return text

    @staticmethod
    def rewrite_logging(text: str) -> str:
        text = LOGGING_IMPORT_RE.sub("from loguru import logger\n", text)
        text = LOGGER_GETLOGGER_RE.sub("", text)
        text = BASICCONFIG_RE.sub(
            'logger.remove()\nlogger.add(sys.stderr, level="INFO", format="{time:YYYY-MM-DD HH:mm:ss.SSS} {level} {file.name}:{line} {message}")',
            text,
        )
        return text

    @staticmethod
    def strip_relative_imports(text: str) -> str:
        text = re.sub("^\\s*from \\.+\\S*\\s+import[^\\n]*\\n", "", text, flags=re.MULTILINE)
        text = re.sub("^\\s*import \\.+\\S*\\n", "", text, flags=re.MULTILINE)
        return text

    @staticmethod
    def apply_all_rewrites(text: str) -> str:
        text = PyRefact.strip_six_and_py2(text)
        text = PyRefact.rewrite_os_path(text)
        text = PyRefact.rewrite_parallelism(text)
        text = PyRefact.rewrite_logging(text)
        text = PyRefact.strip_relative_imports(text)
        return text

    @staticmethod
    def block_hash(node_source: str) -> str:
        normalized = "\n".join((line.rstrip() for line in node_source.strip().splitlines()))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def collect_top_level_items(filename: str, text: str) -> tuple[list[str], list[CollectedItem]]:
        try:
            tree = ast.parse(text, filename=filename)
        except SyntaxError as exc:
            print(
                f"warning: {filename} failed to parse after rewrites ({exc}); skipping its body",
                file=sys.stderr,
            )
            return ([], [])
        lines = text.splitlines()
        imports: list[str] = []
        items: list[CollectedItem] = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append(ast.unparse(node))
                continue
            start = node.lineno - 1
            end = getattr(node, "end_lineno", start + 1)
            source = "\n".join(lines[start:end])
            if isinstance(node, ast.ClassDef):
                items.append(CollectedItem(node.name, "class", source, filename, node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "main":
                    continue
                items.append(CollectedItem(node.name, "function", source, filename, node))
            elif (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.isupper()
            ):
                items.append(CollectedItem(node.targets[0].id, "constant", source, filename, node))
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id.isupper()
            ):
                items.append(CollectedItem(node.target.id, "constant", source, filename, node))
        return (imports, items)

    @staticmethod
    def dedup_by_hash(items: list[CollectedItem]) -> list[CollectedItem]:
        seen_hashes: set[str] = set()
        result: list[CollectedItem] = []
        for item in items:
            if item.hash in seen_hashes:
                continue
            seen_hashes.add(item.hash)
            result.append(item)
        return result

    @staticmethod
    def resolve_name_collisions(items: list[CollectedItem]) -> list[CollectedItem]:
        by_name: dict[str, list[CollectedItem]] = {}
        for item in items:
            by_name.setdefault(item.name, []).append(item)
        resolved: list[CollectedItem] = []
        for name, group in by_name.items():
            if len(group) == 1:
                resolved.append(group[0])
                continue
            print(
                f"note: {len(group)} distinct definitions of '{name}' found ({', '.join((i.origin for i in group))}); keeping the first, renaming the rest with a numeric suffix",
                file=sys.stderr,
            )
            for idx, item in enumerate(group):
                if idx == 0:
                    resolved.append(item)
                    continue
                new_name = f"{name}_{idx}"
                item.source = re.sub(f"\\b{re.escape(name)}\\b", new_name, item.source, count=1)
                item.name = new_name
                resolved.append(item)
        return resolved

    @staticmethod
    def extract_referenced_names(source: str) -> set[str]:
        return set(re.findall("\\b([A-Za-z_][A-Za-z0-9_]*)\\b", source))

    @staticmethod
    def topo_sort_items(items: list[CollectedItem]) -> list[CollectedItem]:
        names = {item.name for item in items}
        by_name = {item.name: item for item in items}
        deps: dict[str, set[str]] = {}
        for item in items:
            refs = PyRefact.extract_referenced_names(item.source) & names
            refs.discard(item.name)
            deps[item.name] = refs
        in_degree = dict.fromkeys(by_name, 0)
        dependents: dict[str, list[str]] = {name: [] for name in by_name}
        for name, refs in deps.items():
            for ref in refs:
                in_degree[name] += 1
                dependents[ref].append(name)
        queue = sorted((name for name, deg in in_degree.items() if deg == 0))
        ordered: list[str] = []
        while queue:
            queue.sort()
            current = queue.pop(0)
            ordered.append(current)
            for dependent in dependents[current]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)
        if len(ordered) != len(by_name):
            remaining = sorted(set(by_name) - set(ordered))
            ordered.extend(remaining)
        return [by_name[name] for name in ordered]

    @staticmethod
    def optimize_imports(raw_imports: list[str]) -> str:
        unique = sorted({line for line in raw_imports if line.strip()})
        stdlib_modules = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()
        stdlib_lines: list[str] = []
        thirdparty_lines: list[str] = []
        for line in unique:
            if line.startswith("import "):
                top_module = line[len("import ") :].split(".")[0].split(" as ")[0].strip()
            elif line.startswith("from "):
                top_module = line[len("from ") :].split(".")[0].split(" import")[0].strip()
            else:
                top_module = ""
            if top_module in stdlib_modules or top_module in {
                "os",
                "sys",
                "pathlib",
                "re",
                "ast",
                "hashlib",
                "argparse",
            }:
                stdlib_lines.append(line)
            else:
                thirdparty_lines.append(line)
        parts = []
        if stdlib_lines:
            parts.append("\n".join(sorted(stdlib_lines)))
        if thirdparty_lines:
            parts.append("\n".join(sorted(thirdparty_lines)))
        return "\n\n".join(parts)

    @staticmethod
    def build_module_docstring(
        filenames: list[str],
        n_constants: int,
        n_classes: int,
        n_functions: int,
        used_pathlib: bool,
        used_pool: bool,
        used_loguru: bool,
    ) -> str:
        features = []
        if used_pathlib:
            features.append("pathlib.Path for all filesystem operations")
        if used_pool:
            features.append(f"multiprocessing.Pool with WORKERS = {WORKERS} for any parallel work")
        if used_loguru:
            features.append("loguru for logging, configured via logger.remove()/logger.add()")
        feature_text = (
            "; ".join(features) if features else "no additional infrastructure beyond the merged API"
        )
        lines = [
            "TODO(manual-review): this docstring is a generated summary, not a verified",
            "original prompt. Replace it with an accurate description before relying on it",
            "as documentation, or hand it to an LLM to phrase as a proper generation prompt.",
            "",
            f"Source files merged ({len(filenames)}): {', '.join(filenames)}",
            f"Result: {n_constants} constant(s), {n_classes} class(es), {n_functions} function(s).",
            f"Target: Python 3.12, Linux/Termux, no Python 2 compatibility layer.",
            f"Infrastructure: {feature_text}.",
        ]
        return '"""\n' + "\n".join(lines) + '\n"""\n'

    @staticmethod
    def strip_docstrings_and_comments(source: str) -> str:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body.pop(0)
                    if not body:
                        body.append(ast.Pass())
        return ast.unparse(tree)

    @staticmethod
    def determine_output_path(requested: str | None) -> Path:
        candidate = Path(requested) if requested else Path("out.py")
        if not candidate.exists():
            return candidate
        n = 1
        while True:
            alt = candidate.with_name(f"{candidate.stem}_{n}{candidate.suffix}")
            if not alt.exists():
                print(f"note: '{candidate}' already exists, writing to '{alt}' instead", file=sys.stderr)
                return alt
            n += 1

    @staticmethod
    def build_output(sources: dict[str, str]) -> str:
        all_imports: list[str] = []
        all_items: list[CollectedItem] = []
        used_pool = used_loguru = used_pathlib = False
        for filename, raw_text in sorted(sources.items()):
            rewritten = PyRefact.apply_all_rewrites(raw_text)
            used_pool = used_pool or "mp.Pool" in rewritten
            used_loguru = used_loguru or "loguru" in rewritten
            used_pathlib = used_pathlib or "Path(" in rewritten
            file_imports, file_items = PyRefact.collect_top_level_items(filename, rewritten)
            all_imports.extend(file_imports)
            all_items.extend(file_items)
        all_items = PyRefact.dedup_by_hash(all_items)
        all_items = PyRefact.resolve_name_collisions(all_items)
        constants = sorted((i for i in all_items if i.kind == "constant"), key=lambda i: i.name)
        classes = PyRefact.topo_sort_items([i for i in all_items if i.kind == "class"])
        functions = PyRefact.topo_sort_items([i for i in all_items if i.kind == "function"])
        if used_pool:
            all_imports.append("import multiprocessing as mp")
        if used_pathlib:
            all_imports.append("from pathlib import Path")
        if used_loguru:
            all_imports.append("from loguru import logger")
            all_imports.append("import sys")
        imports_block = PyRefact.optimize_imports(all_imports)
        body_sections = []
        if used_pool:
            body_sections.append(f"WORKERS: int = {WORKERS}\n")
        if constants:
            body_sections.append("\n".join((i.source.strip() for i in constants)))
        if classes:
            body_sections.append("\n\n\n".join((i.source.strip() for i in classes)))
        if functions:
            body_sections.append("\n\n\n".join((i.source.strip() for i in functions)))
        public_names = sorted((i.name for i in constants + classes + functions))
        all_decl = "__all__ = [\n" + "".join((f'    "{n}",\n' for n in public_names)) + "]\n"
        body = "\n\n\n".join((s for s in body_sections if s))
        stripped_body = PyRefact.strip_docstrings_and_comments(body) if body.strip() else body
        docstring = PyRefact.build_module_docstring(
            filenames=list(sources.keys()),
            n_constants=len(constants),
            n_classes=len(classes),
            n_functions=len(functions),
            used_pathlib=used_pathlib,
            used_pool=used_pool,
            used_loguru=used_loguru,
        )
        return docstring + "\n" + imports_block.strip() + "\n\n\n" + all_decl + "\n\n" + stripped_body + "\n"

    @staticmethod
    def main() -> None:
        parser = argparse.ArgumentParser(
            description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
        )
        parser.add_argument("-f", "--file", help="merged input file with '# File: relpath' sentinels")
        parser.add_argument("-o", "--output", help="output .py filename (default: out.py)")
        args = parser.parse_args()
        sources = PyRefact.load_sources(args)
        output_src = PyRefact.build_output(sources)
        try:
            ast.parse(output_src)
        except SyntaxError as exc:
            print(
                f"warning: generated output has a syntax issue and needs manual review: {exc}",
                file=sys.stderr,
            )
        out_path = PyRefact.determine_output_path(args.output)
        out_path.write_text(output_src, encoding="utf-8")
        print(
            f"wrote {out_path} ({len(sources)} source files merged, {output_src.count('TODO(manual-review)')} manual-review markers)"
        )


class CollectedItem:
    __slots__ = ("hash", "kind", "name", "node", "origin", "source")

    def __init__(self, name: str, kind: str, source: str, origin: str, node: ast.AST) -> None:
        self.name = name
        self.kind = kind
        self.source = source
        self.hash = PyRefact.block_hash(source)
        self.origin = origin
        self.node = node


if __name__ == "__main__":
    PyRefact.main()
