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
the rewrite. Current state: 0 comment lines, 0 docstrings, 2,905/2,905 parameters
annotated (87.3% concrete), 1,956/1,956 returns annotated, everything class-based.

## Notes

- Sources came from `i4147/bin_oct` (`3d93c5d`) because this repo only held
  `README.md`; `fetch_sources.sh` reproduces that layout.
- `py7max.py` does not exist; `py7zmax.py` is the real file and is excluded, as is
  `pysearch.py`.
- Docstring removal makes `__doc__` `None`; where a summary existed, `__doc__`
  references were rewritten to its first line so argparse descriptions still work.
