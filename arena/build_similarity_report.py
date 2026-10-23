#!/usr/bin/env python3
"""Build the script similarity / consolidation report.

The clustering judgement is curated here (group ids, roles, merge plans); the
pairwise scores come from ``script_similarity.py`` and are attached as evidence,
never used as the sole criterion.

Usage::

    python3 arena/build_similarity_report.py            # writes arena/script_similarity.json
    python3 arena/build_similarity_report.py --check    # validate coverage only
    python3 arena/build_similarity_report.py --stdout   # print instead of writing
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import script_similarity

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "arena" / "script_similarity.json"
COMMIT = "8d270cc"
EXCLUDED_FROM_REWRITE = ["pysearch.py", "py7zmax.py"]

PURPOSES: dict[str, str] = {
    "pathcmd.py": "Print every PATH entry, one per line.",
    "pathdiag.py": "Diagnose PATH: duplicates, shadowed binaries, alias/function collisions, optional rc rewrite.",
    "pbat.py": "bat-style file viewer: syntax highlighting, line ranges, paging.",
    "pdf2img.py": "Render PDF pages to JPG with PyMuPDF.",
    "pdf2page.py": "Extract every PDF page to its own .txt with the first of five available backends.",
    "pdf2png.py": "Render PDF pages to PNG via pdftoppm.",
    "pdf2text.py": "Extract PDF text, concatenated or split per page (PyPDF2 / pdfplumber).",
    "pdftools.py": "PDF utility suite on PyMuPDF: text, per-page split, merge, image/font/attachment extraction.",
    "pdftools2.py": "Unified PDF toolkit: outline, merge, to-image, rename pages, shrink, split by size.",
    "pdftoolz.py": "Unified PDF toolkit, PyMuPDF-only sibling of pdftools2.py.",
    "pdftotext.py": "Extract text from PDFs with backend fallback (fitz / pdfminer / pdfplumber / pypdf).",
    "pdfv.py": "Terminal text viewer for PDFs (PyMuPDF extraction, scroll, goto page).",
    "pdfv2.py": "Terminal PDF viewer that renders pages as images (PPM).",
    "pdfview.py": "Scrollable terminal pager for PDF text with backend autodetection.",
    "pdfviewer.py": "Terminal PDF viewer that renders pages at a zoom level.",
    "pdif.py": "Naive line-by-line file comparison based on line membership.",
    "pdiff.py": "Diff two Python files by AST / bytecode instead of text.",
    "perpage.py": "Split PDFs into single pages and extract text per page in a process pool.",
    "pfzf.py": "Interactive fuzzy finder (fzf-like) over stdin lines with a preview pane.",
    "pip_downloader.py": "Download a package's sdist/wheel from PyPI.",
    "pip_reinstaller.py": "Reinstall pure-Python packages, or packages listed in a file.",
    "pip_sanity_check.py": "Sanity-check installed packages (import + metadata).",
    "pipget.py": "Download sdists / pure wheels from a PyPI mirror with archive validation.",
    "piu.py": "Uninstall installed packages, with wildcard matching.",
    "pkg_insoector.py": "Inspect site-packages: duplicate dists, multi-version installs, missing console scripts.",
    "pkg_tools.py": "apt + pip + wheel package-management toolkit (install, clean, du).",
    "pkgfetch.py": "Find and download distributions from PEP 503 / 691 indexes.",
    "pkgres.py": "Detect and autofix deprecated pkg_resources usage.",
    "pkgtools.py": "Copy, move and repack installed distributions.",
    "pkgtools2.py": "Debian/Termux package and system-bin toolkit.",
    "pkl2json.py": "Convert a pickle file to JSON.",
    "pklr.py": "Pickle processor: JSON conversion, raw dump, sanitising.",
    "pkltojson.py": "Convert a pickle to JSON with best-effort serialisation.",
    "plotter.py": "Plot a math function with matplotlib.",
    "pngq.py": "Optimise PNGs with pngquant across a file tree.",
    "pnr.py": "Rename files/dirs by removing or replacing strings, or templating numbers.",
    "pnr2.py": "Advanced renamer: regex, template, case/unicode transforms, undo log.",
    "ppage.py": "Early per-page PDF splitter + text extractor (ancestor of perpage.py).",
    "prefactor.py": "Merge a multi-module package into a single-file module.",
    "pret.py": "Run prettier over the given files (minimal wrapper).",
    "pret2.py": "Run prettier over JS/TS/CSS/HTML/JSON trees with error folder, workers, timeout.",
    "prompt-reformatter.py": "Reflow prompt/text files to 80 columns, fixing list spacing.",
    "psearch.py": "Search PyPI packages through the JSON API.",
    "pss.py": "Substring search over a local PyPI package index (SQLite FTS5 trigram).",
    "purepy.py": "Classify installed packages as pure-Python vs native-wheel using PyPI metadata.",
    "py2ipynb.py": "Convert a Python script to a notebook, splitting at cell markers.",
    "py3migrate.py": "Python 2→3 toolbox: detect, lib2to3 refactor, fix print, strip tags.",
    "py7zmax.py": "Compress files/directories to .7z (excluded from the rewrite).",
    "py_clean.py": "Strip comments, docstrings and type hints with LibCST; parallel, gitignore-aware.",
    "py_refact.py": "Merge a package into one modernised module (six/py2, os.path, logging rewrites).",
    "py_refactor.py": "Refactor a small package: merge modules, build __all__, keep backups.",
    "pybat.py": "Identical sibling of pbat.py (bat clone).",
    "pycatimg.py": "Display images, including SVG, in the terminal in true colour.",
    "pychafa.py": "Display an image in the terminal with block characters.",
    "pyclean.py": "Unified source cleaner: strip docstrings/comments (AST, libcst, tokenize) and check.",
    "pycleaner.py": "Report and autofix unused imports and unused definitions.",
    "pyco.py": "Remove __pycache__ directories and report freed space.",
    "pycppcheck.py": "Syntax-check C/C++ files with clang in parallel.",
    "pycurl_downloader.py": "Download URLs from a list with pycurl, retries and resume.",
    "pydeduper.py": "Find duplicate top-level definitions and extract them into a utils module.",
    "pydevtools.py": "Scaffold and package Python or Rust projects (pyproject, cargo).",
    "pydiff.py": "Line diff that hashes/groups differing files and can move them.",
    "pydocr.py": "Extract docstrings and AST docs from modules into Markdown.",
    "pydos2unix.py": "Convert CRLF to LF across a tree (dos2unix wrapper).",
    "pydos2unix2.py": "Fast CRLF→LF converter with mmap, worker pool and error log.",
    "pydup.py": "Find duplicate Python files, definitions and near-duplicates (token, tlsh, ssdeep).",
    "pyenhance.py": "Fix invalid escapes, bytes literals and `is` comparisons in Python source.",
    "pyextract.py": "Extract functions/classes/constants from files, archives or trees (AST/libcst/sqlite).",
    "pyfmt.py": "Unified HTML/CSS/JS/JSON formatter and linter glue.",
    "pyfslint.py": "Lint text files for CRLF, BOM, missing final newline and binary content.",
    "pygdu.py": "Interactive disk-usage browser (TUI).",
    "pyget.py": "pip-style download manager with progress bars and pluggable backends.",
    "pyhead.py": "Print the first 1 KiB of a file.",
    "pyjcal.py": "Jalali (Persian) calendar CLI and date converter.",
    "pylychee.py": "Check and fix Markdown / HTML image links.",
    "pymonolith.py": "Save a page as one HTML file with CSS/JS/images inlined.",
    "pyocr.py": "OCR CLI over tesseract/cuneiform (pyocr library port).",
    "pypi_stats.py": "CLI for pypistats.org download statistics (subcommands, cache, formats).",
    "pypi_update_check.py": "Check installed packages for available updates with state caching.",
    "pypi_user_pkgs.py": "List and download every package published by a PyPI user or org.",
    "pypidl.py": "Unified PyPI downloader: mirrors, wheel scoring, hash verification.",
    "pypistats.py": "Fetch and aggregate pypistats.org statistics (helper layer).",
    "pyproj.py": "Scaffold a new Python project and create/push its GitHub repository.",
    "pyqalc.py": "Command-line calculator with unit conversion.",
    "pyrg.py": "ripgrep-like recursive search with type presets and glob filters.",
    "pyrgb.py": "Interactive RGB colour chart for the terminal.",
    "pyruf.py": "Run ruff, including on extensionless Python scripts.",
    "pyrun.py": "Run every .py under a directory and summarise outcomes.",
    "pysanity-check.py": "Check that installed packages import and report latest versions.",
    "pysearch.py": "File/content search toolkit (grep/ls/hex/info) — excluded from the rewrite.",
    "pyshuf.py": "Shuffle the lines of a file (crypto / weighted / mmap strategies).",
    "pysim.py": "Structural similarity of top-level definitions via AST hashes.",
    "pysplit.py": "Split a text file into N line-balanced parts.",
    "pysvg.py": "Optimise SVGs with svgo across a file tree.",
    "pyterser.py": "Minify JS/TS with terser.",
    "python_downloader.py": "pip-style download manager, cwd-only older sibling of pyget.py.",
    "python_duplicate_finder.py": "Duplicate finder sibling of pydup.py with fewer engines.",
    "pytoipynb.py": "Convert a script to a notebook (simple cell splitting).",
    "pytokei.py": "Count lines of code, comments and blanks by language.",
    "pyxsrt.py": "Extract embedded subtitle streams from video with ffmpeg/ffprobe.",
    "pyzpkg.py": "Convert installed site-packages directories into zipimport-ready archives.",
}


def script(path: str, role: str, verdict: str = "merge", backends: list[str] | None = None) -> dict[str, object]:
    entry: dict[str, object] = {"path": path, "role": role, "verdict": verdict}
    if backends:
        entry["backends"] = backends
    return entry


GROUPS: list[dict[str, object]] = [
    {
        "id": "pdf-text",
        "title": "PDF → text extraction",
        "task": "Extract text from PDFs: whole document, per page, or one .txt per page",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("pdftotext.py", "backend-fallback extractor", "merge-target",
                   ["fitz", "pdfminer", "pdfplumber", "pypdf"]),
            script("pdf2page.py", "per-page .txt writer, five optional backends",
                   backends=["PyPDF2", "fitz", "pdfminer", "pdfplumber", "pypdf"]),
            script("pdf2text.py", "concat/split text extractor", backends=["PyPDF2", "pdfplumber"]),
            script("perpage.py", "parallel per-page split + text extraction", backends=["pypdf", "loguru"]),
            script("ppage.py", "older, serial ancestor of perpage.py"),
        ],
        "why": (
            "All five read the same input and emit text; three of them write one .txt per page. "
            "Backend choice is baked in as an import fallback chain, which is exactly the axis a "
            "-b/--backend flag should control."
        ),
        "merge": {
            "suggested_name": "pdftotext",
            "strategy": "One CLI; backend and output layout become flags, parallelism comes from perpage.py.",
            "cli_example": "pdftotext -b pdfplumber --mode per-page -j 8 -o out/ report.pdf",
            "flags": [
                {"flag": "-b, --backend", "values": ["pymupdf", "pypdf", "pdfplumber", "pdfminer"],
                 "replaces": "hard-coded import fallback order in each script"},
                {"flag": "--mode", "values": ["concat", "per-page", "split"],
                 "replaces": "pdf2text run_concat/run_split, pdf2page, perpage split_pdf_by_page"},
                {"flag": "-j, --jobs", "replaces": "perpage.py process pool"},
                {"flag": "-o, --output-dir", "replaces": "per-page output naming in three scripts"},
                {"flag": "--pages", "replaces": "ad-hoc page selection in pdf2page.py"},
            ],
            "remove_files": ["pdf2text.py", "pdf2page.py", "ppage.py"],
            "effort": "M",
            "risks": ["per-backend quirks (encrypted PDFs, layout mode) differ; keep per-backend tests"],
            "payoff": "5 files → 1, one place to fix extraction bugs",
        },
    },
    {
        "id": "pdf-viewer",
        "title": "Terminal PDF viewers",
        "task": "View PDF content in the terminal (extracted text or rendered pages)",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("pdfview.py", "text pager with backend autodetection", "merge-target",
                   ["fitz", "pdfminer", "pypdf"]),
            script("pdfv.py", "PyMuPDF text viewer with goto/scroll", backends=["pymupdf", "readchar"]),
            script("pdfv2.py", "image viewer rendering PPM pages", backends=["ghostscript"]),
            script("pdfviewer.py", "image viewer with zoom", backends=["fitz"]),
        ],
        "why": (
            "Two rendering strategies (extract text vs rasterise pages) have each been implemented "
            "twice, and all four share terminal sizing and key handling."
        ),
        "merge": {
            "suggested_name": "pdfview",
            "strategy": "Single viewer; text vs image is a mode, the rasteriser is a backend.",
            "cli_example": "pdfview --mode image -b fitz --zoom 1.5 report.pdf",
            "flags": [
                {"flag": "--mode", "values": ["text", "image"],
                 "replaces": "pdfview/pdfv (text) vs pdfv2/pdfviewer (image)"},
                {"flag": "-b, --backend", "values": ["fitz", "pdfminer", "pypdf", "ghostscript"],
                 "replaces": "per-script extraction and rasterisation choices"},
                {"flag": "--zoom", "replaces": "pdfviewer.py set_zoom"},
                {"flag": "--keys", "values": ["vi", "arrows"], "replaces": "three copies of key decoding"},
            ],
            "remove_files": ["pdfv.py", "pdfv2.py", "pdfviewer.py"],
            "effort": "M",
            "risks": ["image mode needs a rasteriser dependency; keep ghostscript optional"],
            "payoff": "4 files → 1, shared key handling; measured twin pdfv2/pdfviewer 0.48",
        },
    },
    {
        "id": "pdf-toolkit",
        "title": "PDF manipulation toolkits",
        "task": "Merge, split, outline, compress and convert PDF files",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("pdftools2.py", "toolkit with pdf2image/pypdf/pdfplumber backends", "merge-target",
                   ["pdf2image", "pypdf", "pdfplumber", "lxml"]),
            script("pdftoolz.py", "PyMuPDF-only toolkit with the same subcommands", backends=["pymupdf"]),
            script("pdftools.py", "PyMuPDF text/split/merge/attachment extras", backends=["pymupdf"]),
        ],
        "why": (
            "pdftools2.py and pdftoolz.py expose the same subcommands (outline, merge, to-img, "
            "rename-pages, shrink, split-size) with different libraries — the textbook backend split. "
            "pdftools.py adds extraction subcommands that belong in the same CLI."
        ),
        "merge": {
            "suggested_name": "pdftools",
            "strategy": "Subcommand CLI; library selection is a backend flag plus optional dependency guards.",
            "cli_example": "pdftools -b pymupdf merge --out book.pdf a.pdf b.pdf",
            "flags": [
                {"flag": "-b, --backend", "values": ["pymupdf", "pypdf", "pdf2image", "ghostscript"],
                 "replaces": "pdftoolz vs pdftools2 dependency choice"},
                {"flag": "subcommands", "values": ["merge", "split", "split-size", "outline", "to-img",
                                                   "rename-pages", "shrink", "extract", "info"],
                 "replaces": "the union of cmd_* methods in the three scripts"},
            ],
            "remove_files": ["pdftools2.py", "pdftoolz.py", "pdftools.py"],
            "effort": "L",
            "risks": ["largest surface area of any group; migrate one subcommand per commit"],
            "payoff": "3 toolkits → 1; measured twin pdftools2/pdftoolz 0.66",
        },
    },
    {
        "id": "pdf-render",
        "title": "PDF → raster images",
        "task": "Render PDF pages to PNG/JPG files",
        "kind": "near-duplicate",
        "confidence": "high",
        "scripts": [
            script("pdf2img.py", "PyMuPDF rasteriser", "merge-target", ["pymupdf"]),
            script("pdf2png.py", "pdftoppm rasteriser", backends=["poppler"]),
        ],
        "why": "Same job, two renderers; output only differs in format and naming.",
        "merge": {
            "suggested_name": "pdf2img",
            "strategy": "Keep one script, expose format and renderer.",
            "cli_example": "pdf2img -b pdftoppm -f png --dpi 300 report.pdf",
            "flags": [
                {"flag": "-b, --backend", "values": ["pymupdf", "pdftoppm"],
                 "replaces": "two scripts"},
                {"flag": "-f, --format", "values": ["png", "jpg"], "replaces": "hard-coded format"},
                {"flag": "--dpi", "replaces": "pdf2png dpi argument"},
                {"flag": "-o, --output-dir", "replaces": "output dir handling in both"},
            ],
            "remove_files": ["pdf2png.py"],
            "effort": "S",
            "risks": ["pdftoppm is an external binary; guard with shutil.which"],
            "payoff": "2 files → 1, format no longer decides which script to call",
        },
    },
    {
        "id": "image-terminal",
        "title": "Images in the terminal",
        "task": "Render image files as text/ANSI inside the terminal",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("pycatimg.py", "true-colour renderer with SVG support", "merge-target", ["PIL", "cairosvg"]),
            script("pychafa.py", "block-character renderer", backends=["PIL"]),
            script("pyrgb.py", "colour-chart viewer, keep as demo mode", "keep"),
        ],
        "why": (
            "pycatimg.py and pychafa.py both open an image with PIL, fit it to the terminal and "
            "emit ANSI cells; they differ only in the cell renderer."
        ),
        "merge": {
            "suggested_name": "pycatimg",
            "strategy": "One renderer with a cell-style flag; pyrgb becomes a --demo colour chart.",
            "cli_example": "pycatimg -s blocks logo.png ; pycatimg --demo",
            "flags": [
                {"flag": "-s, --style", "values": ["truecolor", "blocks", "halfblocks"],
                 "replaces": "pycatimg vs pychafa pixel-to-cell code"},
                {"flag": "--svg", "replaces": "cairosvg fallback in pycatimg"},
                {"flag": "--width / --height / --fit", "replaces": "per-script terminal sizing"},
                {"flag": "--demo", "replaces": "pyrgb.py colour chart entry point"},
            ],
            "remove_files": ["pychafa.py"],
            "effort": "S",
            "risks": ["true-colour support varies by terminal; auto-detect and fall back to blocks"],
            "payoff": "2 files → 1 (plus pyrgb as a mode)",
        },
    },
    {
        "id": "python-source-cleanup",
        "title": "Python source stripping / cleanup",
        "task": "Strip comments, docstrings, type hints; remove unused imports and definitions",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("pyclean.py", "unified cleaner with AST/libcst/tokenize engines", "merge-target",
                   ["libcst"]),
            script("py_clean.py", "LibCST stripper with config, workers, gitignore, diff", backends=["libcst"]),
            script("pycleaner.py", "unused-import and unused-definition reporter/autofixer"),
        ],
        "why": (
            "pyclean.py and py_clean.py strip the same three things and both grew a libcst engine; "
            "pycleaner.py is the inverse operation on the same AST, which fits as a subcommand."
        ),
        "merge": {
            "suggested_name": "pyclean",
            "strategy": "One tool: transform subcommands plus engine selection; keep py_clean config flags.",
            "cli_example": "pyclean strip -e libcst --docstrings --comments -j 8 src/ ; pyclean unused --autofix src/",
            "flags": [
                {"flag": "subcommands", "values": ["strip", "check", "diff", "unused"],
                 "replaces": "py_clean.py modes + pycleaner.py p_imp/p_def subcommands"},
                {"flag": "-e, --engine", "values": ["libcst", "ast", "tokenize"],
                 "replaces": "three stripping implementations"},
                {"flag": "--docstrings / --comments / --type-hints", "replaces": "py_clean.py flag set"},
                {"flag": "-j, --jobs / --workers", "replaces": "duplicated worker pools"},
            ],
            "remove_files": ["py_clean.py", "pycleaner.py"],
            "effort": "M",
            "risks": ["libcst is optional in pyclean.py; keep the AST path dependency-free"],
            "payoff": "3 → 1; strip and unused-detection finally share one parser",
        },
    },
    {
        "id": "package-merge",
        "title": "Collapse a package into a single module",
        "task": "Merge a multi-file Python package into one file",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("py_refactor.py", "merge with __all__ building and backups", "merge-target", ["libcst"]),
            script("prefactor.py", "merge with import resolution and topological ordering"),
            script("py_refact.py", "merge + py2/six/os.path/logging modernisation passes"),
        ],
        "why": "Three implementations of the same pipeline: discover → order → rewrite imports → emit.",
        "merge": {
            "suggested_name": "pyrefactor",
            "strategy": "One pipeline; output layout and optional rewrites are flags.",
            "cli_example": "pyrefactor --layout single --modernize --backup pkg/",
            "flags": [
                {"flag": "--layout", "values": ["single", "top-level", "subpkg"],
                 "replaces": "py_refactor build_merged_module / build_top_level_modules / build_subpkg_modules"},
                {"flag": "--modernize", "replaces": "py_refact strip_six_and_py2 / rewrite_os_path / rewrite_logging"},
                {"flag": "--all-list / --no-all", "replaces": "py_refactor make_all_list"},
                {"flag": "--backup / --restore", "replaces": "py_refactor ensure/restore_backups"},
            ],
            "remove_files": ["prefactor.py", "py_refact.py"],
            "effort": "L",
            "risks": ["import rewriting is where bugs hide; diff every generated module against the manual merge"],
            "payoff": "3 → 1; the modernisation passes stop being tied to one merger",
        },
    },
    {
        "id": "python-modernize",
        "title": "Python 2 → 3 / source modernisation",
        "task": "Migrate or clean up legacy Python syntax",
        "kind": "toolkit-overlap",
        "confidence": "medium",
        "scripts": [
            script("py3migrate.py", "2to3 driver, print fixer, tag stripping", "merge-target", ["lib2to3"]),
            script("pyenhance.py", "escape-sequence, bytes-literal and `is` comparison fixer"),
        ],
        "why": (
            "py3migrate rewrites syntax py3 cannot run; pyenhance rewrites syntax that is valid but "
            "wrong (invalid escapes, `is 'x'`). Same visitor shape, same target files."
        ),
        "cross_refs": ["package-merge"],
        "merge": {
            "suggested_name": "py3migrate",
            "strategy": "Add pyenhance's fixes as a fixer set; keep detect/refactor/fixprint subcommands.",
            "cli_example": "py3migrate refactor --fixer escapes --fixer print --fixer is-comparison src/",
            "flags": [
                {"flag": "--fixer", "values": ["print", "escapes", "bytes", "is-comparison", "six"],
                 "replaces": "pyenhance.py's hard-coded fix list"},
                {"flag": "--check / --diff", "replaces": "pyenhance fingerprint/compile_check reporting"},
            ],
            "remove_files": ["pyenhance.py"],
            "effort": "M",
            "risks": ["lib2to3 is deprecated; plan the move to a token-based fixer"],
            "payoff": "2 → 1, one rewrite registry",
        },
    },
    {
        "id": "duplicate-detection",
        "title": "Duplicate / similarity detection for Python code",
        "task": "Find duplicate files, duplicate definitions and near-duplicate code",
        "kind": "near-duplicate",
        "confidence": "high",
        "scripts": [
            script("pydup.py", "full engine: token, tlsh, ssdeep, joblib, trash", "merge-target",
                   ["tlsh", "ssdeep", "joblib", "xxhash"]),
            script("python_duplicate_finder.py", "sibling with the same classes, fewer engines"),
            script("pydeduper.py", "duplicate top-level definitions → utils module"),
            script("pysim.py", "structural AST-hash similarity between two files"),
        ],
        "why": (
            "pydup.py and python_duplicate_finder.py are structural twins (17 shared classes, "
            "identical method names, measured 0.81). The other two are the same question asked "
            "at definition granularity (pydeduper) and by compare mode (pysim)."
        ),
        "merge": {
            "suggested_name": "pydup",
            "strategy": "One CLI; granularity is a mode, algorithm is an engine, actions are flags.",
            "cli_example": "pydup --mode defs -e token --dedupe-to utils/ src/ ; pydup --mode files --trash",
            "flags": [
                {"flag": "--mode", "values": ["files", "defs", "near", "compare"],
                 "replaces": "pydup files/near + pydeduper defs + pysim compare"},
                {"flag": "-e, --engine", "values": ["token", "tlsh", "ssdeep", "struct"],
                 "replaces": "engine imports scattered across pydup/python_duplicate_finder"},
                {"flag": "--trash / --json / --cache", "replaces": "pydup action flags"},
                {"flag": "--dedupe-to DIR", "replaces": "pydeduper write_utils/patch_originals"},
            ],
            "remove_files": ["python_duplicate_finder.py", "pydeduper.py", "pysim.py"],
            "effort": "M",
            "risks": ["ssdeep/tlsh are optional C deps; degrade to token mode"],
            "payoff": "4 → 1; removes the largest measured duplicate (0.81)",
        },
    },
    {
        "id": "pypi-download",
        "title": "Download packages from PyPI / mirrors",
        "task": "Fetch wheels and sdists for a package name, list file or user",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("pypidl.py", "mirror-aware downloader, wheel scoring, hash verify", "merge-target",
                   ["requests", "pycurl", "rich"]),
            script("pyget.py", "download manager with progress backends", backends=["requests", "pycurl", "urllib"]),
            script("python_downloader.py", "older cwd-only sibling of pyget.py"),
            script("pipget.py", "mirror downloader with archive validation", backends=["httpx", "bs4"]),
            script("pip_downloader.py", "minimal PyPI fetcher", backends=["httpx"]),
            script("pypi_user_pkgs.py", "download all packages of a PyPI user/org", backends=["httpx", "pycurl"]),
        ],
        "why": (
            "Six scripts fetch distributions over HTTP. pyget.py and python_downloader.py are the "
            "same architecture (0.50) with three interchangeable HTTP backends in each; pypidl.py "
            "and pipget.py both add mirror selection and archive validation."
        ),
        "merge": {
            "suggested_name": "pypidl",
            "strategy": "One downloader: transport is a backend, the rest are flags/subcommands.",
            "cli_example": "pypidl -b requests --mirror tsinghua --verify-hash numpy ; pypidl user --all pyca",
            "flags": [
                {"flag": "-b, --backend", "values": ["requests", "pycurl", "urllib", "httpx"],
                 "replaces": "Backend/PurePythonBackend/RequestsBackend/PycurlBackend and httpx paths"},
                {"flag": "--mirror", "replaces": "pipget and pypidl mirror tables"},
                {"flag": "--users / subcommand user", "replaces": "pypi_user_pkgs.py"},
                {"flag": "--manager / --progress", "replaces": "pyget-style download manager mode"},
                {"flag": "--verify-hash", "replaces": "pypidl hash verification"},
            ],
            "remove_files": ["python_downloader.py", "pyget.py", "pipget.py", "pip_downloader.py", "pypi_user_pkgs.py"],
            "effort": "L",
            "risks": ["progress rendering differs per backend; extract one presenter first"],
            "payoff": "6 → 1; the heaviest duplication in the repo after pdf/dup clusters",
        },
    },
    {
        "id": "pypi-search",
        "title": "Search PyPI packages",
        "task": "Find packages by name/description",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("psearch.py", "JSON API search", "merge-target", ["requests"]),
            script("pss.py", "local SQLite FTS5 substring search"),
        ],
        "why": "Same user question, two data sources: live API vs a local index for offline/fast search.",
        "merge": {
            "suggested_name": "psearch",
            "strategy": "Source selectable; pss keeps its index-build subcommand.",
            "cli_example": "psearch --source index 'numpy' ; psearch index --rebuild",
            "flags": [
                {"flag": "--source", "values": ["api", "index"], "replaces": "two entry points"},
                {"flag": "--rebuild-index", "replaces": "pss.py table build path"},
                {"flag": "--limit", "replaces": "pss DEFAULT_LIMIT and psearch paging"},
            ],
            "remove_files": ["pss.py"],
            "effort": "S",
            "risks": ["index lives at a hard-coded /sdcard path; make it configurable"],
            "payoff": "2 → 1, offline search becomes a flag not a separate tool",
        },
    },
    {
        "id": "pypi-stats",
        "title": "PyPI download statistics",
        "task": "Report download counts from pypistats.org",
        "kind": "duplicate-lineage",
        "confidence": "high",
        "scripts": [
            script("pypi_stats.py", "full CLI: subcommands, cache, formats", "merge-target",
                   ["urllib3", "prettytable", "termcolor"]),
            script("pypistats.py", "fetch/aggregate helper layer", backends=["ssl"]),
        ],
        "why": "pypistats.py is the networking half of pypi_stats.py with a different entry point.",
        "merge": {
            "suggested_name": "pypistats",
            "strategy": "Absorb the helper functions as private module functions; CLI stays in one file.",
            "cli_example": "pypistats recent numpy --period week -f json",
            "flags": [
                {"flag": "--cache-dir", "replaces": "platformdirs cache location"},
                {"flag": "subcommands", "values": ["recent", "overall", "python-major", "python-minor", "system"],
                 "replaces": "pypistats.py get_stats/aggregate callers"},
            ],
            "remove_files": ["pypistats.py"],
            "effort": "S",
            "risks": ["pypistats.py may be imported by other scripts; grep before deleting"],
            "payoff": "2 → 1",
        },
    },
    {
        "id": "pypi-health",
        "title": "Installed-package health checks",
        "task": "Check whether installed packages import and are up to date",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("pypi_update_check.py", "update checker with state cache", "merge-target",
                   ["packaging", "pycurl", "requests"]),
            script("pysanity-check.py", "import check + latest version"),
            script("pip_sanity_check.py", "installed package sanity check"),
        ],
        "why": (
            "All three answer 'is what I have installed healthy?': two check importability, two "
            "fetch the latest version, and two keep a JSON state file."
        ),
        "merge": {
            "suggested_name": "pypi-check",
            "strategy": "One CLI with check modes; state file shared.",
            "cli_example": "pypi-check --mode outdated --state ~/.cache/pypi-check.json ; pypi-check --mode import",
            "flags": [
                {"flag": "--mode", "values": ["outdated", "import", "latest", "all"],
                 "replaces": "three scripts"},
                {"flag": "-j, --jobs", "replaces": "per-script pooling"},
                {"flag": "--state", "replaces": "duplicated state JSON handling"},
                {"flag": "--json", "replaces": "output formats"},
            ],
            "remove_files": ["pip_sanity_check.py", "pysanity-check.py"],
            "effort": "S",
            "risks": ["import checks must run in a subprocess to survive broken modules"],
            "payoff": "3 → 1",
        },
    },
    {
        "id": "installed-inspection",
        "title": "Inspect installed distributions",
        "task": "Audit site-packages: duplicates, versions, purity, deprecated APIs",
        "kind": "variant-cluster",
        "confidence": "medium",
        "scripts": [
            script("pkg_insoector.py", "duplicate/multi-version/missing-script audit", "merge-target"),
            script("purepy.py", "pure-Python vs native-wheel classification via PyPI"),
            script("pkgres.py", "deprecated pkg_resources usage scanner"),
        ],
        "why": (
            "Each walks site-packages and reports a property of the installed set; they differ only "
            "in what they check and how they fetch metadata."
        ),
        "merge": {
            "suggested_name": "pkginspect",
            "strategy": "Subcommands per audit; one site-packages enumerator underneath.",
            "cli_example": "pkginspect duplicates ; pkginspect purity -j 8 ; pkginspect pkgresources --autofix src/",
            "flags": [
                {"flag": "subcommands", "values": ["duplicates", "versions", "scripts", "purity", "pkgresources"],
                 "replaces": "pkg_insoector cmd_* + purepy + pkgres"},
                {"flag": "--json", "replaces": "ad-hoc report formats"},
            ],
            "remove_files": ["purepy.py", "pkgres.py"],
            "effort": "M",
            "risks": ["purepy hits the network; keep the offline dedup checks usable without it"],
            "payoff": "3 → 1 with a shared enumerator",
        },
    },
    {
        "id": "pkg-maintenance",
        "title": "Package / site-packages maintenance",
        "task": "Install, clean, copy, repack or zip installed packages",
        "kind": "toolkit-overlap",
        "confidence": "medium",
        "scripts": [
            script("pkg_tools.py", "apt + pip + wheel toolkit", "merge-target",
                   ["pip", "packaging", "rapidfuzz"]),
            script("pkgtools2.py", "Debian/Termux package + system-bin toolkit", backends=["apt", "dpkg"]),
            script("pkgtools.py", "copy/move/repack distributions", backends=["packaging"]),
            script("pyzpkg.py", "site-packages dirs → zipimport archives"),
            script("pyco.py", "__pycache__ cleanup"),
        ],
        "why": (
            "pkg_tools.py and pkgtools2.py are both 'run apt/pip and clean up the result' with the "
            "same helper shapes (run_cmd, human_size, parallel_map); pkgtools.py adds copy/repack, "
            "pyzpkg.py is repack-to-zip, pyco.py is the cleanup tail of the same workflow."
        ),
        "merge": {
            "suggested_name": "pkgtool",
            "strategy": "Subcommand CLI; manager selection is a flag, destructive actions keep --dry-run.",
            "cli_example": "pkgtool -m apt missing-files ; pkgtool copy --to wheelhouse numpy ; pkgtool clean --pycache",
            "flags": [
                {"flag": "-m, --manager", "values": ["pip", "apt", "wheel"],
                 "replaces": "pkg_tools vs pkgtools2 split"},
                {"flag": "subcommands", "values": ["install", "missing-files", "copy", "zip", "clean", "du"],
                 "replaces": "cmd_* methods across the five scripts"},
                {"flag": "--dry-run / -y, --yes", "replaces": "inconsistent confirmation flags"},
            ],
            "remove_files": ["pkgtools2.py", "pkgtools.py", "pyzpkg.py", "pyco.py"],
            "effort": "L",
            "risks": ["apt/dpkg calls are platform-specific; keep them behind the apt manager"],
            "payoff": "5 → 1; the scattered 'clean' operations get one home",
        },
    },
    {
        "id": "pip-ops",
        "title": "pip install/uninstall operations",
        "task": "Uninstall or reinstall installed packages",
        "kind": "variant-cluster",
        "confidence": "medium",
        "scripts": [
            script("piu.py", "uninstall with wildcard matching", "merge-target", ["pip"]),
            script("pip_reinstaller.py", "reinstall pure-Python or listed packages", backends=["pip"]),
        ],
        "why": "Both are thin wrappers that resolve package lists and call pip's internal main().",
        "merge": {
            "suggested_name": "pipop",
            "strategy": "Two subcommands over one resolver.",
            "cli_example": "pipop uninstall -w numpy* ; pipop reinstall --pure -j 8",
            "flags": [
                {"flag": "subcommands", "values": ["uninstall", "reinstall"],
                 "replaces": "the two scripts"},
                {"flag": "-w, --wildcard", "replaces": "piu.py wildcard mode"},
                {"flag": "-l, --list-file", "replaces": "pip_reinstaller.py list parsing"},
                {"flag": "--pure", "replaces": "pip_reinstaller.py pure-python filter"},
            ],
            "remove_files": ["pip_reinstaller.py"],
            "effort": "S",
            "risks": ["pip._internal is unstable API; pin or prefer subprocess pip"],
            "payoff": "2 → 1",
        },
    },
    {
        "id": "file-renamer",
        "title": "File / directory renamers",
        "task": "Rename files with patterns, templates, case and unicode transforms",
        "kind": "duplicate-lineage",
        "confidence": "high",
        "scripts": [
            script("pnr2.py", "superset renamer: regex, template, undo, preview", "merge-target"),
            script("pnr.py", "remove/replace/template renamer (older)"),
        ],
        "why": "pnr.py is the ancestor; pnr2.py already implements every pnr.py flag plus undo.",
        "merge": {
            "suggested_name": "pnr",
            "strategy": "Delete the old script; keep pnr2's behaviour under the short name.",
            "cli_example": "pnr -r old -s new --regex --dry-run ; pnr -t img --start 1 --padding 3 --undo log.json",
            "flags": [
                {"flag": "--remove / --replace", "replaces": "pnr.py core options"},
                {"flag": "-t, --template", "replaces": "both template implementations"},
                {"flag": "--dry-run / --undo", "replaces": "pnr2 preview and undo log"},
            ],
            "remove_files": ["pnr.py"],
            "effort": "S",
            "risks": ["renaming is destructive; keep --dry-run default-on when ambiguous"],
            "payoff": "2 → 1, no behaviour loss",
        },
    },
    {
        "id": "text-hygiene",
        "title": "Text-file hygiene (line endings, BOM, final newline)",
        "task": "Detect or fix CRLF, BOM and missing trailing newlines",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("pyfslint.py", "linter over a tree (CRLF/BOM/binary/final newline)", "merge-target"),
            script("pydos2unix.py", "CRLF→LF via dos2unix", backends=["dos2unix"]),
            script("pydos2unix2.py", "CRLF→LF with mmap + workers", backends=["mmap"]),
        ],
        "why": (
            "pyfslint reports exactly what the two converters rewrite, and the converters perform "
            "the same transformation at different speeds: one shells out to dos2unix, the other "
            "uses mmap and a worker pool."
        ),
        "merge": {
            "suggested_name": "pyfslint",
            "strategy": "Lint by default, fix with --fix; converter implementation is a backend.",
            "cli_example": "pyfslint --fix newline -b mmap -j 8 src/ ; pyfslint --check .",
            "flags": [
                {"flag": "-b, --backend", "values": ["stream", "mmap", "dos2unix"],
                 "replaces": "pydos2unix (external binary) vs pydos2unix2 (mmap)"},
                {"flag": "--fix", "values": ["newline", "bom", "final-newline"],
                 "replaces": "converter side effects"},
                {"flag": "-j, --jobs / --error-log", "replaces": "pydos2unix2 worker pool"},
                {"flag": "--check", "replaces": "pyfslint default report"},
            ],
            "remove_files": ["pydos2unix.py", "pydos2unix2.py"],
            "effort": "S",
            "risks": ["binary detection must run before any rewrite"],
            "payoff": "3 → 1; the backend flag covers both the external binary and the in-process mmap path",
        },
    },
    {
        "id": "file-diff",
        "title": "File comparison / diffing",
        "task": "Compare files (text lines, bytecode) and act on the result",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("pydiff.py", "line diff with hashing, grouping and move", "merge-target", ["dh"]),
            script("pdiff.py", "AST/bytecode-level diff of Python files", backends=["dis", "ast"]),
            script("pdif.py", "naive membership line compare"),
        ],
        "why": "Three comparison granularities for the same question; pydiff already has the plumbing.",
        "merge": {
            "suggested_name": "pydiff",
            "strategy": "Comparison level is a mode; the move/group action stays.",
            "cli_example": "pydiff --mode bytecode a.py b.py ; pydiff --mode lines --move-to changed/ a/ b/",
            "flags": [
                {"flag": "--mode", "values": ["lines", "bytecode", "groups"],
                 "replaces": "pdif/pdiff/pydiff"},
                {"flag": "--move-to", "replaces": "pydiff next_group_dir"},
                {"flag": "--ignore-whitespace", "replaces": "manual normalisation in pdif"},
            ],
            "remove_files": ["pdif.py", "pdiff.py"],
            "effort": "S",
            "risks": ["bytecode diff needs identical interpreter versions"],
            "payoff": "3 → 1",
        },
    },
    {
        "id": "pickle-json",
        "title": "Pickle → JSON conversion",
        "task": "Convert pickle files to JSON",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("pklr.py", "processor: json/raw/sanitise", "merge-target"),
            script("pkl2json.py", "pandas-assisted conversion", backends=["pandas"]),
            script("pkltojson.py", "recursive serialiser"),
        ],
        "why": "Same conversion with three different fallback strategies for non-JSON-safe objects.",
        "merge": {
            "suggested_name": "pklr",
            "strategy": "One converter; output style and coercion strategy are flags.",
            "cli_example": "pklr data.pkl --format json --coerce pandas ; pklr data.pkl --raw",
            "flags": [
                {"flag": "--format", "values": ["json", "raw", "pretty"], "replaces": "three scripts"},
                {"flag": "--coerce", "values": ["strict", "recursive", "pandas"],
                 "replaces": "pkltojson recursive serialiser + pkl2json pandas path"},
                {"flag": "-o, --output", "replaces": "per-script output handling"},
            ],
            "remove_files": ["pkl2json.py", "pkltojson.py"],
            "effort": "S",
            "risks": ["pandas path is heavy; keep it optional"],
            "payoff": "3 → 1",
        },
    },
    {
        "id": "notebook-convert",
        "title": "Python script → Jupyter notebook",
        "task": "Convert a .py script into an .ipynb notebook",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("py2ipynb.py", "marker-based cell splitting", "merge-target", ["nbformat"]),
            script("pytoipynb.py", "simple cell splitting", backends=["nbformat"]),
        ],
        "why": "Two nbformat writers with different cell-splitting heuristics.",
        "merge": {
            "suggested_name": "py2ipynb",
            "strategy": "One writer; splitting heuristic is a flag.",
            "cli_example": "py2ipynb --style markers script.py ; py2ipynb --style simple script.py",
            "flags": [
                {"flag": "--style", "values": ["markers", "simple"], "replaces": "the two scripts"},
                {"flag": "--cell-marker", "replaces": "hard-coded '%%' detection"},
                {"flag": "-o, --output", "replaces": "per-script naming"},
            ],
            "remove_files": ["pytoipynb.py"],
            "effort": "S",
            "risks": ["keep the output valid for nbformat 4"],
            "payoff": "2 → 1",
        },
    },
    {
        "id": "formatting",
        "title": "Formatting and minification",
        "task": "Format or minify web assets and code",
        "kind": "toolkit-overlap",
        "confidence": "high",
        "scripts": [
            script("pyfmt.py", "HTML/CSS/JS/JSON formatter with tool glue", "merge-target",
                   ["yapf", "cssbeautifier", "jsbeautifier", "tree_sitter"]),
            script("pyterser.py", "terser minifier wrapper", backends=["terser"]),
            script("pret2.py", "prettier runner with error folder and workers", backends=["prettier"]),
            script("pret.py", "minimal prettier wrapper"),
        ],
        "why": (
            "pyfmt.py already merges eight formatters; pret/pret2 call prettier, pyterser calls "
            "terser — the same 'walk files, run an external formatter' loop three more times."
        ),
        "merge": {
            "suggested_name": "pyfmt",
            "strategy": "External tools become engines; one walker and one error policy.",
            "cli_example": "pyfmt -e prettier --jobs 8 src/ ; pyfmt -e terser --minify dist/*.js",
            "flags": [
                {"flag": "-e, --engine", "values": ["yapf", "tree-sitter", "prettier", "terser", "js-beautify", "css-beautify"],
                 "replaces": "pyfmt tool table + pret/pret2/pyterser"},
                {"flag": "--minify", "replaces": "pyterser mode"},
                {"flag": "-j, --jobs / --error-dir", "replaces": "pret2 worker pool and error folder"},
                {"flag": "--check / --diff", "replaces": "pyfmt check mode"},
            ],
            "remove_files": ["pret.py", "pret2.py", "pyterser.py"],
            "effort": "M",
            "risks": ["global node binaries; keep engine detection explicit"],
            "payoff": "4 → 1; pret2's robustness (timeouts, error folder) benefits every engine",
        },
    },
    {
        "id": "optimizers",
        "title": "Asset optimiser wrappers",
        "task": "Run an external optimiser (pngquant / svgo) over a file tree",
        "kind": "near-duplicate",
        "confidence": "high",
        "scripts": [
            script("pngq.py", "pngquant wrapper", "merge-target", ["pngquant"]),
            script("pysvg.py", "svgo wrapper", backends=["svgo"]),
        ],
        "why": (
            "Structurally identical (walk → optimise_one → saved/bytes report), measured 0.58 the "
            "highest score outside the true duplicates — only the binary and extension differ."
        ),
        "merge": {
            "suggested_name": "pyopt",
            "strategy": "One wrapper; asset type picks the optimiser, or pass a binary explicitly.",
            "cli_example": "pyopt --type svg assets/ ; pyopt --type png --quality 80 img/",
            "flags": [
                {"flag": "--type", "values": ["png", "svg"], "replaces": "the two scripts"},
                {"flag": "--bin", "replaces": "hard-coded pngquant/svgo paths"},
                {"flag": "--jobs / --dry-run", "replaces": "both walkers"},
            ],
            "remove_files": ["pysvg.py"],
            "effort": "S",
            "risks": ["flag vocabularies differ per binary; pass --extra-args through"],
            "payoff": "2 → 1; template for future optimisers (jpeg, webp)",
        },
    },
    {
        "id": "bat-clone",
        "title": "bat-style file viewer",
        "task": "View files with syntax highlighting, line ranges and paging",
        "kind": "near-duplicate",
        "confidence": "high",
        "scripts": [
            script("pbat.py", "bat clone with highlighting, ranges, paging", "merge-target", ["pygments"]),
            script("pybat.py", "byte-identical sibling of pbat.py"),
        ],
        "why": (
            "The highest measured pair in the repo (0.92, identical imports, flags and definitions): "
            "pybat.py is a copy of pbat.py, so there is nothing to reconcile, only one file to delete."
        ),
        "merge": {
            "suggested_name": "pbat",
            "strategy": "Delete pybat.py; keep pbat.py (shorter name, same behaviour).",
            "cli_example": "pbat -n 40 file.py",
            "flags": [
                {"flag": "(none)", "values": [], "replaces": "no behaviour change; pure duplicate removal"}
            ],
            "remove_files": ["pybat.py"],
            "effort": "S",
            "risks": ["check whether shell aliases or rc files call pybat.py before deleting"],
            "payoff": "2 → 1 with zero porting risk; do this one first",
        },
    },
    {
        "id": "path-tools",
        "title": "PATH inspection",
        "task": "Show and diagnose the PATH environment variable",
        "kind": "duplicate-lineage",
        "confidence": "high",
        "scripts": [
            script("pathdiag.py", "full diagnostic + optional rc rewrite", "merge-target"),
            script("pathcmd.py", "prints PATH entries only"),
        ],
        "why": "pathcmd.py is the first screen of pathdiag.py's output.",
        "merge": {
            "suggested_name": "pathdiag",
            "strategy": "Fold the printer in as the default subcommand.",
            "cli_example": "pathdiag show ; pathdiag conflicts --fix-dedup",
            "flags": [
                {"flag": "subcommands", "values": ["show", "conflicts", "aliases"],
                 "replaces": "pathcmd.py + pathdiag sections"},
                {"flag": "--fix-dedup / --rc-file", "replaces": "pathdiag update_rc_file"},
            ],
            "remove_files": ["pathcmd.py"],
            "effort": "S",
            "risks": ["rc rewriting must keep --dry-run"],
            "payoff": "2 → 1",
        },
    },
    {
        "id": "file-search",
        "title": "Recursive file / content search",
        "task": "Search files by content or pick one interactively",
        "kind": "variant-cluster",
        "confidence": "medium",
        "scripts": [
            script("pyrg.py", "ripgrep-like search with type presets", "merge-target", ["loguru"]),
            script("pfzf.py", "interactive fuzzy picker with preview"),
            script("pysearch.py", "search toolkit: grep/names/strings/regex/info (excluded from rewrite)"),
        ],
        "why": (
            "pyrg.py finds matches; pfzf.py lets a human pick from candidates. A single binary with "
            "an interactive mode removes the 'pipe rg into fzf' glue users have to write."
        ),
        "merge": {
            "suggested_name": "pyrg",
            "strategy": "Add --interactive that hands results to the existing TUI; keep pysearch as legacy.",
            "cli_example": "pyrg -t py 'TODO' --interactive ; pyrg --fuzzy < files.txt",
            "flags": [
                {"flag": "-i, --interactive / --fuzzy", "replaces": "pfzf.py standalone entry point"},
                {"flag": "--preview-cmd", "replaces": "pfzf preview wiring"},
                {"flag": "-t, --type / --glob", "replaces": "pyrg presets"},
            ],
            "remove_files": ["pfzf.py"],
            "effort": "M",
            "risks": ["pyrg performance relies on a fast walker; keep the picker lazy"],
            "payoff": "2 → 1; pysearch.py stays as the reference implementation",
        },
    },
    {
        "id": "scaffolding",
        "title": "Project scaffolding",
        "task": "Create a new project skeleton (Python or Rust) and package metadata",
        "kind": "variant-cluster",
        "confidence": "high",
        "scripts": [
            script("pydevtools.py", "scaffold/package Python or Rust projects", "merge-target",
                   ["tomli", "cargo"]),
            script("pyproj.py", "scaffold Python project + create GitHub repo", backends=["dotenv"]),
        ],
        "why": "Same rendering problem: emit pyproject/__init__/package files from a template + user data.",
        "merge": {
            "suggested_name": "pyproj",
            "strategy": "One generator; language/layout are flags, GitHub publishing is opt-in.",
            "cli_example": "pyproj new mypkg --layout src --rust --github ; pyproj cargo-toml .",
            "flags": [
                {"flag": "--layout", "values": ["src", "flat", "package", "single-file"],
                 "replaces": "pyproj render_*_files + pydevtools renderers"},
                {"flag": "--rust / --cargo", "replaces": "pydevtools cargo toml support"},
                {"flag": "--github", "replaces": "pyproj repo creation/push"},
                {"flag": "--publish / --meta", "replaces": "pydevtools packaging reads"},
            ],
            "remove_files": ["pydevtools.py"],
            "effort": "M",
            "risks": ["pyproj touches the network/credentials; keep that path explicit and optional"],
            "payoff": "2 → 1",
        },
    },
    {
        "id": "ast-extraction",
        "title": "Python source extraction",
        "task": "Pull definitions or documentation out of Python files",
        "kind": "variant-cluster",
        "confidence": "medium",
        "scripts": [
            script("pyextract.py", "extract defs/classes/constants to splits, sqlite, archive", "merge-target",
                   ["libcst", "tree_sitter"]),
            script("pydocr.py", "extract docstrings to Markdown"),
        ],
        "why": "Both parse the same AST/libcst trees and serialise a projection of them; only the output differs.",
        "merge": {
            "suggested_name": "pyextract",
            "strategy": "Output target is a flag; docs become one of the writers.",
            "cli_example": "pyextract --what docs --format md src/ ; pyextract --what defs --format sqlite src/",
            "flags": [
                {"flag": "--what", "values": ["defs", "classes", "constants", "docs", "imports"],
                 "replaces": "pyextract entity kinds + pydocr"},
                {"flag": "--format", "values": ["files", "md", "json", "sqlite"],
                 "replaces": "pyextract writers + pydocr markdown writer"},
                {"flag": "--engine", "values": ["ast", "libcst", "tree-sitter"],
                 "replaces": "three extractors already present"},
            ],
            "remove_files": ["pydocr.py"],
            "effort": "M",
            "risks": ["docstring formatting is opinionated; keep the md template configurable"],
            "payoff": "2 → 1",
        },
    },
    {
        "id": "web-docs",
        "title": "Web / Markdown asset handling",
        "task": "Fetch, inline or validate web and Markdown assets",
        "kind": "category",
        "confidence": "medium",
        "scripts": [
            script("pymonolith.py", "inline CSS/JS/images into one HTML file", "keep"),
            script("pylychee.py", "check and fix Markdown/HTML image links", "keep"),
        ],
        "why": (
            "Both resolve URLs against a base document and rewrite asset references — the shared "
            "piece is URL resolution and content-type guessing, not the CLI."
        ),
        "merge": {
            "suggested_name": None,
            "strategy": "Keep separate; extract the shared URL/mime helpers into one module.",
            "cli_example": None,
            "flags": [],
            "remove_files": [],
            "effort": "S",
            "risks": ["premature CLI merge would bloat both tools"],
            "payoff": "shared helper only (~60 lines deduplicated)",
        },
    },
    {
        "id": "tui-shared",
        "title": "Shared terminal-UI code",
        "task": "Cross-cutting: raw-mode key handling, terminal sizing and ANSI rendering",
        "kind": "shared-library",
        "confidence": "high",
        "overlaps": True,
        "scripts": [
            script("pfzf.py", "key decoding + preview pane", "keep", ["termios", "tty", "select"]),
            script("pygdu.py", "disk-usage browser", "keep", ["termios", "tty"]),
            script("pyrgb.py", "colour chart", "keep", ["termios", "tty", "select"]),
            script("pdfv.py", "PDF text viewer", "keep", ["termios", "tty"]),
            script("pdfv2.py", "PDF image viewer", "keep", ["termios", "tty", "select"]),
            script("pdfviewer.py", "PDF image viewer", "keep", ["termios", "tty"]),
            script("pycatimg.py", "image renderer", "keep"),
            script("pychafa.py", "image renderer", "keep"),
        ],
        "why": (
            "Eight scripts each hand-roll the same raw-mode key loop and terminal-size probe; the "
            "escape sequences are duplicated in at least four of them."
        ),
        "merge": {
            "suggested_name": "tui.py (shared module)",
            "strategy": "Do NOT merge CLIs; extract keys/term/ansi into a common module.",
            "cli_example": None,
            "flags": [],
            "remove_files": [],
            "effort": "M",
            "risks": ["vendoring a module couples scripts that are currently standalone"],
            "payoff": "~200 duplicated lines removed; one place to fix key decoding",
        },
    },
    {
        "id": "linter-wrappers",
        "title": "External linter wrappers",
        "task": "Cross-cutting: run a linter over a file set in parallel",
        "kind": "shared-library",
        "confidence": "medium",
        "scripts": [
            script("pyruf.py", "ruff wrapper incl. extensionless scripts", "keep"),
            script("pycppcheck.py", "clang -fsyntax-only wrapper", "keep", ["clang"]),
        ],
        "why": "Same shape: collect files by extension, run a binary, map output back to files.",
        "merge": {
            "suggested_name": None,
            "strategy": "Keep separate; share the file-collection + process-pool helper.",
            "cli_example": None,
            "flags": [],
            "remove_files": [],
            "effort": "S",
            "risks": [],
            "payoff": "small; only worth it if a third linter wrapper appears",
        },
    },
]

STANDALONE: list[dict[str, str]] = [
    {"path": "plotter.py", "why": "Standalone matplotlib plotting utility; nothing else plots functions."},
    {"path": "pyhead.py", "why": "10-line head(1) helper; too small to absorb, could become `pyfslint --head`."},
    {"path": "pysplit.py", "why": "Standalone split(1); nothing else slices one file into N parts."},
    {"path": "pyqalc.py", "why": "Standalone calculator; no overlap in the set."},
    {"path": "pyjcal.py", "why": "Standalone Jalali calendar; no overlap in the set."},
    {"path": "pyshuf.py", "why": "Standalone line shuffler with its own RNG strategies."},
    {"path": "pytokei.py", "why": "Standalone LOC counter; overlaps only conceptually with pydup."},
    {"path": "pyrun.py", "why": "Standalone test/script runner with a summary table."},
    {"path": "pyocr.py", "why": "Standalone OCR binding port; no other OCR script."},
    {"path": "pyxsrt.py", "why": "Standalone subtitle extractor (ffmpeg/ffprobe)."},
    {"path": "pycurl_downloader.py", "why": "Generic URL-list downloader; could fold into pypidl as --url-list."},
    {"path": "pygdu.py", "why": "Standalone disk-usage TUI (see tui-shared for the shared code)."},
    {"path": "pkgfetch.py", "why": "PEP 503/691 index client used as a library; could become pkgtool's resolver."},
    {"path": "prompt-reformatter.py", "why": "Text reflow; could become `pyfmt --wrap 80`."},
    {"path": "py7zmax.py", "why": "Excluded from the rewrite; the only archiver."},]


RECIPES: list[dict[str, object]] = [
    {"intent": "convert a pickle file to JSON", "group": "pickle-json",
     "use": ["pklr.py"], "also": ["pkl2json.py", "pkltojson.py"]},
    {"intent": "convert a Python script to a Jupyter notebook", "group": "notebook-convert",
     "use": ["py2ipynb.py"], "also": ["pytoipynb.py"]},
    {"intent": "convert line endings (CRLF <-> LF) or fix BOM/final newline", "group": "text-hygiene",
     "use": ["pyfslint.py"], "also": ["pydos2unix.py", "pydos2unix2.py"]},
    {"intent": "extract text from a PDF", "group": "pdf-text",
     "use": ["pdftotext.py"], "also": ["pdf2text.py", "pdf2page.py", "perpage.py", "ppage.py"]},
    {"intent": "render PDF pages to PNG/JPG", "group": "pdf-render",
     "use": ["pdf2img.py"], "also": ["pdf2png.py"]},
    {"intent": "merge, split or compress PDFs", "group": "pdf-toolkit",
     "use": ["pdftoolz.py"], "also": ["pdftools2.py", "pdftools.py"]},
    {"intent": "read a PDF in the terminal", "group": "pdf-viewer",
     "use": ["pdfview.py"], "also": ["pdfv.py", "pdfv2.py", "pdfviewer.py"]},
    {"intent": "display an image in the terminal", "group": "image-terminal",
     "use": ["pycatimg.py"], "also": ["pychafa.py", "pyrgb.py"]},
    {"intent": "find duplicate or near-duplicate Python code", "group": "duplicate-detection",
     "use": ["pydup.py"], "also": ["python_duplicate_finder.py", "pydeduper.py", "pysim.py"]},
    {"intent": "download packages or wheels from PyPI or a mirror", "group": "pypi-download",
     "use": ["pypidl.py"], "also": ["pyget.py", "pipget.py", "python_downloader.py",
                                    "pip_downloader.py", "pypi_user_pkgs.py"]},
    {"intent": "search for a package by name/description", "group": "pypi-search",
     "use": ["psearch.py"], "also": ["pss.py"]},
    {"intent": "get download statistics for a package", "group": "pypi-stats",
     "use": ["pypi_stats.py"], "also": ["pypistats.py"]},
    {"intent": "check installed packages for updates / import errors", "group": "pypi-health",
     "use": ["pypi_update_check.py"], "also": ["pysanity-check.py", "pip_sanity_check.py"]},
    {"intent": "audit site-packages (duplicates, versions, purity)", "group": "installed-inspection",
     "use": ["pkg_insoector.py"], "also": ["purepy.py", "pkgres.py"]},
    {"intent": "install, copy, repack or clean installed packages", "group": "pkg-maintenance",
     "use": ["pkg_tools.py"], "also": ["pkgtools2.py", "pkgtools.py", "pyzpkg.py", "pyco.py"]},
    {"intent": "uninstall or reinstall installed packages", "group": "pip-ops",
     "use": ["piu.py"], "also": ["pip_reinstaller.py"]},
    {"intent": "strip comments, docstrings or unused imports", "group": "python-source-cleanup",
     "use": ["pyclean.py"], "also": ["py_clean.py", "pycleaner.py"]},
    {"intent": "merge a multi-module package into one file", "group": "package-merge",
     "use": ["py_refactor.py"], "also": ["prefactor.py", "py_refact.py"]},
    {"intent": "modernise Python 2 / legacy syntax", "group": "python-modernize",
     "use": ["py3migrate.py"], "also": ["pyenhance.py", "py_refact.py"]},
    {"intent": "rename files with patterns, templates or case changes", "group": "file-renamer",
     "use": ["pnr2.py"], "also": ["pnr.py"]},
    {"intent": "diff two files (lines or bytecode)", "group": "file-diff",
     "use": ["pydiff.py"], "also": ["pdiff.py", "pdif.py"]},
    {"intent": "format or minify HTML/CSS/JS/JSON", "group": "formatting",
     "use": ["pyfmt.py"], "also": ["pret2.py", "pret.py", "pyterser.py"]},
    {"intent": "optimise PNG or SVG assets", "group": "optimizers",
     "use": ["pngq.py"], "also": ["pysvg.py"]},
    {"intent": "search files by content", "group": "file-search",
     "use": ["pyrg.py"], "also": ["pfzf.py", "pysearch.py"]},
    {"intent": "inspect or fix PATH problems", "group": "path-tools",
     "use": ["pathdiag.py"], "also": ["pathcmd.py"]},
    {"intent": "scaffold a new project (pyproject, package, GitHub repo)", "group": "scaffolding",
     "use": ["pyproj.py"], "also": ["pydevtools.py"]},
    {"intent": "extract definitions or docstrings from Python sources", "group": "ast-extraction",
     "use": ["pyextract.py"], "also": ["pydocr.py"]},
    {"intent": "view a file with syntax highlighting", "group": "bat-clone",
     "use": ["pbat.py"], "also": ["pybat.py"]},
    {"intent": "count lines of code by language", "group": None,
     "use": ["pytokei.py"], "also": []},
    {"intent": "run every script in a directory and summarise results", "group": None,
     "use": ["pyrun.py"], "also": []},
    {"intent": "OCR an image", "group": None, "use": ["pyocr.py"], "also": []},
    {"intent": "extract subtitles from a video", "group": None, "use": ["pyxsrt.py"], "also": []},
    {"intent": "calculate expressions / convert units", "group": None, "use": ["pyqalc.py"], "also": []},
    {"intent": "print a Jalali calendar or convert dates", "group": None, "use": ["pyjcal.py"], "also": []},
    {"intent": "shuffle lines of a file", "group": None, "use": ["pyshuf.py"], "also": []},
    {"intent": "split a text file into N parts", "group": None, "use": ["pysplit.py"], "also": []},
    {"intent": "plot a function", "group": None, "use": ["plotter.py"], "also": []},
    {"intent": "inline a webpage into a single HTML file", "group": "web-docs",
     "use": ["pymonolith.py"], "also": ["pylychee.py"]},
    {"intent": "download URLs from a list", "group": None,
     "use": ["pycurl_downloader.py"], "also": ["pypidl.py"]},
    {"intent": "syntax-check C/C++ files", "group": "linter-wrappers",
     "use": ["pycppcheck.py"], "also": ["pyruf.py"]},
]


def all_scripts() -> list[str]:
    return sorted(path.name for path in REPO_ROOT.glob("p*.py") if path.is_file())


def grouped_paths() -> dict[str, str]:
    """Map every script that belongs to a (non-overlapping) group to that group id."""
    owners: dict[str, str] = {}
    for group in GROUPS:
        if group.get("overlaps"):
            continue
        for entry in group["scripts"]:
            path = entry["path"]
            if path in owners:
                raise SystemExit(f"{path} listed twice: {owners[path]} and {group['id']}")
            owners[path] = group["id"]
    return owners


def standalone_paths() -> set[str]:
    return {entry["path"] for entry in STANDALONE}


def validate() -> list[str]:
    problems: list[str] = []
    scripts = set(all_scripts())
    grouped = grouped_paths()
    standalone = standalone_paths()
    missing = scripts - set(grouped) - standalone
    extra = (set(grouped) | standalone) - scripts
    if missing:
        problems.append(f"scripts not categorised: {sorted(missing)}")
    if extra:
        problems.append(f"unknown scripts in report: {sorted(extra)}")
    both = set(grouped) & standalone
    if both:
        problems.append(f"scripts both grouped and standalone: {sorted(both)}")
    unknown_purpose = scripts - set(PURPOSES)
    if unknown_purpose:
        problems.append(f"scripts without a purpose line: {sorted(unknown_purpose)}")
    group_ids = {group["id"] for group in GROUPS}
    for recipe in RECIPES:
        if recipe["group"] is not None and recipe["group"] not in group_ids:
            problems.append(f"recipe '{recipe['intent']}' points at unknown group {recipe['group']}")
        for path in [*recipe["use"], *recipe["also"]]:
            if path not in scripts:
                problems.append(f"recipe '{recipe['intent']}' references unknown script {path}")
    return problems


def group_evidence(group: dict[str, object], pairs: list[dict[str, object]]) -> dict[str, object]:
    members = {entry["path"] for entry in group["scripts"]}
    internal = [
        pair for pair in pairs if pair["left"] in members and pair["right"] in members
    ]
    internal.sort(key=lambda pair: pair["score"], reverse=True)
    if not internal:
        return {"best_pair": None, "pairs": [], "interpretation": "single-member group"}
    best = float(internal[0]["score"])
    kind = group["kind"]
    if kind == "shared-library":
        interpretation = (
            f"best measured pair {best:.2f}: members duplicate key/TUI code, which is what the "
            "shared module should absorb (their CLIs stay separate)"
        )
    elif best >= 0.45:
        interpretation = f"best measured pair {best:.2f}: structurally close, merge is mostly deletion"
    else:
        interpretation = (
            f"best measured pair {best:.2f}: low by design, these use different libraries and "
            "identifier names for the same job; the grouping is semantic, not textual"
        )
    return {"best_pair": internal[0], "pairs": internal[:6], "interpretation": interpretation}


def build() -> dict[str, object]:
    problems = validate()
    if problems:
        raise SystemExit("report is inconsistent:\n  " + "\n  ".join(problems))
    features, pairs = script_similarity.analyse(REPO_ROOT, "p*.py")
    grouped = grouped_paths()
    standalone = standalone_paths()
    groups: list[dict[str, object]] = []
    for group in GROUPS:
        entry = dict(group)
        entry["evidence"] = group_evidence(group, pairs)
        entry["scripts"] = [dict(item, purpose=PURPOSES[item["path"]]) for item in group["scripts"]]
        groups.append(entry)
    merge_groups = [group for group in groups if group["merge"]["remove_files"]]
    removals = sorted({path for group in merge_groups for path in group["merge"]["remove_files"]})
    index: dict[str, dict[str, object]] = {}
    for name in all_scripts():
        group_id = grouped.get(name)
        verdict = "standalone"
        group = next((item for item in GROUPS if item["id"] == group_id), None)
        if group is not None:
            entry = next(item for item in group["scripts"] if item["path"] == name)
            verdict = entry["verdict"]
        index[name] = {
            "purpose": PURPOSES[name],
            "group": group_id,
            "group_title": group["title"] if group else None,
            "verdict": "delete-after-merge" if name in removals else verdict,
            "excluded_from_rewrite": name in EXCLUDED_FROM_REWRITE,
            "lines": features[name].lines if name in features else None,
            "third_party": features[name].summary()["third_party"] if name in features else [],
        }
    by_task = {group["id"]: group["task"] for group in groups}
    report = {
        "schema_version": 1,
        "generated": date.today().isoformat(),
        "repository": "i4147/bin",
        "commit": COMMIT,
        "source_of_scripts": "i4147/bin_oct@3d93c5d (copied in, then rewritten in place)",
        "scope": {
            "scripts_total": len(all_scripts()),
            "scripts_in_groups": len(grouped),
            "scripts_standalone": len(standalone),
            "groups": len(groups),
            "mergeable_groups": len(merge_groups),
            "files_removable_if_all_merges_land": len(removals),
            "excluded_from_rewrite": EXCLUDED_FROM_REWRITE,
        },
        "how_to_read": {
            "kind": {
                "near-duplicate": "measured score >= 0.5: same code with small differences",
                "duplicate-lineage": "one script is an ancestor/sibling of another",
                "variant-cluster": "same job, different technique or backend",
                "toolkit-overlap": "subcommand/feature overlap; merge at the CLI level",
                "category": "same domain, merging NOT recommended",
                "shared-library": "cross-cutting code to extract, CLIs stay separate",
            },
            "verdict": {
                "merge-target": "keep this file as the base of the merged tool",
                "merge": "fold into the merge target, then delete",
                "keep": "stays as-is",
            },
            "evidence": "scores come from arena/script_similarity.py; weights are in similarity_model",
            "effort": {"S": "under an hour", "M": "half a day", "L": "a day or more"},
        },
        "similarity_model": {
            "weights": script_similarity.WEIGHTS,
            "signals": {
                "shingles": "3-gram Jaccard over the normalised token stream",
                "tokens": "Jaccard over identifier names",
                "definitions": "Jaccard over function/class names",
                "imports": "Jaccard over imported modules",
                "flags": "Jaccard over --cli options",
            },
            "caveat": (
                "Scores rank candidates; they are not the grouping criterion. Scripts doing the same "
                "job with different libraries can score low (different identifiers) while unrelated "
                "small scripts score ~0.15 on shared stdlib imports."
            ),
            "recompute": "python3 arena/script_similarity.py --top 40",
        },
        "by_task": by_task,
        "recipes": [
            dict(
                recipe,
                group_title=next(
                    (group["title"] for group in groups if group["id"] == recipe["group"]), None
                ),
            )
            for recipe in RECIPES
        ],
        "groups": groups,
        "standalone": [dict(item, purpose=PURPOSES[item["path"]]) for item in STANDALONE],
        "index": index,
        "merge_plan": build_plan(groups),
        "measured_duplicates": [
            pair for pair in pairs if pair["score"] >= 0.45
        ],
    }
    return report


def build_plan(groups: list[dict[str, object]]) -> list[dict[str, object]]:
    order = {"S": 0, "M": 1, "L": 2}
    steps: list[dict[str, object]] = []
    for group in groups:
        merge = group["merge"]
        if not merge["remove_files"]:
            continue
        steps.append(
            {
                "group": group["id"],
                "suggested_name": merge["suggested_name"],
                "effort": merge["effort"],
                "removes": merge["remove_files"],
                "flags_to_add": [flag["flag"] for flag in merge["flags"]],
                "confidence": group["confidence"],
            }
        )
    steps.sort(key=lambda step: (order.get(str(step["effort"]), 3), -len(step["removes"])))
    for position, step in enumerate(steps, start=1):
        step["order"] = position
    return steps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build arena/script_similarity.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    parser.add_argument("--check", action="store_true", help="validate coverage and exit")
    args = parser.parse_args(argv)
    if args.check:
        problems = validate()
        for problem in problems:
            print(f"FAIL {problem}")
        print("coverage ok" if not problems else "coverage broken")
        return 1 if problems else 0
    report = build()
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.stdout:
        print(payload)
        return 0
    args.output.write_text(payload + "\n", encoding="utf-8")
    scope = report["scope"]
    print(
        f"wrote {args.output}\n"
        f"  {scope['scripts_total']} scripts, {scope['groups']} groups, "
        f"{scope['mergeable_groups']} mergeable, {scope['files_removable_if_all_merges_land']} removable files"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
