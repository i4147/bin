from __future__ import annotations

import ast
import difflib
import io
import tokenize
from dataclasses import dataclass

from .models import TextEdit

DIRECTIVE_PREFIXES = (
    "#!",
    "# type:",
    "# fmt:",
    "# noqa",
    "# pylint:",
    "# isort:",
    "# coding:",
    "# cython:",
    "# pyright:",
    "# mypy:",
)
DIRECTIVE_MARKERS = ("coding", "-*-", "fmt: off", "fmt: on")
TYPE_COMMENT_PREFIX = "# type:"


@dataclass(frozen=True, slots=True)
class LineIndex:
    starts: tuple[int, ...]
    text: str

    @classmethod
    def build(cls, text: str) -> "LineIndex":
        starts = [0]
        for number, char in enumerate(text):
            if char == "\n":
                starts.append(number + 1)
        return cls(tuple(starts), text)

    def offset(self, line: int, column: int) -> int:
        if line <= 0:
            return 0
        if line > len(self.starts):
            return len(self.text)
        return min(self.starts[line - 1] + column, len(self.text))

    def line_start(self, line: int) -> int:
        return self.offset(line, 0)

    def line_end(self, line: int) -> int:
        if line >= len(self.starts):
            return len(self.text)
        return self.starts[line]

    def line_of(self, offset: int) -> int:
        low, high = 0, len(self.starts)
        while low < high:
            middle = (low + high) // 2
            if self.starts[middle] <= offset:
                low = middle + 1
            else:
                high = middle
        return max(1, low)

    def only_blanks_before(self, offset: int) -> bool:
        return not self.text[self.line_start(self.line_of(offset)) : offset].strip()


def is_directive(comment: str) -> bool:
    stripped = comment.strip().lower()
    if stripped.startswith(DIRECTIVE_PREFIXES):
        return True
    return any(marker in stripped for marker in DIRECTIVE_MARKERS)


def is_type_comment(comment: str) -> bool:
    return comment.strip().lower().startswith(TYPE_COMMENT_PREFIX)


def iter_comment_tokens(source: str) -> list[tuple[int, int, int, int, str]]:
    lines = source.splitlines()
    found: list[tuple[int, int, int, int, str]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            (start_line, start_col), (end_line, end_col) = token.start, token.end
            if start_line <= 0:
                continue
            text = lines[start_line - 1] if start_line - 1 < len(lines) else ""
            found.append((start_line, start_col, end_line, min(end_col, len(text)), token.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return found
    return found


def comment_edit(index: LineIndex, start: int, end: int) -> TextEdit:
    if index.only_blanks_before(start) and not index.text[end : index.line_end(index.line_of(end))].strip():
        return TextEdit(index.line_start(index.line_of(start)), index.line_end(index.line_of(end)))
    while start > 0 and index.text[start - 1] in " \t":
        start -= 1
    return TextEdit(start, end)


def removal_edit(index: LineIndex, start: int, end: int, keep_body: bool) -> TextEdit:
    if keep_body:
        return TextEdit(start, end, "pass")
    if index.only_blanks_before(start) and not index.text[end : index.line_end(index.line_of(end))].strip():
        return TextEdit(index.line_start(index.line_of(start)), index.line_end(index.line_of(end)))
    return TextEdit(start, end)


def colon_before(index: LineIndex, offset: int) -> int:
    probe = offset - 1
    while probe >= 0 and index.text[probe] in " \t\n\\":
        probe -= 1
    if probe < 0 or index.text[probe] != ":":
        return -1
    while probe > 0 and index.text[probe - 1] in " \t":
        probe -= 1
    return probe


def arrow_before(index: LineIndex, offset: int) -> int:
    probe = offset - 1
    while probe >= 0 and index.text[probe] in " \t\n\\":
        probe -= 1
    if probe >= 1 and index.text[probe - 1 : probe + 1] == "->":
        start = probe - 1
        while start > 0 and index.text[start - 1] in " \t":
            start -= 1
        return start
    return -1


def apply_edits(source: str, edits: list[TextEdit]) -> str:
    result = source
    limit = len(source)
    for edit in sorted(edits, key=lambda item: item.start, reverse=True):
        start = max(0, edit.start)
        end = min(edit.end, len(source))
        if end < start or end > limit:
            continue
        result = result[:start] + edit.replacement + result[end:]
        limit = start
    return result


def collapse_blank_lines(source: str, keep: int = 2) -> str:
    out: list[str] = []
    blank = 0
    for line in source.splitlines():
        if line.strip():
            blank = 0
            out.append(line)
            continue
        blank += 1
        if blank <= keep:
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n" if out else ""


def strip_trailing_space(source: str) -> str:
    body = "\n".join(line.rstrip() for line in source.splitlines())
    return body + ("\n" if source.endswith("\n") else "")


def unified_diff(before: str, after: str, name: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
        )
    )


def byte_to_char(text: str) -> list[int]:
    mapping: list[int] = []
    for position, char in enumerate(text):
        mapping.extend([position] * len(char.encode("utf-8", "surrogatepass")))
    mapping.append(len(text))
    return mapping


def normalize_newlines(source: str) -> str:
    return source.replace("\r\n", "\n").replace("\r", "\n")


def iter_docstrings(tree: ast.AST) -> list[tuple[ast.stmt, list[ast.stmt]]]:
    scopes: list[list[ast.stmt]] = [getattr(tree, "body", [])]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scopes.append(node.body)
    found: list[tuple[ast.stmt, list[ast.stmt]]] = []
    for body in scopes:
        if body and isinstance(body[0], ast.Expr):
            value = body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.append((body[0], body))
    return found
