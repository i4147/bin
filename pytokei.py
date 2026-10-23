#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations
import argparse
from pathlib import Path
import re
from typing import Any, Sequence

LANGUAGE_EXTENSIONS: dict[str, list[str]] = {
    "python": [".py", ".pyi"],
    "javascript": [".js"],
    "java": [".java"],
    "c": [".c"],
    "cpp": [".cpp", ".h"],
    "html": [".html"],
    "css": [".css"],
    "ruby": [".rb"],
    "php": [".php"],
    "bash": [".sh", ".bash"],
}
COMMENT_PATTERNS: dict[str, str] = {
    "python": "^\\s*#",
    "javascript": "^\\s*//",
    "java": "^\\s*//",
    "c": "^\\s*//",
    "cpp": "^\\s*//",
    "html": "^\\s*<!--",
    "css": "^\\s*/\\*",
    "ruby": "^\\s*#",
    "php": "^\\s*//",
}
SHEBANG_PATTERNS: dict[str, list[str]] = {
    "python": ["#!/usr/bin/env python", "#!/usr/bin/python3", "#!/bin/python3"],
    "bash": ["#!/bin/bash"],
    "ruby": ["#!/usr/bin/ruby", "#!/bin/ruby"],
    "perl": ["#!/usr/bin/perl"],
    "node": ["#!/usr/bin/node", "#!/bin/node"],
    "sh": ["#!/bin/sh"],
}
DEFAULT_EXCLUDES: list[str] = [".git"]


class Pytokei:
    @staticmethod
    def is_binary(path: Path) -> bool:
        try:
            with path.open("rb") as f:
                chunk = f.read(8192)
            return b"\x00" in chunk
        except OSError:
            return False

    @staticmethod
    def _contains_exclude(path: Path, excludes: set[str]) -> bool:
        s = str(path)
        return any((ex in s for ex in excludes))

    @staticmethod
    def detect_shebang(path: Path, excludes: set[str]) -> str | None:
        if Pytokei.is_binary(path):
            print(f"{path} is binary")
            return None
        if Pytokei._contains_exclude(path, excludes):
            return None
        try:
            with path.open(encoding="utf-8") as f:
                first = f.readline().strip()
            for lang, shebangs in SHEBANG_PATTERNS.items():
                for shebang in shebangs:
                    if first.startswith(shebang):
                        return lang
        except Exception as exc:
            print(f"Error reading file {path}:{exc}")

    @staticmethod
    def count_file(path: Path, language: str, excludes: set[str]) -> tuple[int, int, int]:
        if Pytokei._contains_exclude(path, excludes):
            return (0, 0, 0)
        if Pytokei.is_binary(path):
            print(f"{path} is binary")
            return (0, 0, 0)
        code = comments = blank = 0
        pattern = COMMENT_PATTERNS.get(language, "")
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        blank += 1
                    elif re.match(pattern, line):
                        comments += 1
                    else:
                        code += 1
        except Exception as exc:
            print(f"Error reading file {path}:{exc}")
            return (0, 0, 0)
        return (code, comments, blank)

    @staticmethod
    def _add(stats: dict[str, Any], language: str, code: int, comments: int, blank: int) -> None:
        stats["languages"][language]["code"] += code
        stats["languages"][language]["comments"] += comments
        stats["languages"][language]["blank"] += blank
        stats["total"]["code"] += code
        stats["total"]["comments"] += comments
        stats["total"]["blank"] += blank

    @staticmethod
    def analyze(root: Path, excludes: set[str]) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "total": {"code": 0, "comments": 0, "blank": 0},
            "languages": {lang: {"code": 0, "comments": 0, "blank": 0} for lang in LANGUAGE_EXTENSIONS},
        }
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if not suffix:
                lang = Pytokei.detect_shebang(path, excludes)
                if lang:
                    c, m, b = Pytokei.count_file(path, lang, excludes)
                    Pytokei._add(stats, lang, c, m, b)
                    continue
            for lang, exts in LANGUAGE_EXTENSIONS.items():
                if suffix in exts:
                    c, m, b = Pytokei.count_file(path, lang, excludes)
                    Pytokei._add(stats, lang, c, m, b)
                    break
        return stats

    @staticmethod
    def print_report(stats: dict[str, Any]) -> None:
        print(f"Total lines of code:{stats['total']['code']}")
        print(f"Total comment lines:{stats['total']['comments']}")
        print(f"Total blank lines:{stats['total']['blank']}\n")
        print("Language-based statistics:")
        for lang, counts in stats["languages"].items():
            if counts["code"] > 0:
                print(f"\n{lang.capitalize()}:")
                print(f"  Code lines:{counts['code']}")
                print(f"  Comment lines:{counts['comments']}")
                print(f"  Blank lines:{counts['blank']}")

    @staticmethod
    def build_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="pytokei_merged.py",
            description="Count lines of code, comments, and blanks by language.",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=__doc__,
        )
        parser.add_argument(
            "root", nargs="?", default=".", help="Root directory to analyze. Default: current directory."
        )
        parser.add_argument(
            "--no-report",
            action="store_true",
            help="Suppress final statistics report (pytokei2.py behavior).",
        )
        parser.add_argument(
            "--exclude",
            action="append",
            default=DEFAULT_EXCLUDES,
            help="Substrings to exclude from paths. Can be repeated. Default: .git",
        )
        return parser

    @staticmethod
    def main(argv: Sequence[str] | None = None) -> int:
        parser = Pytokei.build_parser()
        args = parser.parse_args(argv)
        root = Path(args.root)
        if not root.is_dir():
            print(f"Error: {root} is not a directory")
            return 1
        excludes = set(args.exclude)
        stats = Pytokei.analyze(root, excludes)
        if not args.no_report:
            Pytokei.print_report(stats)
        return 0


if __name__ == "__main__":
    raise SystemExit(Pytokei.main())
