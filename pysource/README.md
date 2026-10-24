# pysource

One Python source tool that replaces the 30-plus one-off scripts that used to sit
next to each other: `armc.py`, `brmc.py`, `cleants.py`, `crmc.py`, `drmc.py`,
`felormc.py`, `frmc.py`, `grmc.py`, `grmcst.py`, `py_clean.py`, `pyclean.py`,
`rmc.py`, `rmc2.py`, `rmc3.py`, `rmc_termux.py`, `rmca.py`, `rmcco.py`,
`rmcomdocs.py`, `rmcst.py`, `rmctools.py`, `rmcts.py`, `srmc1.py`, `srmc2.py`,
`stripy.py`, `tsrmc.py`, `benchrmc.py`, `cleanpy.py`, `annotate.py`,
`annotate_types.py` and `clean_type_annotations.py`.

The originals are copied verbatim into [`orig/`](orig) together with a mapping
table in [`orig/README.md`](orig/README.md).

## Why the split looks like this

| Concern | Module | What lives there |
| --- | --- | --- |
| syntax engines | `backends.py` | `AstBackend`, `LibcstBackend`, `TreeSitterBackend`, `Backend`, `Plan`, `resolve()`, `BACKENDS` |
| parallelism | `parallel.py` | `ParallelRunner`, `MultiprocessingRunner`, `FuturesRunner`, `JoblibRunner`, `SerialRunner`, `make_runner()` |
| per-file work | `engine.py` | `Document`, `FileFinder`, `Processor`, `atomic_write`, `run_paths` |
| hint application | `hints.py` | `TypeshedSanitizer`, `apply_hints`, stubgen wrappers |
| text surgery | `text.py` | `LineIndex`, `TextEdit` application, comment/docstring span helpers |
| data | `models.py` | `StripOptions`, `HintOptions`, `Outcome`, `FileResult` |
| command line | `cli.py` | `strip`, `annotate`, `find`, `list` subcommands |

`parallel.py` and `backends.py` have no dependency on each other and can be copied
into another project on their own.

## Backends as classes

```python
from pysource import AstBackend, LibcstBackend, TreeSitterBackend, StripOptions

options = StripOptions(comments=True, docstrings=True, annotations=True)
outcome = LibcstBackend().strip(source, options)
print(outcome.code, outcome.comments, outcome.docstrings, outcome.annotations)
```

| Backend | Name / alias | Parser | Can strip | Can add hints | Needs |
| --- | --- | --- | --- | --- | --- |
| `AstBackend` | `ast` | `ast` + `tokenize` | comments, docstrings, annotations | no | stdlib |
| `LibcstBackend` | `libcst` | `libcst` | comments, docstrings, annotations | yes | `libcst` |
| `TreeSitterBackend` | `ts`, `tree-sitter` | `tree_sitter_python` | comments, docstrings, annotations | no | `tree-sitter`, `tree-sitter-python` |

All three produce byte-identical output on the 27-file reference corpus in
`bin_oct` (checked by `arena/test_pysource.py`), so the choice of backend is a
question of dependencies and speed, not of results.

Adding a backend means subclassing `Backend` and calling `register(...)`:

```python
from pysource import Outcome, StripOptions
from pysource.backends import Backend, register

class PassthroughBackend(Backend):
    name = "noop"
    aliases = ("identity",)

    def strip(self, source: str, options: StripOptions) -> Outcome:
        return Outcome(source, backend=self.name)

register(PassthroughBackend)
```

`--backend` rejects a backend whose third-party modules are missing, and names the
missing distribution.

## Parallelism as a class

```python
from pysource.parallel import make_runner

with make_runner("mp", workers=8) as runner:
    results = runner.starmap(work, [(a, b), (c, d)])
```

