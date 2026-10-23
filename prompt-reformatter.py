#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import typing as t
from pathlib import Path
import re

SKIP_DIRS: t.set[str] = {".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "lazy"}
_BEFORE_MARKER = re.compile("[ \\t\\r\\n]+(?=(?:\\d+[.)]|\\(\\d+\\)|[-*\\u2022]|\\*\\*[^*\\n]+\\*\\*)\\s)")
_COMMA_SPLIT = re.compile(",[ \\t]+")
_SEMICOLON_SPLIT = re.compile(";[ \\t]+")
MAX_LINE: int = 80
_STRING_HEAD = re.compile("^([rRbBuUfF]*)(\"\"\"|\\'\\'\\'|\"|\\')")


class PromptReformatter:
    @staticmethod
    def reformat(text: str) -> str:
        if not text.strip():
            return text
        lead_match = re.match("^([ \\t]*(?:\\r?\\n[ \\t]*)*)", text)
        leading = lead_match.group(1) if lead_match else ""
        trail_match = re.search("([ \\t]*(?:\\r?\\n[ \\t]*)*)$", text)
        trailing = trail_match.group(1) if trail_match else ""
        indent = leading.rsplit("\n", 1)[-1] if "\n" in leading else ""
        core_end = len(text) - len(trailing) if trailing else len(text)
        core = text[len(leading) : core_end]
        core = _BEFORE_MARKER.sub("\n" + indent, core)
        core = _COMMA_SPLIT.sub(",\n" + indent, core)
        if MAX_LINE > 0:
            rebuilt = []
            for line in core.split("\n"):
                if len(line) > MAX_LINE and ";" in line:
                    line = _SEMICOLON_SPLIT.sub(";\n" + indent, line)
                rebuilt.append(line)
            core = "\n".join(rebuilt)
        return leading + core + trailing

    @staticmethod
    def process_file(path: Path) -> bool:
        try:
            source = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        code = PromptReformatter.reformat(source)
        path.write_text(code, encoding="utf-8")
        return True

    @staticmethod
    def iter_files(root: Path) -> t.Iterator[t.Any]:
        for path in sorted(root.rglob("*.txt")):
            rel = path.relative_to(root)
            if any((part in SKIP_DIRS for part in rel.parts)):
                continue
            if path.is_file():
                yield path

    @staticmethod
    def main(root: Path | None = None) -> int:
        root = (root or Path.cwd()).resolve()
        changed = 0
        for path in PromptReformatter.iter_files(root):
            if PromptReformatter.process_file(path):
                print(f"formatted: {path.name}")
                changed += 1
        print(f"{changed} file(s) updated")
        return 0


if __name__ == "__main__":
    raise SystemExit(PromptReformatter.main())
