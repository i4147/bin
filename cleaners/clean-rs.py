#!/data/data/com.termux/files/usr/bin/python

from __future__ import annotations

import argparse
import difflib
import os
import shutil
import sys
from multiprocessing import Pool
from pathlib import Path
from typing import NamedTuple

from loguru import logger
from tree_sitter import Language, Node, Parser
import tree_sitter_rust


# tree-sitter-rust emits line_comment / block_comment, not "comment".
# Accept all spellings so this works across grammar versions.
_COMMENT_TYPES = frozenset({"comment", "line_comment", "block_comment"})

_PARSER: Parser | None = None


def get_parser() -> Parser:
    global _PARSER
    if _PARSER is None:
        _PARSER = Parser(Language(tree_sitter_rust.language()))
    return _PARSER


# ---------- comment classification ----------------------------------------


def is_doc_comment(text: bytes) -> bool:
    """True for ///, //!, /** … */ and /*! … */.

    Explicitly false for ////, /***/ and /**/ (Rust treats those as
    ordinary comments, per the reference).
    """
    if text.startswith(b"///"):
        return len(text) < 4 or text[3:4] != b"/"
    if text.startswith(b"//!"):
        return True
    if text.startswith(b"/**"):
        return len(text) < 4 or text[3:4] not in (b"/", b"*")
    if text.startswith(b"/*!"):
        return True
    return False


