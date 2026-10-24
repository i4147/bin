from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StripOptions:
    comments: bool = True
    docstrings: bool = False
    module_docstring: bool = False
    annotations: bool = False
    keep_directives: bool = True
    keep_shebang: bool = True
    collapse_blank_lines: bool = False

    @property
    def wants_docstrings(self) -> bool:
        return self.docstrings or self.module_docstring

    @property
    def is_noop(self) -> bool:
        return not (self.comments or self.wants_docstrings or self.annotations)


@dataclass(frozen=True, slots=True)
class HintOptions:
    overwrite: bool = True
    future_annotations: bool = False
    stub: Path | None = None
    generate_stub: bool = False
    stubgen_args: tuple[str, ...] = ("--include-private",)


@dataclass(frozen=True, slots=True)
class Outcome:
    code: str
    comments: int = 0
    docstrings: int = 0
    annotations: int = 0
    backend: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.comments or self.docstrings or self.annotations)


@dataclass(frozen=True, slots=True)
class TextEdit:
    start: int
    end: int
    replacement: str = ""


@dataclass(frozen=True, slots=True)
class FileResult:
    path: Path
    backend: str = ""
    comments: int = 0
    docstrings: int = 0
    annotations: int = 0
    before: int = 0
    after: int = 0
    error: str = ""
    diff: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.comments or self.docstrings or self.annotations or self.before != self.after)


class BackendError(RuntimeError):
    pass


class UnsupportedOperation(BackendError):
    pass
