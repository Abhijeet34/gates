#!/usr/bin/env bash
# Regression tests for run/instruction-truth/size-limit.py, the check
# .github/workflows/shared-instruction-truth.yml runs beside path-truth.py.
#
# Each case builds a tree holding one instruction file of a stated size and states
# whether the check must refuse it. The line cap is pinned at its edge, 200 and 201.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

CHECK=$PWD/run/instruction-truth/size-limit.py

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

failed=0

# expect <name> <0|1> <relative path> <lines> <bytes per line> [finding]: write
# <lines> lines of <bytes per line> bytes each, then judge the verdict and the finding.
expect() {
    local name=$1 want=$2 rel=$3 lines=$4 width=$5 finding=${6:-} dir out rc
    dir=$(mktemp -d "$workdir/case.XXXXXX")
    mkdir -p "$dir/$(dirname "$rel")"
    awk -v n="$lines" -v w="$width" 'BEGIN { s = sprintf("%*s", w - 1, ""); gsub(/ /, "x", s); for (i = 0; i < n; i++) print s }' >"$dir/$rel"
    out=$(python3 "$CHECK" "$dir" 2>&1)
    rc=$?
    if [[ $rc -ne $want ]]; then
        printf 'FAIL %s: exit %s, wanted %s\n%s\n' "$name" "$rc" "$want" "$out"
        failed=1
    elif [[ -n $finding && $out != *"$finding"* ]]; then
        printf 'FAIL %s: output does not name %s\n%s\n' "$name" "$finding" "$out"
        failed=1
    else
        printf 'ok   %s\n' "$name"
    fi
}

expect 'a 201-line AGENTS.md is refused' 1 AGENTS.md 201 10 'AGENTS.md: 201 lines'
expect 'a 200-line AGENTS.md is accepted' 0 AGENTS.md 200 10
expect 'a 201-line nested CLAUDE.md is named by its path' 1 apps/desktop/CLAUDE.md 201 10 'apps/desktop/CLAUDE.md: 201 lines'
expect 'a 5,001-byte home AGENTS.md is refused' 1 home/AGENTS.md 100 51 'home/AGENTS.md: 5100 bytes'
expect 'a 5,000-byte home AGENTS.md is accepted' 0 home/AGENTS.md 100 50
expect 'the byte cap binds only a file under home' 0 docs/AGENTS.md 100 51
expect 'another file name is not judged' 0 NOTES.md 500 10

dir=$(mktemp -d "$workdir/empty.XXXXXX")
if python3 "$CHECK" "$dir" >/dev/null 2>&1; then
    printf 'ok   %s\n' 'a tree with no instruction file passes'
else
    printf 'FAIL a tree with no instruction file passes\n'
    failed=1
fi

exit "$failed"
