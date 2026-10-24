# arena — helper scripts for this repo

Tooling used to rewrite the root `p*.py` scripts and to keep them navigable.
Everything here is standalone: `python3` for the Python tools, bash for the rest.

| file | what it does |
| --- | --- |
| `script_similarity.py` | measures pairwise similarity of the root scripts |
| `build_similarity_report.py` | curated groups + merge plans → `script_similarity.json` |
| `script_similarity.json` | **the report**: 31 groups, 40 recipes, per-script index |
| `refactor_pfiles.py` | the class-based rewrite transformer |
| `verify_pfiles.py` | proves rewritten scripts behave like the originals |
| `annotation_stats.py` | annotation/comment/docstring coverage |
| `check_all.sh` | compile + comments + annotations + structure in one go |
| `inspect.sh` | quick look at a script (head/tail, defs, flags, imports, grep) |
| `compare_cli.sh` | diff `--help` / bare-run behaviour between two directories |
| `fetch_sources.sh` | fetch pristine `p*.py` from `i4147/bin_oct` into `orig/` + `out/` |
| `test_stripper.py` | 18 tests for `../stripper.py` (runs without tree-sitter) |
| `test_pysource.py` | 60 tests for `../pystrip.py` + `../pysource/` (skips missing extras) |

## The similarity report

`script_similarity.json` answers two questions:

**"Which script converts X?"** — use `recipes` (40 intents) or `index`:

```console
jq -r '.recipes[] | select(.intent | test("pickle")) | "\(.intent): \(.use|join(", "))"' \
    arena/script_similarity.json
# convert a pickle file to JSON: pklr.py

jq -r '.index["pdf2png.py"]' arena/script_similarity.json
# { "purpose": "Render PDF pages to PNG via pdftoppm.", "group": "pdf-render", ... }

jq -r '.groups[] | select(.id=="text-hygiene") | .merge.flags' arena/script_similarity.json
```

**"Which scripts do the same job, and how do I merge them?"** — use `groups`.
Each group carries: members with roles/backends, the reason they belong together,
the measured best pair, and a concrete merge plan (suggested binary name, flags,
files to delete, effort, risks, payoff).

Shapes:

```text
index[script]      -> {purpose, group, group_title, verdict, lines, third_party}
groups[]           -> {id, title, task, kind, confidence, scripts[], why,
                       evidence{best_pair, interpretation}, merge{suggested_name,
                       strategy, cli_example, flags[], remove_files[], effort,
                       risks[], payoff}}
recipes[]          -> {intent, use[], also[], group, group_title}
merge_plan[]       -> ordered steps, cheapest first
```

Group kinds: `near-duplicate` (≥0.5 measured), `duplicate-lineage`,
`variant-cluster` (same job, different technique), `toolkit-overlap` (subcommand
overlap), `category` (same domain, do **not** merge), `shared-library` (extract a
module, keep the CLIs).

### Headline numbers

| fact | value |
| --- | --- |
| scripts analysed | 101 |
| groups | 31 (28 mergeable, 3 informational) |
| scripts in groups / standalone | 84 / 17 |
| files removable if every merge lands | 52 |
| strongest measured duplicate | `pbat.py` = `pybat.py` (0.92) |

Biggest clusters by file count: `pypi-download` (6 → 1, `-b/--backend`),
`pkg-maintenance` (5 → 1, `-m/--manager`), `pdf-text` (5 → 1, `-b/--backend`),
`duplicate-detection` (4 → 1, `-e/--engine`), `formatting` (4 → 1, `-e/--engine`).

The backend-flag pattern the user asked about shows up literally in
`text-hygiene` (`pydos2unix` external binary vs `pydos2unix2` mmap →
`-b/--backend`), `pdf-render` (PyMuPDF vs pdftoppm), `image-terminal`
(true-colour vs block cells), `pdf-text`/`pdf-toolkit`/`pypi-download`
(library choice per backend).

### Similarity model

`script_similarity.py` scores every pair on five signals:
token 3-gram shingles (0.30), identifier Jaccard (0.25), function/class-name
Jaccard (0.20), import Jaccard (0.15) and CLI-flag Jaccard (0.10).

Scores rank candidates; they are not the grouping criterion. Scripts doing the
same job with different libraries score low (`pdf2img`/`pdf2png` 0.09) while
unrelated small scripts score ~0.15 on shared stdlib imports, so every group's
`evidence.interpretation` says how to read its number.

Regenerate everything:

```console
python3 arena/build_similarity_report.py          # writes script_similarity.json
python3 arena/build_similarity_report.py --check  # validate coverage only
python3 arena/script_similarity.py --top 40       # raw pair table
```

`--check` fails if any script is missing from the report, listed twice, or if a
recipe points at a non-existent script/group.

## The rewrite tools

```console
python3 arena/refactor_pfiles.py <src-dir> <dst-dir> --exclude pysearch.py py7zmax.py
python3 arena/verify_pfiles.py   <src-dir> <dst-dir>
```

`refactor_pfiles.py` moves module functions into a class (the module's own tool
class when it has one, otherwise one named after the file), rewrites references,
annotates every signature/constant/attribute, strips comments and docstrings, and
drops imports that became dead. `verify_pfiles.py` then compares structure,
importability and CLI behaviour against the originals: **99/99 ok, 0 warnings**.

## Daily helpers