| Mode | Runner | Mechanism |
| --- | --- | --- |
| `mp`, `starmap` | `MultiprocessingRunner` | `multiprocessing.Pool.starmap` |
| `map` | `MultiprocessingRunner` | `Pool.map` |
| `imap_unordered` | `MultiprocessingRunner` | `Pool.imap_unordered` |
| `apply_async` | `MultiprocessingRunner` | `Pool.apply_async` + `get()` |
| `cf`, `cf_map` | `FuturesRunner` | `ProcessPoolExecutor.map` |
| `cf_submit` | `FuturesRunner` | `ProcessPoolExecutor.submit` + `result()` |
| `joblib` | `JoblibRunner` | `joblib.Parallel` + `delayed` |
| `serial`, `none` | `SerialRunner` | plain loop, no processes |

Every runner exposes `map` and `starmap`, is a context manager, and shares the same
`workers`/`chunksize` constructor arguments. The callable and its arguments must be
picklable; `Processor` implements `__getstate__`/`__setstate__` so its backend can
be rebuilt inside the worker.

## Command line

```
pystrip.py strip    [paths] [--comments/--no-comments] [--docstrings]
                    [--module-docstring] [--annotations] [-b ast|libcst|ts]
                    [-a] [--pattern GLOB] [--exclude DIR] [-j N] [-m MODE]
                    [-n] [-d] [--backup] [--check] [--json] [-q] [--no-verify]
pystrip.py annotate [paths] (-s STUB.pyi | -g) [--no-overwrite] [--future-annotations]
pystrip.py find     [paths] [-a]
pystrip.py list
```

Examples:

```sh
python3 pystrip.py strip -a .                        # inline comments only
python3 pystrip.py strip -a . --docstrings --annotations -b libcst -j 8
python3 pystrip.py strip -a . -n -d                  # dry run with a unified diff
python3 pystrip.py strip -a . --check                # exit 1 if anything would change
python3 pystrip.py strip -a . --json                 # machine-readable report
python3 pystrip.py annotate -a . -g                  # stubgen, then apply hints
python3 pystrip.py strip -a . --annotations --docstrings   # then round-trip back
```

Exit codes: `0` success, `1` `--check` found pending changes, `2` error.

## Behaviour guarantees

- **Safety gate.** Every changed file is re-parsed with `ast.parse` before it is
  written (`--no-verify` opts out). A failure is reported per file and the file is
  left untouched.
- **Atomic writes.** Output goes to a temporary file in the same directory and is
  moved over the original with `os.replace`, keeping the file mode.
- **Encoding and newlines.** The file's declared encoding (`tokenize.detect_encoding`),
  BOM and CRLF line endings are preserved.
- **Directives survive.** `#!` shebangs, `# type:`, `# fmt:`, `# noqa`, `# pylint:`,
  `# isort:`, `# coding:` and friends are kept unless `--no-keep-directives` is
  given. `# type:` comments are removed when `--annotations` is used, because they
  are annotations rather than comments.
- **No empty bodies.** Removing the only statement of a block inserts `pass` instead
  of leaving invalid syntax (`def f():` + docstring only).
- **Comments inside strings are untouched.** String literals, f-strings and triple
  quoted text are never edited because only real `COMMENT` tokens and real string
  expressions are considered.
- **Idempotence.** Running the same command twice changes nothing the second time.
- **Removing annotations is not always behaviour neutral.** `x: int = 5` becomes
  `x = 5`, which is fine, but a dataclass field or `NamedTuple` member loses its
  entry in `__annotations__`, so `dataclasses.fields()` and similar introspection
  will report nothing. `clean_type_annotations.py` had the same caveat.

## Tests

```sh
python3 arena/test_pysource.py            # stdlib unittest, skips missing extras
python3 arena/test_pysource.py -v
python3 pystrip.py strip -a . --check          # what would the tool change here?
```

The suite covers the three backends on adversarial fixtures, cross-backend output
identity, AST preservation for comment-only runs, semantics preservation for
docstring/annotation runs, every parallel mode, the file pipeline (encoding, CRLF,
BOM, backup, diff, `--check`), and the CLI end to end.

## Optional dependencies

```sh
pip install libcst tree-sitter tree-sitter-python joblib mypy
```

Only `ast` is always available. `annotate` needs `libcst`; `-g` also needs `mypy`
(for `mypy.stubgen`); `-m joblib` needs `joblib`.
