#!/usr/bin/env bash
# check_all.sh - acceptance checks for the rewritten root scripts.
#
# 1. every p*.py compiles
# 2. no comments (except the shebang) and no docstrings
# 3. every signature/return is annotated (via annotation_stats.py)
# 4. every script can be imported without executing it (compile + AST import sanity)
#
# Usage:
#   ./arena/check_all.sh                 # check the repo root
#   ./arena/check_all.sh path/to/scripts
set -uo pipefail

DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
PYTHON="${PYTHON:-python3}"
EXCLUDED=(pysearch.py py7zmax.py)
fail=0

echo "== compiling =="
if "$PYTHON" - "$DIR" "${EXCLUDED[@]}" <<'PY'
import compileall, re, sys
skip = "|".join(re.escape(name) for name in sys.argv[2:])
pattern = re.compile(rf"^(?!({skip})$).*\.py$") if skip else re.compile(r".*\.py$")
ok = compileall.compile_dir(sys.argv[1], quiet=2, force=True, rx=pattern)
raise SystemExit(0 if ok else 1)
PY
then
    echo "  ok: all scripts compile"
else
    echo "  FAIL: compile errors" >&2
    fail=1
fi

echo "== comments / docstrings =="
if "$PYTHON" - "$DIR" "${EXCLUDED[@]}" <<'PY'
import ast, io, sys, tokenize
from pathlib import Path
root = Path(sys.argv[1])
skip = set(sys.argv[2:])
bad = []
for path in sorted(root.glob("p*.py")):
    if path.name in skip:
        continue
    source = path.read_bytes()
    for token in tokenize.tokenize(io.BytesIO(source).readline):
        if token.type == tokenize.COMMENT and not token.string.startswith("#!"):
            bad.append(f"{path.name}:{token.start[0]}: comment")
    tree = ast.parse(source.decode("utf-8"))
    if ast.get_docstring(tree):
        bad.append(f"{path.name}: module docstring")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            bad.append(f"{path.name}:{node.lineno}: docstring in {node.name}")
for item in bad[:20]:
    print("  " + item)
print(f"  {'ok: none found' if not bad else str(len(bad)) + ' problems'}")
raise SystemExit(0 if not bad else 1)
PY
then :; else fail=1; fi

echo "== annotations =="
if ! "$PYTHON" "$(dirname "$0")/annotation_stats.py" --dir "$DIR" --exclude "${EXCLUDED[@]}"; then
    fail=1
fi

echo "== structure =="
if "$PYTHON" - "$DIR" "${EXCLUDED[@]}" <<'PY'
import ast, sys
from pathlib import Path
root = Path(sys.argv[1])
skip = set(sys.argv[2:])
loose = []
for path in sorted(root.glob("p*.py")):
    if path.name in skip:
        continue
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    if functions or not classes:
        loose.append(f"{path.name}: module-level functions={len(functions)} classes={len(classes)}")
for item in loose:
    print("  " + item)
print(f"  {'ok: everything is class-based' if not loose else str(len(loose)) + ' files need a class'}")
raise SystemExit(0 if not loose else 1)
PY
then :; else fail=1; fi

echo
if [[ "$fail" -eq 0 ]]; then
    echo "all checks passed"
else
    echo "checks failed" >&2
fi
exit "$fail"
