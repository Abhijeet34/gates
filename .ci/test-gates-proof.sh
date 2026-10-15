#!/usr/bin/env bash
# gates-proof must be able to fail: each case below breaks a copy of the live
# registry or tree in one way and requires the proof to refuse it, after the
# live registry is shown passing.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

failed=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failed=1; }

workdir=$(mktemp -d) || exit 2
trap 'rm -rf "$workdir"' EXIT

if python3 .ci/gates-proof.py >/dev/null; then
    pass "the live registry"
else
    fail "the live registry is refused"
fi

# A fresh copy of the files the proof reads, for one mutation.
fresh() {
    local dir
    dir=$(mktemp -d "$workdir/t.XXXXXX")
    git ls-files -z | xargs -0 tar cf - | tar xf - -C "$dir"
    printf '%s' "$dir"
}

# <label> <expression run inside the copy>
refuses() {
    local label=$1 dir
    dir=$(fresh)
    (cd "$dir" && eval "$2") || { fail "$label: the mutation did not apply"; return; }
    if python3 .ci/gates-proof.py "$dir" "$dir/.ci/gate-registry.toml" >/dev/null 2>&1; then
        fail "$label was not refused"
    else
        pass "refuses $label"
    fi
}

refuses "an unregistered gate" 'printf "#!/bin/sh\n" > .githooks/pre-commit'
refuses "a deleted red case" "sed -i.orig 's/all skipped/zzz/' .ci/test-checks-verdict.sh && rm .ci/test-checks-verdict.sh.orig"
refuses "a deleted clean case" "sed -i.orig 's/allows GITHUB_TOKEN/zzz/' .ci/test-workflow-policy.sh && rm .ci/test-workflow-policy.sh.orig"
refuses "a deleted test file" 'rm .ci/test-no-mistakes-required.sh'
refuses "a test that check.sh no longer runs" "sed -i.orig '/test-no-mistakes-required.sh/d' .ci/check.sh && rm .ci/check.sh.orig"
refuses "a registered gate file that is gone" 'rm .github/workflows/shared-guard-generated-files.yml'
refuses "a red case equal to the clean case" "sed -i.orig 's/^red = \"expect .all skipped. 1\"/red = \"pull request, every job ran\"/' .ci/gate-registry.toml && rm .ci/gate-registry.toml.orig"
refuses "a red case left only in a comment" "sed -i.orig \"s/^expect 'all skipped' 1/# expect 'all skipped' 1/\" .ci/test-checks-verdict.sh && rm .ci/test-checks-verdict.sh.orig"
refuses "a suite invocation left only in a comment" "sed -i.orig 's|^\( *\)bash .ci/test-no-mistakes-required.sh|\1# bash .ci/test-no-mistakes-required.sh|' .ci/check.sh && rm .ci/check.sh.orig"
refuses "an empty registry" ': > .ci/gate-registry.toml'

((failed)) && { printf '\ngates-proof regression tests FAILED\n' >&2; exit 1; }
printf '\ngates-proof regression tests passed\n'
