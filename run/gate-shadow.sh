#!/usr/bin/env bash
# Run the caller's gate registry through run/gate.py in shadow and record the
# verdict, without ever gating. README.md, "The shadow job", owns the design.
#
#   GATES_DIR=<gates clone> SHADOW_STORE=<dir outside the tree> [SHADOW_BASE=<sha>] run/gate-shadow.sh
#
# Runs with the caller's checkout as the working directory. The store must lie
# outside that tree, because `gate.py run` refuses a checkout with untracked files.
# The exit status is 0 whatever the gates decided: the verdict is the
# `verdict=` line of $SHADOW_STORE/shadow-verdict.txt, read later against the
# caller's own check jobs, never by the job's status.
set -uo pipefail

: "${GATES_DIR:?GATES_DIR is unset}"
: "${SHADOW_STORE:?SHADOW_STORE is unset}"
# gate.py's NO_BASE: with no base to compare with, every gate is selected.
base=${SHADOW_BASE:-0000000000000000000000000000000000000000}
# The caller's registry rows (README.md) read the base as BASE_SHA, which the
# bootstrap sets only in its lock step, so the rows get it from here.
export BASE_SHA="$base"
gate="$GATES_DIR/run/gate.py"
mkdir -p "$SHADOW_STORE"

run_status=0
python3 "$gate" run --base "$base" --store "$SHADOW_STORE" >"$SHADOW_STORE/run.txt" 2>&1 || run_status=$?
fold_status=0
python3 "$gate" fold --base "$base" --store "$SHADOW_STORE" >"$SHADOW_STORE/fold.txt" 2>&1 || fold_status=$?
cat "$SHADOW_STORE/run.txt" "$SHADOW_STORE/fold.txt"

# A shadow row that failed folds as `shadow`, not `FAIL`, so the verdict reads
# the fold's lines rather than its status: either kind of red is a disagreement
# to compare, and only a fold that could not decide is something else.
if [ "$fold_status" -gt 1 ] || [ "$run_status" -gt 1 ]; then
    verdict=undecided
elif grep -q '^FAIL ' "$SHADOW_STORE/fold.txt"; then
    verdict=refused
elif grep -q '^shadow ' "$SHADOW_STORE/fold.txt"; then
    verdict=shadow-red
else
    verdict=clear
fi
{
    printf 'head=%s\n' "$(git rev-parse HEAD 2>/dev/null || echo unknown)"
    printf 'base=%s\n' "$base"
    printf 'run_status=%s\nfold_status=%s\n' "$run_status" "$fold_status"
    printf 'verdict=%s\n' "$verdict"
} >"$SHADOW_STORE/shadow-verdict.txt"

cat "$SHADOW_STORE/shadow-verdict.txt"
if [ "$verdict" != clear ]; then
    echo "::warning title=gate shadow::verdict=$verdict; recorded, not gating."
fi
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
        printf '### Gate runner (shadow): %s\n\n```\n' "$verdict"
        cat "$SHADOW_STORE/fold.txt"
        printf '```\n'
    } >>"$GITHUB_STEP_SUMMARY"
fi
exit 0
