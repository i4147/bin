# cleaners — superseded language comment strippers

These are the per-language comment strippers that `../stripper.py` replaces. They
are kept for reference and are **not** the entry point any more; nine of them
overlapped heavily and the rest were older drafts of the same idea.

| file | language | backend | replaced by |
| --- | --- | --- | --- |
| `clean-bash.py` | bash / sh / zsh | tree-sitter | `stripper.py --bash` |
| `clean-cpp.py` | C / C++ | tree-sitter (c + cpp grammars) | `stripper.py --c --cpp` |
| `clean-css.py` | CSS | tree-sitter, tinycss2, csscompressor | `stripper.py --css` |
| `clean-html.py` | HTML (+ embedded CSS/JS) | tree-sitter, htmlmin | `stripper.py --html` |
| `clean-js.py` | JavaScript | tree-sitter, rjsmin, jsmin | `stripper.py --js` |
| `clean-lua.py` | Lua | tree-sitter, luaparse | `stripper.py --lua` |
| `clean-rs.py` | Rust | tree-sitter | `stripper.py --rs` |
| `clean-toml.py` | TOML | tree-sitter | `stripper.py --toml` |
| `clean-ts.py` | TypeScript | tree-sitter, rjsmin, jsmin | `stripper.py --ts --tsx` |
| `cleants.py` | 22 languages, table-driven | tree-sitter | `stripper.py -a` |
| `cleants2.py` | 60+ extensions, table-driven | tree-sitter | `stripper.py -a` |
| `cleanjsts.py` | JS/TS (older draft) | tree-sitter | `stripper.py --js --ts` |
| `cleanrs.py` | Rust (older draft) | tree-sitter, regex fallback | `stripper.py --rs` |
| `cleanlua.py` | Lua (older draft) | tree-sitter | `stripper.py --lua` |
| `cleanbash.py` | bash (older draft) | tree-sitter | `stripper.py --bash` |
| `cleanvim.py` | Vim script | tree-sitter | `stripper.py --vim` |

## Why these were consolidated

Every script re-implemented the same pipeline: collect files by extension, parse,
drop comment nodes, collapse blank lines, write under a process pool. The pairs
that did literally the same job: `cleanrs.py`/`clean-rs.py`, `cleanlua.py`/
`clean-lua.py`, `cleanbash.py`/`clean-bash.py`, `cleanjsts.py`/`clean-js.py`,
`cleants.py`/`cleants2.py`. Only the grammar import and the extension list
differed, which is now a table in `../stripper.py` plus a `--<lang>` flag.

## What the old scripts did that the consolidation keeps

- tree-sitter comment-node removal by byte range, with comment-only lines dropped
  and inline comments trimmed without leaving trailing whitespace;
- blank-line collapsing (`\n\n\n` -> `\n\n`), now opt-out with `--keep-blank-lines`;
- `--diff`, `--dry-run`, `--backup`, `-j/--workers` (the old flags were
  inconsistent: some scripts used `-n` for dry-run, others `--dry-run`);
- skip lists for `.git`, `.venv`, `node_modules`, `__pycache__`, `dist`, `build`.

## What was dropped and why

- **Minifier backends** (`rjsmin`, `jsmin`, `htmlmin`, `csscompressor`,
  `tinycss2`, `luaparse`): they rewrite more than comments, so they do not belong
  behind a `--backend` flag on a comment stripper. `stripper.py` keeps two
  backends that produce the same output by construction: `tree-sitter` and
  `regex`.
- **HTML embedded CSS/JS stripping**: `stripper.py --html` removes HTML comments
  only. Run `--css --js` over the same tree, or use `-a`, to catch comments inside
  `<style>`/`<script>` blocks.
- The extensionless twins of these files (`clean-rs`, `cleants`, ...) were
  byte-identical copies; only the `.py` versions were carried over.

## Python strippers stay at the repo root

Per the split, Python is not part of this folder:

- `../cleanpy.py` — strips comments and docstrings (libcst)
- `../clean_type_annotations.py` — strips type annotations (tokenize)
- `../pyclean.py`, `../py_clean.py`, `../pycleaner.py` — the older p* cleaners

`stripper.py` deliberately has no `--py` flag so that Python keeps its AST-aware
tools; it prints a pointer to `cleanpy.py` in its help.

## Not included

These share the `clean*` prefix but do not strip language comments:
`cleaner.py` and `cleanlog.py` (ANSI escapes in text/logs), `cleanhistory.py`
(bash history dedupe), `clean_version.py` (pip-freeze clean-up), `cleandic.py`
(JSON dictionary clean-up).