def collect_comment_ranges(root: Node, content: bytes) -> list[tuple[int, int]]:
    """Byte ranges of non-doc comments, grown to swallow adjacent blanks."""
    ranges: list[tuple[int, int]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in _COMMENT_TYPES:
            text = content[node.start_byte : node.end_byte].lstrip()
            if is_doc_comment(text):
                continue
            start, end = node.start_byte, node.end_byte
            # swallow spaces/tabs immediately before the comment on the same line
            while start > 0 and content[start - 1 : start] in (b" ", b"\t"):
                start -= 1
            # if the rest of the line is empty, swallow the newline too
            nl = content.find(b"\n", end)
            if nl >= 0:
                if content[end:nl].strip(b" \t\r") == b"":
                    end = nl + 1
            elif content[end:].strip(b" \t\r") == b"":
                end = len(content)
            ranges.append((start, end))
            continue
        for child in reversed(node.children):
            stack.append(child)
    # merge adjacent/overlapping ranges (can happen after extension)
    ranges.sort()
    merged: list[tuple[int, int]] = []
    for s, e in ranges:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def strip_comments(content: bytes) -> tuple[bytes, int, bool]:
    """Return (stripped, removed_count, original_had_syntax_error)."""
    tree = get_parser().parse(content)
    ranges = collect_comment_ranges(tree.root_node, content)
    if not ranges:
        return content, 0, tree.root_node.has_error
    out = bytearray()
    last = 0
    for s, e in ranges:
        out.extend(content[last:s])
        last = e
    out.extend(content[last:])
    return bytes(out), len(ranges), tree.root_node.has_error


# ---------- whitespace tidy ------------------------------------------------


def remove_blank_lines(content: bytes) -> bytes:
    """Drop lines that are entirely whitespace. Never touches other lines."""
    return b"".join(line for line in content.splitlines(keepends=True) if line.strip(b" \t\r\n"))


def validate_rust(content: bytes) -> bool:
    return not get_parser().parse(content).root_node.has_error


# ---------- per-file worker ------------------------------------------------


class FileResult(NamedTuple):
    name: str
    reduced: int
    count: int
    changed: bool
    error: str
    old_text: str
    new_text: str


def process_file(task: tuple[str, bool, bool]) -> FileResult:
    path_str, dry_run, backup = task
    path = Path(path_str)
    name = path.name
    try:
        content = path.read_bytes()
    except OSError as exc:
        return FileResult(name, 0, 0, False, str(exc), "", "")

    orig_size = len(content)
    try:
        new_content, count, orig_has_error = strip_comments(content)
        if count > 0:
            new_content = remove_blank_lines(new_content)
            # validate the exact bytes we intend to write
            if not orig_has_error and not validate_rust(new_content):
                return FileResult(
                    name,
                    0,
                    count,
                    False,
                    "stripping comments introduced syntax errors",
                    "",
                    "",
                )
    except Exception as exc:  # noqa: BLE001 — one bad file must not kill the run
        return FileResult(name, 0, 0, False, f"{type(exc).__name__}: {exc}", "", "")

    if new_content == content:
        return FileResult(name, 0, count, False, "", "", "")

    reduced = orig_size - len(new_content)
    old_text = content.decode("utf-8", errors="replace")
    new_text = new_content.decode("utf-8", errors="replace")
    if not dry_run:
        try:
            if backup:
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            path.write_bytes(new_content)
        except OSError as exc:
            return FileResult(name, 0, count, False, str(exc), old_text, new_text)
    return FileResult(name, reduced, count, True, "", old_text, new_text)


# ---------- traversal ------------------------------------------------------


def iter_rust_files(paths):
    seen: set[Path] = set()
    for p in paths:
        if p.is_symlink():
            logger.warning(f"Skipping symlink: {p}")
            continue
        if p.is_file():
            if p.suffix.lower() != ".rs" or p.name.startswith("."):
                logger.warning(f"Skipping non-Rust file: {p}")
                continue
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                yield p
        elif p.is_dir():
            for dirpath, dirnames, filenames in os.walk(p, followlinks=False):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for fname in sorted(filenames):
                    if fname.startswith(".") or not fname.lower().endswith(".rs"):
                        continue
                    fp = Path(dirpath) / fname
                    if fp.is_symlink():
                        continue
                    rp = fp.resolve()
                    if rp in seen:
                        continue
                    seen.add(rp)
                    yield fp
        else:
            logger.warning(f"Path not found: {p}")


# ---------- output ---------------------------------------------------------


def _color(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def _truncate(s: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(s) <= width:
        return s
    return s[: width - 1] + "…" if width > 1 else "…"


def format_result(index: int, total: int, r: FileResult, term_width: int) -> str:
    idx_w = len(str(total))
    prefix = f"[{index:>{idx_w}}]  "
    bytes_w = 9  # len("unchanged")
    name_w = max(10, term_width - len(prefix) - 2 - bytes_w)
    name = _truncate(r.name, name_w).ljust(name_w)
    if r.changed:
        val = _color("32", str(r.reduced))  # green
    else:
        val = _color("2", "unchanged")  # dim
    return f"{prefix}{name}  {val}"


def print_diff(old: str, new: str, name: str) -> None:
    use_color = sys.stdout.isatty()
    for line in difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{name}",
        tofile=f"b/{name}",
    ):
        if use_color:
            if line.startswith("+") and not line.startswith("+++"):
                sys.stdout.write(f"\033[32m{line}\033[0m")
                continue
            if line.startswith("-") and not line.startswith("---"):
                sys.stdout.write(f"\033[31m{line}\033[0m")
                continue
            if line.startswith("@@"):
                sys.stdout.write(f"\033[36m{line}\033[0m")
                continue
        sys.stdout.write(line)


# ---------- CLI ------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Strip non-doc comments from Rust sources.")
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("-n", "--dry-run", action="store_true", help="Report changes (implies --diff), do not write.")
    ap.add_argument("-d", "--diff", action="store_true", help="Show unified diff.")
    ap.add_argument("-b", "--backup", action="store_true", help="Write <file>.bak before overwriting.")
    ap.add_argument("-w", "--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("-v", "--verbose", action="count", default=0)
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    level = "ERROR" if args.quiet else "DEBUG" if args.verbose >= 2 else "INFO" if args.verbose else "WARNING"
    logger.remove()
    logger.add(sys.stderr, level=level)

    show_diff = args.diff or args.dry_run
    inputs = list(args.paths) if args.paths else [Path()]
    files = list(iter_rust_files(inputs))
    if not files:
        logger.error("No Rust files to process.")
        return 1

    term_width = shutil.get_terminal_size((100, 24)).columns
    total = len(files)
    workers = max(1, args.workers)
    tasks = [(str(p), args.dry_run, args.backup) for p in files]

    scanned = changed = skipped = failed = total_reduced = 0

    def emit(i: int, r: FileResult) -> None:
        nonlocal scanned, changed, skipped, failed, total_reduced
        scanned += 1
        if r.error:
            failed += 1
            logger.error(f"{r.name}: {r.error}")
            return
        if r.changed:
            changed += 1
            total_reduced += r.reduced
        else:
            skipped += 1
        if not args.quiet:
            print(format_result(i, total, r, term_width))
            if show_diff and r.changed:
                print_diff(r.old_text, r.new_text, r.name)

    if total == 1 or workers == 1:
        for i, task in enumerate(tasks, 1):
            emit(i, process_file(task))
    else:
        with Pool(processes=workers) as pool:
            for i, result in enumerate(pool.imap(process_file, tasks), 1):
                emit(i, result)

    if not args.quiet:
        print(
            f"\nScanned {scanned} file(s): {changed} changed, "
            f"{skipped} unchanged, {failed} failed; {total_reduced} bytes freed.",
            file=sys.stderr,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