```console
./arena/inspect.sh pypi_stats.py            # head 60 + tail 30, the triage view
./arena/inspect.sh -f pdfview.py            # classes and defs with line numbers
./arena/inspect.sh -c pdftoolz.py           # argparse flags
./arena/inspect.sh -g 'backend' pyget.py    # regex search

./arena/compare_cli.sh /path/to/orig . pdfview.py pypi_stats.py   # behaviour diff
./arena/check_all.sh                        # compile + comments + annotations + structure
python3 arena/annotation_stats.py --json    # coverage as JSON
./arena/fetch_sources.sh /tmp/work          # pristine sources + empty out/
```

`check_all.sh` skips `pysearch.py` and `py7zmax.py`, the two files excluded from
the rewrite, and it now compiles every file one by one instead of trusting
`compileall`, which reported success while two PEP 695 files in
`pysource/orig/upstream/` failed. Current state: 0 comment lines, 0 docstrings,
2,768/2,768 parameters annotated (87.2% concrete), 1,862/1,862 returns annotated,
everything class-based.

## Language comment strippers

`../stripper.py` replaces the per-language strippers that now live in
`../cleaners/`. It is class-based, fully annotated and comment-free, driven by one
language table (`Language` + `LANGUAGES`), so adding a language is a single row.

```console
./stripper.py --js --rs --css src/           # explicit languages
./stripper.py -a .                           # autodetect by extension under .
./stripper.py -a . -n -b regex               # dry run with a unified diff
./stripper.py --list-languages               # flag / extensions / markers / backend
./stripper.py -a . -b tree-sitter -j 8      # force a backend, use the process pool
```

- **21 languages**: `--bash --c --cpp --css --dockerfile --go --html --java --js
  --lua --make --php --rb --rs --sql --toml --ts --tsx --vim --xml --yaml`, plus
  aliases (`--sh`, `--javascript`, `--rust`, `--ruby`, `--yml`, ...).
- **`-a/--all`** walks the paths (default `.`), skipping `.git`, `.venv`,
  `node_modules`, `__pycache__`, `dist`, `build`, hidden files and `*.bak`, then
  picks a language per extension. Python is deliberately left alone.
- **Backends**: `tree-sitter` when the grammar imports, otherwise a string-aware
  `regex` scanner; `-b/--backend` forces either. Both drop comment-only lines,
  trim inline comments without leaving trailing whitespace, and keep markers that
  sit inside strings (`"http://x//y"`, `"""# not a comment"""`, `'# kept'`).
- **Shared flags**: `-n/--dry-run`, `-d/--diff`, `--backup`, `-j/--workers`,
  `-q/--quiet`, `--keep-blank-lines`.

Consolidation was safe because the old scripts barely differed. Pointing the
similarity tool at them shows it:

```console
python3 arena/script_similarity.py --root cleaners --pattern 'clean*.py' --top 8
# clean-js.py  / clean-ts.py    0.809
# clean-lua.py / clean-toml.py  0.668
# clean-js.py  / clean-lua.py   0.668
# clean-css.py / clean-js.py    0.643
```

Layout: language strippers in `cleaners/` (16 files, each mapped to its
`stripper.py` flag in `cleaners/README.md`), with `stripper.py` as the single entry
point for non-Python sources. Tests: `python3 arena/test_stripper.py` (18/18).

## Python comment + annotation tool

`../pystrip.py` and the `../pysource/` package replace the whole Python side:
the `*rmc*.py` family, `stripy.py`, `py_clean.py`, `pyclean.py`, `cleants.py`,
`cleanpy.py`, and the annotation trio `annotate.py` / `annotate_types.py` /
`clean_type_annotations.py`. The scripts themselves are archived verbatim in
`../pysource/orig/` with a per-script mapping table.

```console
./pystrip.py strip -a .                                  # inline comments only
./pystrip.py strip -a . --docstrings --annotations -b libcst -j 8
./pystrip.py strip -a . -n -d --json                     # preview + machine report
./pystrip.py annotate -a . -g                            # stubgen, then apply hints
./pystrip.py list                                        # backends + parallel modes
```

- **Backends as classes**: `AstBackend` (`ast`), `LibcstBackend` (`libcst`, the
  only one that can add hints) and `TreeSitterBackend` (`ts`) all live in
  `pysource/backends.py` behind one `Backend` protocol and a `register()` /
  `resolve()` registry. On the 27-file `bin_oct` reference corpus the three
  produce byte-identical output.
- **Parallelism as a class**: `pysource/parallel.py` exposes `ParallelRunner`
  with `MultiprocessingRunner` (`starmap`, `map`, `imap_unordered`,
  `apply_async`), `FuturesRunner` (`cf_map`, `cf_submit`), `JoblibRunner` and
  `SerialRunner`, all reachable through `make_runner(mode, workers)`.
- **Per-file pipeline**: `pysource/engine.py` handles discovery, encoding/BOM,
  CRLF, atomic writes, `.bak` backups, diffs and an `ast.parse` safety gate that
  refuses to write a file it cannot re-parse.
- **Grouped separately**: annotation *removal* is `strip --annotations`;
  annotation *addition* is `annotate` (`libcst` + `ApplyTypeAnnotationsVisitor`
  + a typeshed sanitiser, optionally fed by `mypy.stubgen`).

Tests: `python3 arena/test_pysource.py` (60 tests; the `libcst`, tree-sitter and
`joblib` cases skip themselves when those packages are absent).

## Notes

- Sources came from `i4147/bin_oct` (`3d93c5d`) because this repo only held
  `README.md`; `fetch_sources.sh` reproduces that layout.
- `py7max.py` does not exist; `py7zmax.py` is the real file and is excluded, as is
  `pysearch.py`.
- Docstring removal makes `__doc__` `None`; where a summary existed, `__doc__`
  references were rewritten to its first line so argparse descriptions still work.
