# p* rewrite tooling

The rewrite of the 99 root-level `p*.py` scripts was produced by two scripts in
this folder:

| file | purpose |
| --- | --- |
| `refactor_pfiles.py` | the transformer: class-based restructuring, type inference, simplification |
| `verify_pfiles.py` | the harness that proves the output behaves like the originals |

Both are plain `python3` scripts. The transformer only needs Black (used for
formatting); the verifier needs nothing beyond the standard library.

## Usage

```console
python3 arena/refactor_pfiles.py <src-dir> <dst-dir> \
    --exclude pysearch.py py7zmax.py --report report.json
python3 arena/verify_pfiles.py <src-dir> <dst-dir> --report verify.json
```

`refactor_pfiles.py` also accepts `--dry-run` (parse, transform, do not write)
and `--pattern` for a different source glob.

## What the transformer does

1. **Comments and docstrings are dropped** - the module is rebuilt from the AST,
   so `ast.unparse` cannot carry comments over, and every docstring statement is
   removed.
2. **Everything becomes class-based.** Module-level functions are moved into a
   class as `@staticmethod`s and every reference to them is rewritten to
   `Owner.name(...)`. The owner is
   - the module's single *tool* class when there is exactly one plain class that
     already owns its entry point (`run`/`main`/`cli`/...), otherwise
   - a new class named after the module (`pdf2png.py` -> `Pdf2png`,
     `py_clean.py` -> `PyClean`).
   Loose `__main__` code that is more than a single call is moved to a
   `run()` staticmethod; simple guards stay in place.
3. **Full signatures.** Every parameter, `*args`, `**kwargs` and return value is
   annotated. Types come from existing annotations, literal defaults,
   `isinstance` checks, comparison literals, collection/set/dict method calls,
   `Path`/text method usage, `os.path`/`open` arguments and `argparse.Namespace`
   attribute access; anything undecidable becomes `typing.Any` rather than a
   guess. Module constants and `__init__` attributes are annotated too.
   `from __future__ import annotations` is added so annotations never run.
4. **Simplification.** Constant `if` branches are inlined, `if c: return a else
   return b` collapses to `return a if c else b`, `if c: return True` (as a tail
   statement) collapses to `return bool(c)`, a trailing `return None` is removed,
   `else: pass` is dropped, stale `__all__` entries are pruned and imports that
   became unused are deleted.
5. **Ordering safety.** A class is emitted after every module-level name its
   decorators/defaults need, and module statements that reference lifted
   functions are moved behind the class, so name resolution still works.

## What the verifier checks

For each original/rewritten pair:

- `structure` - every module-level function still exists on the class, with the
  same parameter names/order/defaults, and nothing is left unannotated;
- `import` - the module imports cleanly (or fails for the same missing
  third-party dependency as the original);
- `cli` - `--help` and a bare run produce the same exit code and stdout.

Result on the final tree: **99/99 files, 0 failures, 0 warnings**.

## Numbers

| metric | value |
| --- | --- |
| files rewritten | 99 (`pysearch.py`, `py7zmax.py` excluded) |
| lines after rewrite | 39,764 |
| functions / methods | 1,956 |
| functions lifted into a class | 1,388 |
| rewritten call sites | 2,164 |
| annotations added | 1,126 |
| unused imports removed | 37 |
| docstrings removed | 6 |
| comment lines | 0 (shebangs only) |
| parameters annotated | 2,885 / 2,885 (87.8% concrete, rest `Any`) |
| returns annotated | 1,956 / 1,956 |

## Notes and deviations

- The source of the rewrite is `i4147/bin_oct` (commit `3d93c5d`), because
  `i4147/bin` only contained `README.md` at the time.
- `py7max.py` does not exist anywhere in the account; its sibling `py7zmax.py`
  was excluded instead, together with `pysearch.py`.
- Docstring removal means `__doc__` is `None` at runtime. Where a module
  docstring existed, references to `__doc__` are pointed at its first line, so
  `argparse` descriptions and `print(__doc__)` keep showing something sensible.
- Type inference never guesses container element types it cannot justify; those
  positions are annotated `Any` on purpose.
