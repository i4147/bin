from __future__ import annotations

import ast
import io
import os
import shutil
import tempfile
import tokenize
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Sequence

from . import backends, hints
from .models import BackendError, FileResult, HintOptions, Outcome, StripOptions
from .parallel import ParallelRunner, make_runner
from .text import normalize_newlines, unified_diff

SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)
BACKUP_SUFFIX = ".bak"
DEFAULT_ENCODING = "utf-8"
BOM = b"\xef\xbb\xbf"


@dataclass(slots=True)
class Document:
    text: str
    encoding: str = DEFAULT_ENCODING
    with_bom: bool = False
    crlf: bool = False

    @classmethod
    def load(cls, path: Path) -> "Document":
        raw = path.read_bytes()
        encoding, with_bom = detect_encoding(raw)
        body = raw[len(BOM) :] if with_bom else raw
        text = body.decode(encoding, "replace")
        crlf = "\r\n" in text
        return cls(normalize_newlines(text), encoding, with_bom, crlf)

    def render(self, text: str) -> str:
        return text.replace("\n", "\r\n") if self.crlf else text

    def save(self, path: Path, text: str) -> None:
        payload = self.render(text).encode(self.encoding, "replace")
        if self.with_bom:
            payload = BOM + payload
        atomic_write(path, payload)


def detect_encoding(raw: bytes) -> tuple[str, bool]:
    with_bom = raw.startswith(BOM)
    try:
        encoding, _lines = tokenize.detect_encoding(io.BytesIO(raw).readline)
    except (SyntaxError, UnicodeDecodeError):
        return DEFAULT_ENCODING, with_bom
    if encoding in {"utf-8-sig", "utf-8"}:
        return "utf-8", with_bom
    return (encoding or DEFAULT_ENCODING), with_bom


def atomic_write(path: Path, payload: bytes) -> None:
    mode = path.stat().st_mode if path.exists() else None
    descriptor, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def backup_path(path: Path) -> Path:
    candidate = path.with_name(path.name + BACKUP_SUFFIX)
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}{BACKUP_SUFFIX}{counter}")
        counter += 1
    return candidate


class FileFinder:
    def __init__(
        self,
        suffixes: Sequence[str] = (".py",),
        skip_dirs: Iterable[str] = SKIP_DIRS,
        exclude: Sequence[str] = (),
    ) -> None:
        self.suffixes = tuple(suffixes)
        self.skip_dirs = frozenset(skip_dirs)
        self.exclude = tuple(pattern.strip("/") for pattern in exclude if pattern.strip("/"))

    def is_candidate(self, path: Path) -> bool:
        if path.suffix not in self.suffixes or not path.is_file():
            return False
        if path.name.endswith(BACKUP_SUFFIX):
            return False
        parts = set(path.parts)
        if parts & self.skip_dirs:
            return False
        text = str(path)
        return not any(f"/{pattern}/" in text or text.startswith(f"{pattern}/") for pattern in self.exclude)

    def discover(self, roots: Iterable[Path]) -> list[Path]:
        found: list[Path] = []
        for root in roots:
            if root.is_file():
                if self.is_candidate(root):
                    found.append(root)
                continue
            for directory, names, files in os.walk(root):
                base = Path(directory)
                names[:] = sorted(name for name in names if name not in self.skip_dirs)
                for name in sorted(files):
                    candidate = base / name
                    if self.is_candidate(candidate):
                        found.append(candidate)
        return found


def parse_guard(code: str, path: Path) -> str:
    try:
        ast.parse(code, filename=str(path))
    except SyntaxError as error:
        raise BackendError(f"{path}: invalid result at line {error.lineno}: {error.msg}") from error
    return code


@dataclass
class Processor:
    backend_name: str = "ast"
    strip: StripOptions = field(default_factory=StripOptions)
    hints: HintOptions = field(default_factory=HintOptions)
    write: bool = True
    backup: bool = False
    diff: bool = False
    verify: bool = True
    _backend: object | None = field(default=None, repr=False, compare=False)

    @property
    def backend(self):
        if self._backend is None:
            self._backend = backends.resolve(self.backend_name)
        return self._backend

    def __getstate__(self) -> dict:
        return {
            "backend_name": self.backend_name,
            "strip": self.strip,
            "hints": self.hints,
            "write": self.write,
            "backup": self.backup,
            "diff": self.diff,
            "verify": self.verify,
        }

    def __setstate__(self, state: dict) -> None:
        self.backend_name = state["backend_name"]
        self.strip = state["strip"]
        self.hints = state["hints"]
        self.write = state["write"]
        self.backup = state["backup"]
        self.diff = state["diff"]
        self.verify = state["verify"]
        self._backend = None

    def with_backend(self, name: str) -> "Processor":
        return replace(self, backend_name=name)

    def strip_text(self, source: str, path: Path = Path("<memory>")) -> Outcome:
        outcome = self.backend.strip(source, self.strip)
        if self.verify and outcome.changed:
            parse_guard(outcome.code, path)
        return outcome

    def annotate_text(self, source: str, stub: str, path: Path = Path("<memory>")) -> Outcome:
        outcome = self.backend.add_hints(source, stub, self.hints)
        if self.verify and outcome.changed:
            parse_guard(outcome.code, path)
        return outcome

    def stub_for(self, path: Path, document: Document) -> str:
        if self.hints.stub is not None:
            return self.hints.stub.read_text(encoding="utf-8")
        return hints.stubs_from_source(document.text, path.stem, self.hints.stubgen_args)

    def process(self, path: Path) -> FileResult:
        try:
            document = Document.load(path)
        except OSError as error:
            return FileResult(path, error=str(error))
        before = document.text
        try:
            if self.strip.is_noop and self.hints.stub is None and not self.hints.generate_stub:
                return FileResult(path, error="nothing to do: enable --comments, --docstrings, --annotations or hints")
            outcome = self.strip_text(before, path) if not self._hint_mode() else self._annotate(path, document, before)
        except BackendError as error:
            return FileResult(path, error=str(error))
        except Exception as error:
            return FileResult(path, error=f"{type(error).__name__}: {error}")
        if not outcome.changed and outcome.code == before:
            return FileResult(path, backend=outcome.backend, before=len(before), after=len(before))
        text_diff = unified_diff(before, outcome.code, path.name) if self.diff else ""
        error = ""
        if self.write:
            try:
                if self.backup:
                    shutil.copy2(path, backup_path(path))
                document.save(path, outcome.code)
            except OSError as failure:
                error = str(failure)
        return FileResult(
            path,
            backend=outcome.backend,
            comments=outcome.comments,
            docstrings=outcome.docstrings,
            annotations=outcome.annotations,
            before=len(before),
            after=len(outcome.code),
            error=error,
            diff=text_diff,
        )

    def _hint_mode(self) -> bool:
        return self.hints.stub is not None or self.hints.generate_stub

    def _annotate(self, path: Path, document: Document, source: str) -> Outcome:
        stub = self.stub_for(path, document)
        return self.annotate_text(source, stub, path)

    def run(self, paths: Sequence[Path], runner: ParallelRunner | None = None) -> list[FileResult]:
        if runner is None or runner.workers <= 1 or len(paths) <= 1:
            return [self.process(path) for path in paths]
        return runner.starmap(process_file, [(self, path) for path in paths])


def process_file(processor: Processor, path: Path) -> FileResult:
    return processor.process(path)


def run_paths(processor: Processor, paths: Sequence[Path], mode: str = "", workers: int = 0) -> list[FileResult]:
    runner = make_runner(mode, workers)
    with runner:
        return processor.run(paths, runner)
