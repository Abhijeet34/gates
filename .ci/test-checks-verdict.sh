#!/usr/bin/env bash
# Regression tests for .ci/checks-verdict.sh, the rule behind the one status
# the branch ruleset requires.
#
# The case that matters is all_skipped. The aggregate used to accept
# "success or skipped" for every job, so a run in which nothing executed
# reported green. That is not theoretical: on 2026-08-03 a pull request with a
# genuinely failing `python` suite was re-run with every job forced to skip,
# and its mergeStateStatus went from BLOCKED to CLEAN. A skipped job satisfies
# a required status check, so the aggregate has to know which skips are
# legitimate.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

failed=0

# $1 name, $2 expected exit code, $3 needs JSON
expect() {
    local name="$1" want="$2" json="$3" got
    printf '%s' "$json" | ./checks-verdict.sh >/dev/null 2>&1
    got=$?
    if ((got == want)); then
        printf 'ok   %s\n' "$name"
    else
        printf 'FAIL %s: expected exit %d, got %d\n' "$name" "$want" "$got"
        failed=1
    fi
}

# Builds a needs object from `job=result` pairs.
needs() {
    local out="{" pair
    for pair in "$@"; do
        out+="\"${pair%%=*}\":{\"result\":\"${pair##*=}\"},"
    done
    printf '%s}' "${out%,}"
}

ALL_SUITES=(shell=success python=success secrets=success)

# Every job runs on every pull request now that the conditional macOS job is
# gone, so this is the only shape a green run takes.
expect 'pull request, every job ran' 0 "$(needs "${ALL_SUITES[@]}")"

# The false green this file exists to pin.
expect 'all skipped' 1 \
    "$(needs shell=skipped python=skipped secrets=skipped)"

# No suite has a legitimate skip any more, so one skipping means the gate
# silently stopped covering it.
expect 'one suite skipped' 1 \
    "$(needs shell=success python=skipped secrets=success)"

expect 'one suite failed' 1 \
    "$(needs shell=success python=failure secrets=success)"

expect 'one suite cancelled' 1 \
    "$(needs shell=success python=success secrets=cancelled)"

# `all` is vacuously true over nothing, so no dependencies must fail closed.
expect 'no dependencies' 1 '{}'

# The rule above only gates the jobs `checks` actually depends on. ci.yml says
# in a comment that every job must be listed in `needs`, and nothing enforced
# it: adding a job and forgetting that line leaves it running, reporting, and
# gating nothing, which looks identical on the pull request to a job that gates.
# Cheaper to assert than to notice.
WORKFLOW=../.github/workflows/ci.yml
if ! command -v yq >/dev/null 2>&1; then
    printf 'FAIL needs-completeness: yq is not installed (brew install yq)\n'
    failed=1
elif [ ! -r "$WORKFLOW" ]; then
    printf 'FAIL needs-completeness: cannot read %s\n' "$WORKFLOW"
    failed=1
else
    # Sorted, so the comparison is set equality and not declaration order.
    declared=$(yq -r '.jobs | keys | .[] | select(. != "checks")' "$WORKFLOW" | sort)
    gated=$(yq -r '.jobs.checks.needs[]' "$WORKFLOW" | sort)
    # An empty read means the parse failed, not that the file is fine.
    if [ -z "$declared" ] || [ -z "$gated" ]; then
        printf 'FAIL needs-completeness: read no jobs from %s\n' "$WORKFLOW"
        failed=1
    elif [ "$declared" != "$gated" ]; then
        printf 'FAIL needs-completeness: checks.needs does not cover every job\n'
        diff <(printf '%s\n' "$declared") <(printf '%s\n' "$gated") | sed 's/^/     /'
        failed=1
    else
        printf 'ok   every job in ci.yml is listed in checks.needs\n'
    fi
fi

if ((failed)); then
    printf '\nchecks-verdict regression tests FAILED\n' >&2
    exit 1
fi
printf '\nchecks-verdict regression tests passed\n'
