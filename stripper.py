#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations

import argparse
import bisect
import difflib
import importlib
import os
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

COMMENT_NODE_TYPES: frozenset[str] = frozenset(
    {"comment", "line_comment", "block_comment", "comment_block", "comment_line", "doc_comment", "Comment"}
)
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        ".tox",
        ".idea",
        ".vscode",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        "site-packages",
        "dist",
        "build",
        "venv",
        "env",
    }
)
SKIP_SUFFIXES: frozenset[str] = frozenset({".bak", ".pyc", ".min.js", ".min.css"})
DEFAULT_WORKERS: int = min(8, os.cpu_count() or 1)
JS_QUOTES: tuple[str, ...] = ('"', "'", "`")
C_QUOTES: tuple[str, ...] = ('"', "'")
SLASH_MARKERS: tuple[tuple[str, str], ...] = (("/*", "*/"),)
TRIPLE_QUOTES: tuple[tuple[str, str], ...] = (('"""', '"""'), ("'''", "'''"))
NUL: bytes = b"\0"
REGEX_PREFIX_CHARS: frozenset[str] = frozenset("(,=:[!&|?{};+-*%<>~^ \n\t")
REGEX_PREFIX_WORDS: frozenset[str] = frozenset(
    {"return", "typeof", "case", "delete", "void", "throw", "new", "in", "of", "yield", "instanceof", "do", "else"}
)


@dataclass(frozen=True, slots=True)
class Language:
    name: str
    extensions: tuple[str, ...]
    module: str
    attribute: str = "language"
    line: tuple[str, ...] = ()
    block: tuple[tuple[str, str], ...] = ()
    quotes: tuple[str, ...] = C_QUOTES
    long_quotes: tuple[tuple[str, str], ...] = ()
    shebang: bool = False
    filenames: tuple[str, ...] = ()
    regex_literals: bool = False


