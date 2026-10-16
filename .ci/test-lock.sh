#!/usr/bin/env bash
# Regression tests for run/lock.sh, the bootstrap's verification of a caller's
# .github/gates.lock.
#
#   .ci/test-lock.sh
#
# Every control is shown refusing its red scenario and leaving the gates
# checkout where it was, every clean scenario is shown passing with gates
# checked out at the commit it names, and each control is then deleted from a
# copy of the script, which must let its red scenario through: a refusal that
# survives the deletion of its own guard came from somewhere else.
# Scenarios are built by .ci/fixtures/lock-fixture.sh, which names its commits.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
ROOT=$(pwd -P)
LOCK_SH="$ROOT/run/lock.sh"
FIXTURE="$ROOT/.ci/fixtures/lock-fixture.sh"
command -v jq >/dev/null 2>&1 || { echo "test-lock: jq is not installed (brew install jq)" >&2; exit 2; }

workdir=$(mktemp -d) || exit 2
trap 'rm -rf "$workdir"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

failed=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failed=1; }

build() { # <scenario> ; prints the scenario directory
    local dir
    dir=$(mktemp -d "$workdir/s.XXXXXX")
    (cd "$dir" && bash "$FIXTURE" "$1" >/dev/null) || { echo "test-lock: fixture $1 failed" >&2; exit 2; }
    printf '%s' "$dir"
}

# <label> <scenario> <want rc> <text the output must carry> <commit gates must end at>
#   [prep, run in the scenario directory with env.sh sourced] [script]
expect() {
    local label=$1 scenario=$2 want=$3 must=$4 at=$5 prep=${6:-:} script=${7:-$LOCK_SH} dir out rc head
    dir=$(build "$scenario")
    out=$(cd "$dir" && . ./env.sh && eval "$prep" && cd caller && bash -e "$script" 2>&1)
    rc=$?
    head=$(cd "$dir" && . ./env.sh && git -C .gates rev-parse HEAD)
    at=$(cd "$dir" && . ./env.sh && eval "echo $at")
    if [ "$rc" -ne "$want" ]; then
        fail "$label: exit $rc, want $want"
        printf '%s\n' "$out" | sed 's/^/     | /'
    elif ! grep -qF -- "$must" <<<"$out"; then
        fail "$label: output does not carry '$must'"
        printf '%s\n' "$out" | sed 's/^/     | /'
    elif [ "$head" != "$at" ]; then
        fail "$label: gates checkout is at $head, want $at"
    else
        pass "$label"
    fi
}

# --- clean: the lock is honoured, or its absence means the bootstrap's commit --

expect "no lock on a push runs gates at the bootstrap's own commit" legacy 0 "gates at" '$FIX_C'
expect "a lock on main with green checks runs gates at the lock" locked 0 "gates locked at" '$FIX_B'
expect "a comment beside the lock is allowed" locked 0 "gates locked at" '$FIX_B' \
    'printf "# v1\n%s  # bumped\n" "$FIX_B" > caller/.github/gates.lock'
expect "a pull request that changes no lock needs no bump identity" pr-legacy 0 "gates at" '$FIX_C'
expect "a bump by the bump identity, forward on main, lock and copies only" bump 0 "gates locked at" '$FIX_B'
expect "a lock added by the bump identity at the bootstrap's commit" added 0 "gates locked at" '$FIX_C'
expect "a lock removed by the bump identity, forward to the bootstrap's commit" removed 0 "gates at" '$FIX_C'
expect "a rollback by the bump identity to the lock the base's lock replaced" rollback 0 "gates locked at" '$FIX_A'
expect "a rollback from a depth-1 caller checkout, which lacks the base's history" rollback 0 "gates locked at" '$FIX_A' \
    'mv caller caller.src && git clone -q --no-local --depth 1 "file://$PWD/caller.src" caller'

# --- red: each control, and gates is never checked out at what it refused -----

expect "an imposter: a fork-only commit fetched into the checkout" imposter 1 "not on gates main" '$FIX_C'
expect "an imposter: a commit gates does not have at all" locked 1 "not on gates main" '$FIX_C' \
    'echo 0123456789abcdef0123456789abcdef01234567 > caller/.github/gates.lock'
expect "a commit on main whose checks run failed" unchecked 1 "no green checks run" '$FIX_C'
expect "a downgrade" downgrade 1 "a downgrade" '$FIX_C'
expect "a rollback past the lock the base's lock replaced" rollback-skip 1 "a downgrade" '$FIX_C'
expect "a rollback by another author" rollback 1 "someone (1) is not the bump identity" '$FIX_C' \
    'export PR_AUTHOR=someone PR_AUTHOR_ID=1'
expect "a lock change bundled with other content" bundled 1 "together with other content: app.py" '$FIX_C'
expect "a lock change by another author" author 1 "someone (1) is not the bump identity" '$FIX_C'
expect "a lock change by the bump login with another id" impersonated 1 "(9999) is not the bump identity" '$FIX_C'
expect "a lock change when gates main names no bump identity" bump 1 "configures no bump identity" '$FIX_C' \
    'g() { git -C .gates "$@"; }; g checkout -q -B main origin/main && : > .gates/run/bump-identity && g commit -qam unset && g push -q origin main && g checkout -q --detach "$FIX_C"'

