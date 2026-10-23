#!/usr/bin/env bash
# inspect.sh - quick look at one or more p* scripts.
#
# Default mode is the head/tail pattern used while triaging:
#   head -60 file ... tail -30 file
#
# Usage:
#   ./arena/inspect.sh pypi_stats.py              # head 60 + tail 30
#   ./arena/inspect.sh -n 120 pyocr.py            # head 120 + tail 30
#   ./arena/inspect.sh --funcs pydup.py           # classes + defs with line numbers
#   ./arena/inspect.sh --cli perpage.py           # argparse flags + description
#   ./arena/inspect.sh --imports pdfview.py       # imports, split into stdlib/third-party
#   ./arena/inspect.sh --all pyjcal.py            # whole file, numbered
#   ./arena/inspect.sh --grep 'backend' pfzf.py   # regex search with line numbers
set -uo pipefail

HEAD_LINES=60
TAIL_LINES=30
MODE="default"
PATTERN=""

usage() {
    sed -n '3,17p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--lines) HEAD_LINES="$2"; shift 2 ;;
        -h|--help) usage 0 ;;
        -a|--all) MODE="all"; shift ;;
        -f|--funcs) MODE="funcs"; shift ;;
        -c|--cli) MODE="cli"; shift ;;
        -i|--imports) MODE="imports"; shift ;;
        -g|--grep) MODE="grep"; PATTERN="${2:-}"; shift 2 ;;
        --) shift; break ;;
        -*) echo "unknown option: $1" >&2; usage 1 ;;
        *) break ;;
    esac
done

[[ $# -gt 0 ]] || usage 1

for file in "$@"; do
    [[ -f "$file" ]] || { echo "inspect: no such file: $file" >&2; continue; }
    echo "=== $file ($(wc -l < "$file") lines) ==="
    case "$MODE" in
        default)
            sed -n "1,${HEAD_LINES}p" "$file"
            total=$(wc -l < "$file")
            if [[ "$total" -gt $((HEAD_LINES + TAIL_LINES)) ]]; then
                echo "..."
                tail -n "$TAIL_LINES" "$file"
            fi
            ;;
        all)
            cat -n "$file"
            ;;
        funcs)
            grep -nE '^[[:space:]]*(class |def |@[a-z])' "$file" || echo "(no defs)"
            ;;
        cli)
            grep -nE 'ArgumentParser|add_argument|description=' "$file" || echo "(no argparse)"
            ;;
        imports)
            grep -nE '^[[:space:]]*(import |from [A-Za-z_])' "$file"
            ;;
        grep)
            grep -nE -- "$PATTERN" "$file" || echo "(no match for $PATTERN)"
            ;;
    esac
    echo
done