LANGUAGES: dict[str, Language] = {
    "bash": Language("bash", (".sh", ".bash", ".zsh", ".ksh"), "tree_sitter_bash", line=("#",), shebang=True),
    "c": Language("c", (".c", ".h"), "tree_sitter_c", line=("//",), block=SLASH_MARKERS),
    "cpp": Language(
        "cpp",
        (".cc", ".cpp", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h++", ".inl", ".ipp"),
        "tree_sitter_cpp",
        line=("//",),
        block=SLASH_MARKERS,
    ),
    "css": Language("css", (".css", ".scss", ".sass", ".less"), "tree_sitter_css", block=SLASH_MARKERS),
    "dockerfile": Language("dockerfile", (), "tree_sitter_dockerfile", line=("#",), filenames=("Dockerfile",)),
    "go": Language("go", (".go",), "tree_sitter_go", line=("//",), block=SLASH_MARKERS, quotes=JS_QUOTES),
    "html": Language("html", (".html", ".htm", ".xhtml"), "tree_sitter_html", block=(("<!--", "-->"),)),
    "java": Language("java", (".java",), "tree_sitter_java", line=("//",), block=SLASH_MARKERS),
    "js": Language(
        "js",
        (".js", ".mjs", ".cjs", ".jsx"),
        "tree_sitter_javascript",
        line=("//",),
        block=SLASH_MARKERS,
        quotes=JS_QUOTES,
        regex_literals=True,
    ),
    "lua": Language(
        "lua",
        (".lua",),
        "tree_sitter_lua",
        line=("--",),
        block=(("--[[", "]]"),),
        long_quotes=(("[[", "]]"),),
    ),
    "make": Language("make", (".mk",), "tree_sitter_make", line=("#",), filenames=("Makefile", "GNUmakefile")),
    "php": Language(
        "php",
        (".php", ".phtml"),
        "tree_sitter_php",
        attribute="language_php",
        line=("//", "#"),
        block=SLASH_MARKERS,
        quotes=JS_QUOTES,
    ),
    "rb": Language(
        "rb",
        (".rb", ".rake", ".gemspec"),
        "tree_sitter_ruby",
        line=("#",),
        long_quotes=TRIPLE_QUOTES,
        regex_literals=True,
    ),
    "rs": Language(
        "rs",
        (".rs",),
        "tree_sitter_rust",
        line=("//",),
        block=SLASH_MARKERS,
        quotes=('"',),
        long_quotes=(('r#"', '"#'),),
    ),
    "sql": Language("sql", (".sql",), "tree_sitter_sql", line=("--",), block=SLASH_MARKERS),
    "toml": Language("toml", (".toml",), "tree_sitter_toml", line=("#",), long_quotes=TRIPLE_QUOTES),
    "ts": Language(
        "ts",
        (".ts", ".mts", ".cts"),
        "tree_sitter_typescript",
        line=("//",),
        block=SLASH_MARKERS,
        quotes=JS_QUOTES,
        regex_literals=True,
    ),
    "tsx": Language(
        "tsx",
        (".tsx",),
        "tree_sitter_tsx",
        line=("//",),
        block=SLASH_MARKERS,
        quotes=JS_QUOTES,
        regex_literals=True,
    ),
    "vim": Language("vim", (".vim",), "tree_sitter_vim", line=('"',), quotes=("'",)),
    "xml": Language(
        "xml",
        (".xml", ".svg", ".xsl", ".xsd", ".plist"),
        "tree_sitter_xml",
        attribute="language_xml",
        block=(("<!--", "-->"),),
    ),
    "yaml": Language("yaml", (".yaml", ".yml"), "tree_sitter_yaml", line=("#",), long_quotes=TRIPLE_QUOTES),
}

ALIASES: dict[str, str] = {
    "shell": "bash",
    "sh": "bash",
    "hpp": "cpp",
    "golang": "go",
    "javascript": "js",
    "mjs": "js",
    "jsx": "js",
    "ruby": "rb",
    "rust": "rs",
    "typescript": "ts",
    "yml": "yaml",
}
LANGUAGE_NAMES: tuple[str, ...] = tuple(sorted(LANGUAGES))


@dataclass(frozen=True, slots=True)
class Options:
    backend: str = "auto"
    keep_blank_lines: bool = False
    backup: bool = False
    dry_run: bool = False
    diff: bool = False


@dataclass(frozen=True, slots=True)
class Task:
    path: Path
    language: str
    options: Options


@dataclass(frozen=True, slots=True)
class Result:
    path: Path
    language: str
    removed: int
    before: int
    after: int
    error: str = ""
    diff: str = ""


class TextSpans:
    @staticmethod
    def is_binary(data: bytes) -> bool:
        return NUL in data[:8192]

    @staticmethod
    def byte_offsets(text: str) -> list[int]:
        offsets: list[int] = [0] * (len(text) + 1)
        total = 0
        for index, char in enumerate(text):
            offsets[index] = total
            total += len(char.encode("utf-8", errors="surrogateescape"))
        offsets[len(text)] = total
        return offsets

    @staticmethod
    def char_index(offsets: Sequence[int], byte_offset: int) -> int:
        return min(bisect.bisect_left(offsets, byte_offset), len(offsets) - 1)

    @staticmethod
    def apply(text: str, spans: Sequence[tuple[int, int]]) -> tuple[str, int]:
        lines = text.splitlines(keepends=True)
        if not lines:
            return text, 0
        starts: list[int] = []
        position = 0
        for line in lines:
            starts.append(position)
            position += len(line)
        clipped: dict[int, list[tuple[int, int]]] = {}
        for start, end in spans:
            if start > position:
                continue
            first = max(bisect.bisect_right(starts, start) - 1, 0)
            last = min(bisect.bisect_right(starts, max(start, end - 1)) - 1, len(lines) - 1)
            for index in range(first, last + 1):
                line_start = starts[index]
                line_end = line_start + len(lines[index])
                local_start = max(start, line_start) - line_start
                local_end = min(end, line_end) - line_start
                if local_end > local_start:
                    clipped.setdefault(index, []).append((local_start, local_end))
        output: list[str] = []
        for index, line in enumerate(lines):
            if index not in clipped:
                output.append(line)
                continue
            body, newline = TextSpans.split_line(line)
            chunks: list[str] = []
            previous = 0
            for local_start, local_end in sorted(clipped[index]):
                chunks.append(body[previous:local_start])
                previous = local_end
            chunks.append(body[previous:])
            rebuilt = "".join(chunks)
            if not rebuilt.strip():
                continue
            output.append(rebuilt.rstrip(" \t") + newline)
        return "".join(output), len(spans)

    @staticmethod
    def split_line(line: str) -> tuple[str, str]:
        if line.endswith("\r\n"):
            return line[:-2], "\r\n"
        if line.endswith("\n"):
            return line[:-1], "\n"
        return line, ""

    @staticmethod
    def collapse(text: str) -> str:
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
        return text.lstrip("\n")


class RegexBackend:
    def spans(self, text: str, language: Language) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        length = len(text)
        index = 0
        if language.shebang and text.startswith("#!"):
            newline = text.find("\n")
            index = length if newline == -1 else newline + 1
        while index < length:
            block_end = self.marker_end(text, index, language.block)
            if block_end is not None:
                spans.append((index, block_end))
                index = block_end
                continue
            long_end = self.marker_end(text, index, language.long_quotes)
            if long_end is not None:
                index = long_end
                continue
            quote = next((item for item in language.quotes if text.startswith(item, index)), None)
            if quote is not None:
                index = self.string_end(text, index + len(quote), quote)
                continue
            if language.regex_literals and text[index] == "/":
                regex_end = self.regex_end(text, index)
                if regex_end is not None:
                    index = regex_end
                    continue
            marker = next((item for item in language.line if text.startswith(item, index)), None)
            if marker is not None:
                newline = text.find("\n", index)
                end = length if newline == -1 else newline
                spans.append((index, end))
                index = end
                continue
            index += 1
        return spans

    @staticmethod
    def marker_end(text: str, index: int, markers: Sequence[tuple[str, str]]) -> int | None:
        for opener, closer in markers:
            if text.startswith(opener, index):
                found = text.find(closer, index + len(opener))
                return len(text) if found == -1 else found + len(closer)
        return None

    @staticmethod
    def regex_end(text: str, index: int) -> int | None:
        if text.startswith(("//", "/*"), index):
            return None
        previous = index - 1
        while previous >= 0 and text[previous] in " \t":
            previous -= 1
        if previous >= 0:
            char = text[previous]
            if char.isalnum() or char in "_)]}":
                start = previous
                while start >= 0 and (text[start].isalnum() or text[start] == "_"):
                    start -= 1
                word = text[start + 1 : previous + 1]
                if word not in REGEX_PREFIX_WORDS:
                    return None
            elif char not in REGEX_PREFIX_CHARS:
                return None
        cursor = index + 1
        in_class = False
        length = len(text)
        while cursor < length:
            char = text[cursor]
            if char == "\\":
                cursor += 2
                continue
            if char == "[":
                in_class = True
            elif char == "]":
                in_class = False
            elif char == "/" and not in_class:
                return cursor + 1
            elif char == "\n":
                return None
            cursor += 1
        return None

    @staticmethod
    def string_end(text: str, index: int, quote: str) -> int:
        length = len(text)
        multiline = len(quote) > 1 or quote == "`"
        while index < length:
            char = text[index]
            if char == "\\":
                index += 2
                continue
            if text.startswith(quote, index):
                return index + len(quote)
            if char == "\n" and not multiline:
                return index
            index += 1
        return length


class TreeSitterBackend:
    availability: dict[str, bool] = {}
    parsers: dict[str, object] = {}

    @classmethod
    def available(cls, language: Language) -> bool:
        if language.name not in cls.availability:
            try:
                importlib.import_module("tree_sitter")
                importlib.import_module(language.module)
            except ImportError:
                cls.availability[language.name] = False
            else:
                cls.availability[language.name] = True
        return cls.availability[language.name]

    @classmethod
    def parser(cls, language: Language) -> object:
        if language.name not in cls.parsers:
            parser_module = importlib.import_module("tree_sitter")
            grammar = importlib.import_module(language.module)
            language_object = parser_module.Language(getattr(grammar, language.attribute)())
            cls.parsers[language.name] = parser_module.Parser(language_object)
        return cls.parsers[language.name]

    def spans(self, data: bytes, language: Language) -> list[tuple[int, int]]:
        tree = self.parser(language).parse(data)
        spans: list[tuple[int, int]] = []
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type in COMMENT_NODE_TYPES:
                spans.append((node.start_byte, node.end_byte))
                continue
            stack.extend(node.children)
        spans.sort()
        return spans


class FileFinder:
    @staticmethod
    def iter_files(paths: Sequence[Path]) -> Iterable[Path]:
        for path in paths:
            if path.is_file():
                yield path
                continue
            if not path.is_dir():
                continue
            for child in sorted(path.rglob("*")):
                if not child.is_file():
                    continue
                if any(part in SKIP_DIRS for part in child.parts):
                    continue
                if child.name.startswith(".") or child.suffix in SKIP_SUFFIXES:
                    continue
                yield child

    @staticmethod
    def language_for(path: Path, selected: Sequence[str]) -> Language | None:
        suffix = path.suffix.lower()
        for name in selected:
            language = LANGUAGES[name]
            if language.filenames and path.name in language.filenames:
                return language
            if suffix and suffix in language.extensions:
                return language
        return None

    @staticmethod
    def collect(paths: Sequence[Path], selected: Sequence[str]) -> list[tuple[Path, Language]]:
        found: list[tuple[Path, Language]] = []
        for path in FileFinder.iter_files(paths):
            language = FileFinder.language_for(path, selected)
            if language is not None:
                found.append((path, language))
        return found


class CommentStripper:
    def __init__(self, options: Options) -> None:
        self.options = options
        self.regex = RegexBackend()
        self.tree_sitter = TreeSitterBackend()

    def backend_for(self, language: Language) -> str:
        if self.options.backend == "regex":
            return "regex"
        if self.options.backend == "tree-sitter":
            return "tree-sitter"
        return "tree-sitter" if TreeSitterBackend.available(language) else "regex"

    def spans(self, text: str, data: bytes, language: Language) -> list[tuple[int, int]]:
        if self.backend_for(language) == "regex":
            return self.regex.spans(text, language)
        offsets = TextSpans.byte_offsets(text)
        raw = self.tree_sitter.spans(data, language)
        return [(TextSpans.char_index(offsets, start), TextSpans.char_index(offsets, end)) for start, end in raw]

    def strip(self, data: bytes, language: Language) -> tuple[bytes, int, str]:
        if TextSpans.is_binary(data):
            return data, 0, "binary"
        text = data.decode("utf-8", errors="surrogateescape")
        spans = self.spans(text, data, language)
        if not spans:
            return data, 0, ""
        cleaned, removed = TextSpans.apply(text, spans)
        if not self.options.keep_blank_lines:
            cleaned = TextSpans.collapse(cleaned)
        return cleaned.encode("utf-8", errors="surrogateescape"), removed, ""


class FileStripper:
    @staticmethod
    def run(task: Task) -> Result:
        language = LANGUAGES[task.language]
        try:
            data = task.path.read_bytes()
        except OSError as error:
            return Result(task.path, task.language, 0, 0, 0, error=str(error))
        cleaned, removed, error = CommentStripper(task.options).strip(data, language)
        if error:
            return Result(task.path, task.language, 0, len(data), len(data), error=error)
        if not removed or cleaned == data:
            return Result(task.path, task.language, 0, len(data), len(data))
        diff = ""
        if task.options.diff or task.options.dry_run:
            diff = "".join(
                difflib.unified_diff(
                    data.decode("utf-8", errors="replace").splitlines(keepends=True),
                    cleaned.decode("utf-8", errors="replace").splitlines(keepends=True),
                    str(task.path),
                    str(task.path),
                )
            )
        if not task.options.dry_run:
            if task.options.backup:
                shutil.copy2(task.path, task.path.with_suffix(task.path.suffix + ".bak"))
            task.path.write_bytes(cleaned)
        return Result(task.path, task.language, removed, len(data), len(cleaned), diff=diff)


class Stripper:
    def __init__(self, argv: Sequence[str] | None = None) -> None:
        self.parser = self.build_parser()
        self.args = self.parser.parse_args(list(argv) if argv is not None else None)

    @staticmethod
    def build_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="stripper",
            description="Strip comments from source files in any supported language.",
            epilog="python sources are handled by cleanpy.py, not by this tool",
        )
        parser.add_argument("paths", nargs="*", type=Path, default=[], help="files or directories (default: .)")
        parser.add_argument("-a", "--all", action="store_true", help="autodetect languages by extension")
        group = parser.add_argument_group("languages")
        for name in LANGUAGE_NAMES:
            language = LANGUAGES[name]
            flags = [f"--{name}", *[f"--{alias}" for alias, target in ALIASES.items() if target == name]]
            group.add_argument(*flags, action="store_true", dest=name, help=" ".join(language.extensions or language.filenames))
        parser.add_argument("-b", "--backend", choices=("auto", "tree-sitter", "regex"), default="auto")
        parser.add_argument("-j", "--workers", type=int, default=DEFAULT_WORKERS)
        parser.add_argument("-n", "--dry-run", action="store_true", help="report only and show the diff")
        parser.add_argument("-d", "--diff", action="store_true", help="show a unified diff")
        parser.add_argument("--backup", action="store_true", help="write <file>.bak before overwriting")
        parser.add_argument("-q", "--quiet", action="store_true")
        parser.add_argument("--keep-blank-lines", action="store_true", help="do not collapse repeated blank lines")
        parser.add_argument("--list-languages", action="store_true", help="print the language table and exit")
        return parser

    def selected(self) -> list[str]:
        if self.args.all:
            return list(LANGUAGE_NAMES)
        return [name for name in LANGUAGE_NAMES if getattr(self.args, name)]

    def run(self) -> int:
        if self.args.list_languages:
            return self.print_languages()
        selected = self.selected()
        if not selected:
            self.parser.print_usage(sys.stderr)
            print("stripper: pick a language flag or -a/--all (see --list-languages)", file=sys.stderr)
            return 2
        options = Options(
            backend=self.args.backend,
            keep_blank_lines=self.args.keep_blank_lines,
            backup=self.args.backup,
            dry_run=self.args.dry_run,
            diff=self.args.diff,
        )
        targets = FileFinder.collect(self.args.paths or [Path(".")], selected)
        if not targets:
            print("stripper: no matching files", file=sys.stderr)
            return 0
        results = self.execute([Task(path, language.name, options) for path, language in targets])
        return self.report(results)

    def execute(self, tasks: list[Task]) -> list[Result]:
        workers = max(1, min(self.args.workers, len(tasks)))
        if workers == 1:
            return [FileStripper.run(task) for task in tasks]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(FileStripper.run, tasks))

    def report(self, results: Sequence[Result]) -> int:
        changed = [item for item in results if item.removed]
        failures = [item for item in results if item.error and item.error != "binary"]
        removed = sum(item.removed for item in changed)
        saved = sum(max(0, item.before - item.after) for item in changed)
        for item in changed:
            if not self.args.quiet:
                print(f"{item.path}  [{item.language}]  -{item.removed} comments  -{item.before - item.after} B")
            if item.diff and not self.args.quiet:
                sys.stdout.write(item.diff)
        if not self.args.quiet:
            mode = "would strip" if self.args.dry_run else "stripped"
            print(f"\n{mode} {len(changed)}/{len(results)} files, {removed} comments, {saved} bytes")
        for item in failures:
            print(f"stripper: {item.path}: {item.error}", file=sys.stderr)
        return 1 if failures else 0

    def print_languages(self) -> int:
        print(f"{'flag':16}{'extensions':54}{'markers':26}backend")
        for name in LANGUAGE_NAMES:
            language = LANGUAGES[name]
            extensions = " ".join(language.extensions or language.filenames)
            markers = " ".join(language.line) + " " + " ".join(f"{start}..{end}" for start, end in language.block)
            backend = "tree-sitter" if TreeSitterBackend.available(language) else "regex"
            print(f"{'--' + name:16}{extensions:54}{markers.strip():26}{backend}")
        return 0

    @staticmethod
    def main(argv: Sequence[str] | None = None) -> int:
        return Stripper(argv).run()


if __name__ == "__main__":
    raise SystemExit(Stripper.main())