expect "a lock removed by another author" removed 1 "someone (1) is not the bump identity" '$FIX_C' \
    'export PR_AUTHOR=someone PR_AUTHOR_ID=1'
expect "a lock added by another author" added 1 "someone (1) is not the bump identity" '$FIX_C' \
    'export PR_AUTHOR=someone PR_AUTHOR_ID=1'
expect "a lock added at a commit older than the bootstrap's" added 1 "a downgrade" '$FIX_C' \
    'echo "$FIX_A" > caller/.github/gates.lock && git -C caller commit -qam older'

# --- red: what the lock is read from and checked against ----------------------

expect "a lock that names a tag, not a commit" locked 1 "is not a single 40-hex gates commit: v1" '$FIX_C' \
    'echo v1 > caller/.github/gates.lock'
expect "a lock naming two commits" locked 1 "is not a single 40-hex gates commit" '$FIX_C' \
    'printf "%s\n%s\n" "$FIX_A" "$FIX_B" > caller/.github/gates.lock'
expect "a lock in upper case" locked 1 "is not a single 40-hex gates commit" '$FIX_C' \
    'tr a-f A-F <<<"$FIX_B" > caller/.github/gates.lock'
expect "a lock naming a blob, not a commit" locked 1 "not on gates main" '$FIX_C' \
    'git -C .gates rev-parse "$FIX_B:.gitleaks.toml" > caller/.github/gates.lock'
expect "a lock that is a directory" locked 1 "is not a regular file" '$FIX_C' \
    'rm caller/.github/gates.lock && mkdir caller/.github/gates.lock'
expect "a lock that is a symlink" locked 1 "is not a regular file" '$FIX_C' \
    'echo "$FIX_B" > caller/elsewhere && ln -sf ../elsewhere caller/.github/gates.lock'
expect "a shallow gates checkout" locked 1 "is shallow" '$FIX_C' \
    'rm -rf .gates && git clone -q --depth 1 "file://$PWD/gates.git" .gates'
expect "a check-runs read that fails" locked 1 "could not read the check runs" '$FIX_C' \
    'printf "#!/bin/sh\nexit 1\n" > bin/gh'
expect "a commit with no checks run" locked 1 "no green checks run" '$FIX_C' \
    "printf '#!/bin/sh\necho %s\n' \"'{\\\"check_runs\\\":[]}'\" > bin/gh"
expect "a checks run still in progress" locked 1 "no green checks run" '$FIX_C' \
    "printf '#!/bin/sh\necho %s\n' \"'{\\\"check_runs\\\":[{\\\"name\\\":\\\"checks\\\",\\\"status\\\":\\\"in_progress\\\",\\\"conclusion\\\":null,\\\"app\\\":{\\\"id\\\":15368}}]}'\" > bin/gh"
expect "a green checks run posted by another app" locked 1 "no green checks run" '$FIX_C' \
    "printf '#!/bin/sh\necho %s\n' \"'{\\\"check_runs\\\":[{\\\"name\\\":\\\"checks\\\",\\\"status\\\":\\\"completed\\\",\\\"conclusion\\\":\\\"success\\\",\\\"app\\\":{\\\"id\\\":1}}]}'\" > bin/gh"
expect "a pull request whose base cannot be fetched" pr-legacy 1 "could not fetch the pull request's base" '$FIX_C' \
    'export BASE_SHA=0123456789abcdef0123456789abcdef01234567'
expect "an empty gates source" legacy 1 "the gates source cannot be named" '$FIX_C' \
    'export GATES_REPOSITORY='

# --- the old code path: each control deleted, its red scenario gets through ----

# <label> <scenario> <commit gates then runs at> <exact guard line>
# Matched by bash string equality, not awk: `awk -v` escape-processes the
# value, each guard line ends in a backslash, and on the ubuntu-24.04 runner
# the awk form matched none of the five (measured 2026-10-16).
without() {
    local mutant="$workdir/without-$1.sh" n=0 l
    while IFS= read -r l; do
        if [ "$l" = "$4" ]; then
            printf '%strue \\\n' "${l%%[! ]*}"
            n=$((n + 1))
        else
            printf '%s\n' "$l"
        fi
    done <"$LOCK_SH" >"$mutant"
    if [ "$n" = 1 ]; then
        expect "without its guard, the $1 red scenario gets through" "$2" 0 "gates locked at" "$3" : "$mutant"
    else
        fail "the $1 guard is not in $LOCK_SH exactly once"
    fi
}
without imposter imposter '$FIX_F' '    on_main "$head_lock" \'
without checks unchecked '$FIX_B' '    green "$head_lock" \'
without bundled bundled '$FIX_B' '        [ -z "$extra" ] \'
without author author '$FIX_B' '        grep -qxF "${PR_AUTHOR:-} ${PR_AUTHOR_ID:-}" <<<"$identity" \'
without downgrade downgrade '$FIX_A' '        g merge-base --is-ancestor "$from" "$target" 2>/dev/null \'
without rollback rollback-skip '$FIX_A' '    [ "$prev" = "$1" ] \'

((failed)) && { echo "test-lock: FAILED" >&2; exit 1; }
echo "test-lock: every control refuses its red scenario, and each deleted guard lets its own through"
