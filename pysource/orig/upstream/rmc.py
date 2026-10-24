#!/data/data/com.termux/files/usr/bin/python
from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import contextlib
import functools
import hashlib
import json
import keyword
import os
import re
import shutil
import sys
import tempfile
import textwrap
from math import ceil
from dataclasses import dataclass
from typing import Any, Iterator, NamedTuple
from pathlib import Path
from multiprocessing import Pool
from loguru import logger
from dh import SKIP_DIRS

WORKERS = 4
CACHE_FILE = ".cache.json"


@dataclass(frozen=True)
class Opts:
    backend: str = "libcst"
    strip_full: bool = False
    strip_docs: bool = False
    all: bool = False
    mp_method: str = "ProcessPoolExecutor"
    workers: int = 4


class Result(NamedTuple):
    path: str
    delta: int
    err: str | None


_DIRECTIVE = re.compile(
    r"(?:^|#)\s*(?:noqa\b|type\s*:|pylint:|pyright:|pyre-|pytype:|mypy:|ruff:|flake8:|"
    r"isort:|fmt:|yapf:|black:|autopep8:|pragma\b|nosec\b|nocover\b|skipcq|noinspection|"
    r"coding[:=]|-\*-|vim?:|ex:|region\b|endregion\b|cython:|lint:|ty:)",
    re.I,
)
_PUNCT = re.compile(r"[=()\[\]{}:]")
_WORD = re.compile(r"\w+")
_COMMENT_LINE = re.compile(rb"^[ \t]*#")
_HASH_PREFIX = re.compile(r"^[ \t]*#[ ]?")
_SEMI = re.compile(rb"[ \t]*;[ \t]*")

_CACHE_MEM: dict[str, str] = {}


def _triv_expr(e: ast.expr) -> bool:
    if isinstance(e, (ast.Name, ast.Constant, ast.Attribute)):
        return True
    if isinstance(e, (ast.Tuple, ast.List)):
        return all(_triv_expr(x) for x in e.elts)
    if isinstance(e, ast.UnaryOp):
        return _triv_expr(e.operand)
    if isinstance(e, ast.BoolOp):
        return all(_triv_expr(x) for x in e.values)
    return False


def _trivial(s: ast.stmt) -> bool:
    if isinstance(s, ast.Expr):
        return _triv_expr(s.value)
    if isinstance(s, ast.AnnAssign):
        return s.value is None
    return False


def _candidates(body: str, first: str) -> Iterator[str]:
    yield body
    if body.startswith("@"):
        yield body + "\ndef _(): pass"
        return
    if body.endswith(":"):
        pad = body + "\n    pass"
        yield pad
        if first in ("else", "elif"):
            yield "if 1:\n    pass\n" + pad
        elif first in ("except", "finally"):
            yield "try:\n    pass\n" + pad
        elif first == "try":
            yield pad + "\nfinally:\n    pass"


@functools.lru_cache(maxsize=1024)
def _is_code(body: str) -> bool:
    if not body:
        return False
    m = _WORD.match(body)
    first = m.group() if m else ""
    if not (_PUNCT.search(body) or body[0] == "@" or keyword.iskeyword(first)):
        return False
    for cand in _candidates(body, first):
        try:
            tree = ast.parse(cand)
        except Exception:
            continue
        return any(not _trivial(s) for s in tree.body)
    return False


def _keep_text(text: str) -> bool:
    return bool(_DIRECTIVE.search(text)) or _is_code(text.lstrip("#").strip())


def _keep(text: str, row: int, o: Opts, blocks: frozenset[int] | set[int]) -> bool:
    if o.all:
        return False
    if row == 0 and text.startswith("#!"):
        return True
    if row in blocks:
        return True
    return _keep_text(text)


def _block_code_rows(lines: list[bytes]) -> set[int]:
    rows: set[int] = set()
    n, i = len(lines), 0
    match = _COMMENT_LINE.match
    while i < n:
        if match(lines[i]):
            j = i
            while j + 1 < n and match(lines[j + 1]):
                j += 1
            if j > i:
                block = "\n".join(
                    _HASH_PREFIX.sub("", lines[k].decode("utf-8", "replace").rstrip("\r")) for k in range(i, j + 1)
                )
                if _is_code(textwrap.dedent(block).strip("\n")):
                    rows.update(range(i, j + 1))
            i = j + 1
        else:
            i += 1
    return rows


