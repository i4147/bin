#!/usr/bin/env bash
# compare_cli.sh - compare two versions of the same script from the command line.
#
# Runs `--help` and a bare invocation in throwaway directories and diffs
# exit code + stdout. This is the manual version of what arena/verify_pfiles.py
# does for the whole set.
#
# Usage:
#   ./arena/compare_cli.sh ORIG_DIR NEW_DIR                # every p*.py present in both
#   ./arena/compare_cli.sh ORIG_DIR NEW_DIR pdfv.py pyget.py
#
# Exit status: 0 when every script matches, 1 otherwise.
set -uo pipefail

if [[ $# -lt 2 ]]; then
    sed -n '3,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
fi

ORIG_DIR="$(cd "$1" && pwd)"
NEW_DIR="$(cd "$2" && pwd)"
shift 2
PYTHON="${PYTHON:-python3}"

if [[ $# -gt 0 ]]; then
    scripts=("$@")
else
    mapfile -t scripts < <(cd "$ORIG_DIR" && ls -1 p*.py 2>/dev/null)
fi

fail=0
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

run() {  # run <script> <args...>; captures rc and stdout in files
    local script="$1"
    shift
    ( cd "$workdir" && timeout 25 "$PYTHON" "$script" "$@" \
        > "$workdir/out" 2> "$workdir/err" </dev/null; echo $? > "$workdir/rc" )
}

compare() {
    local original="$ORIG_DIR/$1"
    local rewritten="$NEW_DIR/$1"
    local label="$2"
    run "$original" "$@"
    local rc_a out_a
    rc_a=$(cat "$workdir/rc"); out_a=$(cat "$workdir/out")
    run "$rewritten" "$@"
    local rc_b out_b
    rc_b=$(cat "$workdir/rc"); out_b=$(cat "$workdir/out")
    if [[ "$rc_a" == "$rc_b" && "$out_a" == "$out_b" ]]; then
        printf '  ok    %-28s %s (rc=%s)\n' "$1" "$label" "$rc_a"
    else
        printf '  DIFF  %-28s %s (rc %s -> %s)\n' "$1" "$label" "$rc_a" "$rc_b"
        diff <(printf '%s\n' "$out_a") <(printf '%s\n' "$out_b") | head -10
        fail=1
    fi
}

for script in "${scripts[@]}"; do
    [[ -f "$ORIG_DIR/$script" && -f "$NEW_DIR/$script" ]] || {
        printf '  SKIP  %s (missing in one of the directories)\n' "$script"
        continue
    }
    echo "$script"
    compare "$script" "--help" --help
    compare "$script" "bare"
done

if [[ "$fail" -eq 0 ]]; then
    echo
    echo "all compared scripts match"
else
    echo
    echo "differences found" >&2
fi
exit "$fail"
