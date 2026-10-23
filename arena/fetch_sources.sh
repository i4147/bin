#!/usr/bin/env bash
# fetch_sources.sh - prepare an original/rewritten working pair for a rerun.
#
# i4147/bin only gained the scripts through this session, so the pristine copies
# come from i4147/bin_oct. This lays out:
#
#   $DEST/vendor/       shallow clone of i4147/bin_oct
#   $DEST/orig/         the pristine p*.py
#   $DEST/out/          empty directory for rewrite output
#
# Usage:
#   ./arena/fetch_sources.sh [DEST]        # DEST defaults to ../scratch
#
# Then:
#   python3 arena/refactor_pfiles.py $DEST/orig $DEST/out --exclude pysearch.py py7zmax.py
#   python3 arena/verify_pfiles.py   $DEST/orig $DEST/out
set -euo pipefail

DEST="${1:-$(cd "$(dirname "$0")/.." && pwd)/../scratch}"
VENDOR="$DEST/vendor"
ORIG="$DEST/orig"
OUT="$DEST/out"
URL="https://github.com/i4147/bin_oct.git"
PIN="3d93c5d"

mkdir -p "$DEST"

if [[ -d "$VENDOR/.git" ]]; then
    echo "reusing $VENDOR"
else
    echo "cloning $URL"
    git clone --quiet --depth 1 "$URL" "$VENDOR"
fi

cd "$VENDOR"
current=$(git rev-parse --short HEAD)
if [[ "$current" != "$PIN" ]]; then
    echo "note: vendored tree is at $current, this session used $PIN"
    if git fetch --quiet --depth 1 origin "$PIN" 2>/dev/null; then
        git checkout --quiet "$PIN"
        echo "checked out pinned commit $PIN"
    else
        echo "warning: commit $PIN not fetchable, continuing with $current" >&2
    fi
fi
cd - >/dev/null

mkdir -p "$ORIG" "$OUT"
cp "$VENDOR"/p*.py "$ORIG"/
echo "originals: $(ls -1 "$ORIG" | wc -l) files -> $ORIG"
echo "output dir: $OUT"
