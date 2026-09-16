#!/usr/bin/env bash
# Every workflow in this repository is callable by any repository once it is
# public, so three properties are enforced on every file under
# .github/workflows rather than remembered at review:
#
#   1. no trigger that runs a fork's code with this repository's token or
#      secrets: pull_request_target, workflow_run, issue_comment - matched
#      anywhere in the file, so the list form `on: [push, workflow_run]` and a
#      comment naming one are refused alike;
#   2. no self-hosted runner, which would hand a stranger's pull request a
#      machine that outlives the job;
#   3. no secret but GITHUB_TOKEN, and no `secrets: inherit`.
#
# Each property is then shown refusing a deliberately added line, so the check
# cannot pass by matching nothing.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

WORKFLOWS=.github/workflows
failed=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failed=1; }

# Prints one line per violation, `file:line: text`; prints nothing when clean.
violations() { # <workflow dir>
    local dir=$1 files
    files=$(find "$dir" -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)
    [ -n "$files" ] || { echo "$dir: no workflow files found"; return; }
    # shellcheck disable=SC2086 # word-splitting the sorted file list is the point
    grep -HnE 'pull_request_target|workflow_run|issue_comment|self-hosted' $files
    # shellcheck disable=SC2086
    grep -HnE 'secrets[[:space:]]*:[[:space:]]*inherit|secrets\[|secrets\.[A-Za-z0-9_]+' $files \
        | grep -vE 'secrets\.GITHUB_TOKEN([^A-Za-z0-9_]|$)'
    # shellcheck disable=SC2086
    grep -HnoE 'secrets\.[A-Za-z0-9_]+' $files | grep -vE ':secrets\.GITHUB_TOKEN$'
}

found=$(violations "$WORKFLOWS" | sort -u)
count=$(find "$WORKFLOWS" -type f \( -name '*.yml' -o -name '*.yaml' \) | wc -l | tr -d ' ')
if [ -z "$found" ] && [ "$count" -gt 0 ]; then
    pass "$count workflow file(s): no forbidden trigger, no self-hosted runner, no secret but GITHUB_TOKEN"
else
    fail "workflow policy violated in $WORKFLOWS ($count file(s)):"
    printf '%s\n' "$found" | sed 's/^/     /'
fi

workdir=$(mktemp -d) || exit 2
trap 'rm -rf "$workdir"' EXIT

# One mutation per shape: copy the live directory, add the line to a copy of
# ci.yml, and require the check to name it.
refuses() { # <label> <line to add>
    local label=$1 line=$2 dir="$workdir/m$RANDOM$RANDOM"
    cp -R "$WORKFLOWS" "$dir"
    printf '%s\n' "$line" >>"$dir/ci.yml"
    # Captured first: under pipefail, `grep -q` exiting at its first match
    # SIGPIPEs the producer and turns a hit into a false miss.
    local out
    out=$(violations "$dir")
    # More violations than the live tree has, rather than the added text itself,
    # because a secret is reported by name rather than by its whole line.
    if [ "$(grep -c . <<<"$out")" -gt "$(violations "$WORKFLOWS" | grep -c .)" ]; then
        pass "refuses an added $label"
    else
        fail "an added $label was not refused: $line"
    fi
}
refuses 'self-hosted runner'              '    runs-on: self-hosted'
refuses 'self-hosted runner label list'   '    runs-on: [self-hosted, linux]'
refuses 'pull_request_target trigger'     '  pull_request_target:'
refuses 'workflow_run trigger, list form' 'on: [push, workflow_run]'
refuses 'issue_comment trigger'           '  issue_comment:'
refuses 'named secret'                    '          NPM_TOKEN: ${{ secrets.NPM_TOKEN }}'
refuses 'secret beside GITHUB_TOKEN'      '          X: ${{ secrets.GITHUB_TOKEN }}${{ secrets.DEPLOY_KEY }}'
refuses 'secrets: inherit'                '    secrets: inherit'

# And the one secret that is allowed must stay allowed, or the check is
# refusing everything rather than measuring.
dir="$workdir/allowed"
cp -R "$WORKFLOWS" "$dir"
printf '%s\n' '          GH: ${{ secrets.GITHUB_TOKEN }}' >>"$dir/ci.yml"
if [ -z "$(violations "$dir")" ]; then
    pass "allows GITHUB_TOKEN"
else
    fail "GITHUB_TOKEN alone was refused: $(violations "$dir" | head -1)"
fi

((failed)) && { echo "workflow policy: FAILED" >&2; exit 1; }
echo "workflow policy: every property holds and every mutation is refused"
