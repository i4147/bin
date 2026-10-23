# Original scripts kept for reference

Nothing in this folder is imported by `pysource`. It holds verbatim copies of the
scripts that were consolidated into the `pysource` package plus the `pystrip.py`
entry point.

- `upstream/` — the 29 scripts as they exist in the `bin_oct` repository
  (the `*rmc*.py`, `stripy.py`, `py_clean.py`, `pyclean.py`, `cleants.py` family
  and the annotation trio `annotate.py`, `annotate_types.py`,
  `clean_type_annotations.py`).
- `repo/` — the four scripts that lived at the root of this repository before the
  consolidation (`cleanpy.py`, `clean_type_annotations.py`, `py_clean.py`,
  `pyclean.py`).

`rmc-ts.py` from the request list does not exist in any of the source
repositories; the file with that role is `rmcts.py`.

## Mapping

Every "new command" below assumes the sources are the paths in the last column
and that `pystrip.py` is run from the repository root.

| Original | New command | Notes |
| --- | --- | --- |
| `armc.py` | `pystrip.py strip -b ast DIR` | AST pass, comments only |
| `brmc.py` | `pystrip.py strip -b ast DIR --docstrings` | AST span splice |
| `benchrmc.py` | `pystrip.py strip -a DIR -j 8` | the benchmark is now `-j` |
| `cleants.py` | `pystrip.py strip -b ts DIR --docstrings` | tree-sitter backend |
| `crmc.py` | `pystrip.py strip -b ast DIR --diff` | |
| `drmc.py` | `pystrip.py strip -b {ast,libcst,ts} DIR --comments --annotations` | the reference implementation |
| `felormc.py` | `pystrip.py strip -b libcst DIR` | libcst transformer |
| `frmc.py` | `pystrip.py strip -b ast DIR` | |
| `grmc.py` | `pystrip.py strip -b ts DIR` | |
| `grmcst.py` | `pystrip.py strip -b libcst DIR` | |
| `py_clean.py` | `pystrip.py strip -b libcst DIR --comments --docstrings --annotations` | `-c/-d/-t` map to the long flags |
| `pyclean.py` | `pystrip.py strip -b libcst DIR` | `check` became `--check` |
| `rmc.py` | `pystrip.py strip -b ts DIR --docstrings` | |
| `rmc2.py` | `pystrip.py strip -b ast DIR` | |
| `rmc3.py` | `pystrip.py strip -b ast DIR --module-docstring` | PEP 695 sources need Python 3.12+ |
| `rmc_termux.py` | `pystrip.py strip -b ast DIR --json` | `--report-json` became `--json` |
| `rmca.py` | `pystrip.py strip -b ast DIR` | |
| `rmcco.py` | `pystrip.py strip -b ast DIR` | |
| `rmcomdocs.py` | `pystrip.py strip -b ts DIR --docstrings` | |
| `rmcst.py` | `pystrip.py strip -b libcst DIR --docstrings` | |
| `rmctools.py` | `pystrip.py strip -b ast DIR` | `--no-comments/--no-docstrings` became `--no-comments` |
| `rmcts.py` | `pystrip.py strip -b ts DIR` | |
| `srmc1.py` | `pystrip.py strip -a DIR -j 8 --json` | the kitchen-sink version |
| `srmc2.py` | `pystrip.py strip -a DIR -j 8 --json` | PEP 695 sources need Python 3.12+ |
| `stripy.py` | `pystrip.py strip -b libcst DIR` | |
| `tsrmc.py` | `pystrip.py strip -b ts DIR --diff` | |
| `annotate.py` | `pystrip.py annotate -s STUB.pyi DIR` | add hints from a stub |
| `annotate.py -r` | `pystrip.py strip --annotations DIR` | the removal mode |
| `annotate_types.py` | `pystrip.py annotate -g DIR` | stubgen per file, then apply |
| `clean_type_annotations.py` | `pystrip.py strip --annotations DIR` | |