def _apply(src: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    if not edits:
        return src
    edits.sort(key=lambda e: (e[0], e[1]))
    out: list[bytes] = []
    pos = 0
    for s, e, r in edits:
        if s < pos:
            continue
        out.append(src[pos:s])
        out.append(r)
        pos = e
    out.append(src[pos:])
    return b"".join(out)


_PARSER: Any = None
_STR = {"string", "template_string"}
_STOP = _STR | {"comment"}
_DEFS = {"function_definition", "class_definition"}


def _make_parser() -> Any:
    import tree_sitter_python as tsp
    from tree_sitter import Language, Parser

    lang = Language(tsp.language())
    return Parser(lang)


def _plain(src: bytes, pos: int) -> bool:
    head = src[pos : pos + 4].lower()
    i = 0
    while i < len(head) and head[i : i + 1] not in (b'"', b"'"):
        i += 1
    pre = head[:i]
    return b"f" not in pre and b"b" not in pre and b"t" not in pre


def _doc_edit(node: Any, src: bytes, edits: list[tuple[int, int, bytes]], is_module: bool) -> None:
    first = None
    others = False
    for i in range(node.named_child_count):
        ch = node.named_child(i)
        if ch.type == "comment":
            continue
        if first is None:
            first = ch
        else:
            others = True
            break
    if first is None or first.type != "expression_statement" or first.named_child_count != 1:
        return
    s = first.named_child(0)
    if s.type not in ("string", "concatenated_string") or not _plain(src, s.start_byte):
        return
    sb, eb = first.start_byte, first.end_byte
    if not others and not is_module:
        edits.append((sb, eb, b"pass"))
        return
    ls = sb - first.start_point[1]
    nl = src.find(b"\n", eb)
    le = len(src) if nl < 0 else nl
    if not src[ls:sb].strip() and not src[eb:le].strip():
        edits.append((ls, min(le + 1, len(src)), b""))
    else:
        m = _SEMI.match(src, eb)
        edits.append((sb, m.end() if m else eb, b""))


def _strip_ts(src: bytes, o: Opts) -> bytes:
    global _PARSER
    if _PARSER is None:
        _PARSER = _make_parser()
    root = _PARSER.parse(src).root_node
    if root.has_error:
        raise ValueError("syntax error (tree-sitter)")

    lines = src.split(b"\n")
    blocks = _block_code_rows(lines) if (o.strip_full and not o.all) else set()
    edits: list[tuple[int, int, bytes]] = []
    prot: set[int] = set()
    full, docs = o.strip_full or o.all, o.strip_docs or o.all

    cur = root.walk()
    done = False
    while not done:
        node = cur.node
        t = node.type
        if t == "comment":
            sb, eb = node.start_byte, node.end_byte
            row, col = node.start_point
            ls = sb - col
            pre = src[ls:sb]
            inline = bool(pre.strip())
            if inline or full:
                if src[eb - 1 : eb] == b"\r":
                    eb -= 1
                text = src[sb:eb].decode("utf-8", "replace")
                if not _keep(text, row, o, blocks):
                    if inline:
                        edits.append((sb - (len(pre) - len(pre.rstrip())), eb, b""))
                    else:
                        end = eb
                        if src[end : end + 1] == b"\r":
                            end += 1
                        if src[end : end + 1] == b"\n":
                            end += 1
                        edits.append((ls, end, b""))
        elif t in _STR:
            sr, er = node.start_point[0], node.end_point[0]
            if er > sr:
                prot.update(range(sr + 1, er + 1))
        elif docs:
            if t in _DEFS:
                body = node.child_by_field_name("body")
                if body is not None:
                    _doc_edit(body, src, edits, False)
            elif t == "module" and o.all:
                _doc_edit(node, src, edits, True)

        if t not in _STOP and cur.goto_first_child():
            continue
        while True:
            if cur.goto_next_sibling():
                break
            if not cur.goto_parent():
                done = True
                break

    pos, n, total = 0, len(lines), len(src)
    for i, ln in enumerate(lines):
        ln_len = len(ln)
        if i not in prot and not ln.strip() and not (i == n - 1 and ln_len == 0):
            edits.append((pos, min(pos + ln_len + 1, total), b""))
        pos += ln_len + 1

    return _apply(src, edits)


@functools.lru_cache(maxsize=None)
def _cst_classes() -> tuple[Any, Any, Any, Any]:
    import libcst as cst
    from libcst.metadata import MetadataWrapper, PositionProvider

    def plain(v: Any) -> bool:
        if isinstance(v, cst.SimpleString):
            return "b" not in v.prefix.lower()
        if isinstance(v, cst.ConcatenatedString):
            return plain(v.left) and plain(v.right)
        return False

    def is_doc(small: Any) -> bool:
        return isinstance(small, cst.Expr) and plain(small.value)

    class Stripper(cst.CSTTransformer):
        def __init__(self, o: Opts, blocks: frozenset[int] = frozenset()) -> None:
            super().__init__()
            self.o, self.blocks, self.shebang = o, blocks, None

        def _row(self, node: Any) -> int:
            return -1

        def _keep(self, text: str, node: Any) -> bool:
            if self.o.all:
                return False
            if node is self.shebang:
                return True
            if self.blocks and self._row(node) in self.blocks:
                return True
            return _keep_text(text)

        def visit_Module(self, node: Any) -> bool:
            h = node.header
            if h and h[0].comment is not None and h[0].comment.value.startswith("#!"):
                self.shebang = h[0]
            return True

        def leave_TrailingWhitespace(self, original: Any, updated: Any) -> Any:
            c = updated.comment
            if c is None or self._keep(c.value, original):
                return updated
            return updated.with_changes(whitespace=cst.SimpleWhitespace(""), comment=None)

        def leave_EmptyLine(self, original: Any, updated: Any) -> Any:
            c = updated.comment
            if c is not None and not (self.o.strip_full or self.o.all):
                return updated
            if c is not None and self._keep(c.value, original):
                return updated
            return cst.RemoveFromParent()

        def _strip_body(self, body: Any) -> Any:
            if isinstance(body, cst.IndentedBlock):
                stmts = list(body.body)
                if not stmts:
                    return body
                first = stmts[0]
                if not (isinstance(first, cst.SimpleStatementLine) and first.body and is_doc(first.body[0])):
                    return body
                if len(first.body) > 1:
                    stmts[0] = first.with_changes(body=first.body[1:])
                elif len(stmts) == 1:
                    stmts[0] = first.with_changes(body=[cst.Pass()])
                else:
                    stmts.pop(0)
                    stmts[0] = stmts[0].with_changes(leading_lines=[*first.leading_lines, *stmts[0].leading_lines])
                return body.with_changes(body=stmts)
            if isinstance(body, cst.SimpleStatementSuite) and body.body and is_doc(body.body[0]):
                return body.with_changes(body=body.body[1:] or [cst.Pass()])
            return body

        def _def(self, updated: Any) -> Any:
            if self.o.strip_docs or self.o.all:
                return updated.with_changes(body=self._strip_body(updated.body))
            return updated

        def leave_FunctionDef(self, original: Any, updated: Any) -> Any:
            return self._def(updated)

        def leave_ClassDef(self, original: Any, updated: Any) -> Any:
            return self._def(updated)

        def leave_Module(self, original: Any, updated: Any) -> Any:
            if not self.o.all or not updated.body:
                return updated
            body = list(updated.body)
            first = body[0]
            if not (isinstance(first, cst.SimpleStatementLine) and first.body and is_doc(first.body[0])):
                return updated
            if len(first.body) > 1:
                body[0] = first.with_changes(body=first.body[1:])
            else:
                body.pop(0)
                if body:
                    body[0] = body[0].with_changes(leading_lines=[*first.leading_lines, *body[0].leading_lines])
            return updated.with_changes(body=body)

    class StripperPos(Stripper):
        METADATA_DEPENDENCIES = (PositionProvider,)

        def _row(self, node: Any) -> int:
            return self.get_metadata(PositionProvider, node).start.line - 1

    return cst, MetadataWrapper, Stripper, StripperPos


def _strip_cst(src: bytes, o: Opts) -> bytes:
    cst, wrapper, stripper, stripper_pos = _cst_classes()
    module = cst.parse_module(src)
    blocks = _block_code_rows(src.split(b"\n")) if (o.strip_full and not o.all) else set()
    if blocks:
        out = wrapper(module, unsafe_skip_copy=True).visit(stripper_pos(o, frozenset(blocks)))
    else:
        out = module.visit(stripper(o))
    return out.bytes


def _valid(new: bytes, old: bytes, o: Opts) -> bool:
    try:
        ast.parse(new)
        return True
    except Exception:
        pass
    try:
        ast.parse(old)
        return False
    except Exception:
        pass
    if o.backend == "ts":
        return not _PARSER.parse(new).root_node.has_error
    try:
        _cst_classes()[0].parse_module(new)
        return True
    except Exception:
        return False


def _atomic_write(path: str, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".pystrip-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _init_worker(backend: str) -> None:
    global _PARSER
    if backend == "ts":
        _PARSER = _make_parser()
    else:
        _cst_classes()


def load_cache() -> dict[str, str]:
    global _CACHE_MEM
    if _CACHE_MEM:
        return _CACHE_MEM
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _CACHE_MEM = json.load(f)
                return _CACHE_MEM
        except Exception:
            return {}
    return {}


def update_cache(path: str, file_hash: str) -> None:
    global _CACHE_MEM
    try:
        _CACHE_MEM[path] = file_hash
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_CACHE_MEM, f)
    except Exception:
        pass


def process_file(path: str, o: Opts) -> Result:
    try:
        real = os.path.realpath(path)
        with open(real, "rb") as f:
            src = f.read()
        file_hash = hashlib.sha256(src).hexdigest()

        cache_data = load_cache()
        if cache_data.get(real) == file_hash:
            return Result(path, 0, None)

        new = _strip_ts(src, o) if o.backend == "ts" else _strip_cst(src, o)
        if new == src:
            update_cache(real, file_hash)
            return Result(path, 0, None)
        if not _valid(new, src, o):
            logger.error(f"Result validation failed for {path}")
            return Result(path, 0, "result failed validation, file untouched")
        _atomic_write(real, new)
        with open(real, "rb") as f:
            new_hash = hashlib.sha256(f.read()).hexdigest()
        update_cache(real, new_hash)
        return Result(path, len(src) - len(new), None)
    except Exception as e:
        logger.exception(f"Error processing file {path}: {e}")
        return Result(path, 0, f"{type(e).__name__}: {e}")


def collect(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(p: str) -> None:
        rp = os.path.realpath(p)
        if rp not in seen:
            seen.add(rp)
            out.append(p)

    for p in paths:
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".dist-info"))
                for n in sorted(names):
                    path = Path(root) / n
                    if path.is_symlink():
                        continue
                    if n.endswith(".py"):
                        add(os.path.join(root, n))
        elif os.path.isfile(p):
            add(p)
        else:
            logger.warning(f"warning: {p}: not found")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Strip comments/docstrings from Python files in place.")
    ap.add_argument(
        "paths",
        default=[
            Path.cwd(),
        ],
        nargs="*",
        help="files or directories",
    )
    ap.add_argument("-c", action="store_true", help="strip all comments, not just inline ones")
    ap.add_argument("-d", action="store_true", help="strip all docstrings (module docstring kept)")
    ap.add_argument(
        "-a", action="store_true", help="strip everything, even shebang/module docstring/directives/commented-out code"
    )
    ap.add_argument("-b", choices=("ts", "libcst"), default="libcst", help="backend (default: libcst)")
    ap.add_argument(
        "--mp-method",
        choices=(
            "InterpreterPoolExecutor",
            "Process",
            "ThreadPool",
            "imap_unordered",
            "apply_async",
            "map_async",
            "starmap_async",
            "map",
            "starmap",
        ),
        default="apply_async",
        help="multiprocessing method",
    )
    ap.add_argument("--workers", type=int, default=WORKERS, help="number of workers")
    args = ap.parse_args(argv)

    opts = Opts(
        backend=args.b,
        strip_full=args.c or args.a,
        strip_docs=args.d or args.a,
        all=args.a,
        mp_method=args.mp_method,
        workers=args.workers,
    )
    files = collect(args.paths)

    files = [Path(p) for p in files]
    files = sorted(files, key=lambda p: p.stat().st_size, reverse=True)

    if not files:
        logger.warning("no files")
        return 1

    n = len(files)
    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    green = (lambda s: f"\x1b[32m{s}\x1b[0m") if color else (lambda s: s)
    dim = (lambda s: f"\x1b[2m{s}\x1b[0m") if color else (lambda s: s)
    cyan = (lambda s: f"\x1b[96m{s}\x1b[0m") if color else (lambda s: s)

    w, numw = len(str(n)), 10
    total = changed = 0
    errors: list[Result] = []

    chunksize = max(1, min(500, ceil(n / 16)))
    logger.info(f"chunk: {chunksize}")

    func = functools.partial(process_file, o=opts)
    str_files = [str(f) for f in files]

    try:
        results = []
        if opts.mp_method == "InterpreterPoolExecutor":
            with cf.InterpreterPoolExecutor(
                max_workers=opts.workers, initializer=_init_worker, initargs=(opts.backend)
            ) as ex:
                results = list(ex.map(func, str_files, chunksize=4))
        elif opts.mp_method == "Process":
            with cf.ProcessPoolExecutor(
                max_workers=opts.workers, initializer=_init_worker, initargs=(opts.backend,)
            ) as ex:
                results = list(ex.map(func, str_files, chunksize=chunksize))
        elif opts.mp_method == "ThreadPool":
            with cf.ThreadPoolExecutor(max_workers=opts.workers) as ex:
                results = list(ex.map(func, str_files, chunksize=chunksize))
        else:
            with Pool(processes=opts.workers, initializer=_init_worker, initargs=(opts.backend,)) as pool:
                if opts.mp_method == "imap_unordered":
                    results = list(pool.imap_unordered(func, str_files, chunksize=chunksize))
                elif opts.mp_method == "map":
                    results = pool.map(func, str_files, chunksize=chunksize)
                elif opts.mp_method == "starmap":
                    results = pool.map(func, str_files)
                elif opts.mp_method == "apply_async":
                    res_objs = [pool.apply_async(func, (f,)) for f in str_files]
                    results = [r.get() for r in res_objs]
                elif opts.mp_method == "map_async":
                    results = pool.map_async(func, str_files).get()
                elif opts.mp_method == "starmap_async":
                    results = pool.map_async(func, str_files).get()
                else:
                    results = list(pool.imap_unordered(func, str_files, chunksize=chunksize))

        for i, r in enumerate(results):
            cols = shutil.get_terminal_size((100, 24)).columns
            pre = f"[{i:>{w}}/{n}]"
            namew = max(8, cols - len(pre) - 2 - 2 - numw)
            name = r.path if len(r.path) <= namew else "…" + r.path[-(namew - 1) :]
            if r.err:
                errors.append(r)
                logger.error(f"Error in file {r.path}: {r.err}")
                col = cyan("error".rjust(numw))
            else:
                num = f"{r.delta:,}".rjust(numw)
                if r.delta:
                    total += r.delta
                    changed += 1
                    col = green(num)
                else:
                    col = dim(num)
            print(f"{pre}  {name:<{namew}}  {col}")

    except KeyboardInterrupt:
        logger.warning("Interrupted by user via Ctrl+C. Exiting gracefully...")

    print(f"\n{changed}/{n} files changed, {green(f'{total:,}')} bytes reduced")
    for r in errors:
        print(f"{cyan('error')}: {r.path}: {r.err}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
