#!/usr/bin/env bash
# Every suite `.ci/check.sh` declares must run somewhere, and every suite the
# local gate does NOT run must be named in `.no-mistakes.yaml`'s own comment.
#
# Both halves are the mechanical form of a prose rule that failed twice in the
# repository these gates came from: two test files worth 190 assertions that no
# suite, workflow step or gate entry ever reached, and a gate comment that named
# one skipped suite of three, which a worker then read as the whole story. A
# prose rule cannot enumerate; this can.
#
# Kept grep-shaped and dependency-free so it can live in `check_shell` beside
# the cheap guards: 0.35s at load 10, three passes over three small files, no
# yq, no network, no $HOME.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

CHECK=.ci/check.sh
GATE=.no-mistakes.yaml
CI=.github/workflows/ci.yml

rc=0
fail() {
    printf '\033[31mFAIL\033[0m %s\n' "$1" >&2
    rc=1
}

# The `case` arm in run_suite() is the declaration of what suites exist. Read it
# rather than a hand-kept list, so a new suite is covered the moment it is added.
declared_suites() {
    sed -n 's/^ *\(\([a-z]* | \)*[a-z]*\)) run "\$1" "check_\$1" ;;$/\1/p' "$1" | tr -d ' ' | tr '|' '\n'
}

# The gate's lint value, as words: `.ci/check.sh shell python secrets` -> the
# three suite names.
lint_suites() {
    sed -n 's/^ *lint: *"\.ci\/check\.sh \(.*\)"$/\1/p' "$1" | tr ' ' '\n'
}

# Every suite named by a `run: .ci/check.sh ...` step in the CI workflow.
ci_suites() {
    sed -n 's/^ *- run: \.ci\/check\.sh \(.*\)$/\1/p' "$1" | tr ' ' '\n'
}

# The comment block that ends at the `commands:` key. A suite the local gate does
# not run has to be named here, because this file is what the reader has open.
gate_comment() {
    sed -n '1,/^commands:/p' "$1"
}

# Both assertions, against one triple of files. Prints its own failures; returns
# nonzero if either assertion fired, which is what the mutation cases below read.
assert_coverage() {
    local check=$1 gate=$2 ci=$3 label=$4 sub=0
    local declared lint ci_run comment suite

    declared=$(declared_suites "$check")
    lint=$(lint_suites "$gate")
    ci_run=$(ci_suites "$ci")
    comment=$(gate_comment "$gate")

    [[ -n $declared && -n $lint && -n $ci_run ]] || {
        printf '%s: parsed nothing (declared=%d lint=%d ci=%d)\n' \
            "$label" "$(wc -w <<<"$declared")" "$(wc -w <<<"$lint")" "$(wc -w <<<"$ci_run")" >&2
        return 1
    }

    while read -r suite; do
        [[ -n $suite ]] || continue
        if ! grep -qxF "$suite" <<<"$lint" && ! grep -qxF "$suite" <<<"$ci_run"; then
            printf '%s: suite `%s` is declared in %s and run by neither %s nor %s\n' \
                "$label" "$suite" "$check" "$gate" "$ci" >&2
            sub=1
            continue
        fi
        # Absent from the local gate: the reader of $gate must be able to find
        # that out from $gate.
        if ! grep -qxF "$suite" <<<"$lint" && ! grep -qF "\`$suite\`" <<<"$comment"; then
            printf '%s: suite `%s` does not run in the local gate and %s never names it\n' \
                "$label" "$suite" "$gate" >&2
            sub=1
        fi
    done <<<"$declared"

    return $sub
}

assert_coverage "$CHECK" "$GATE" "$CI" 'live' || fail 'suite coverage'

# The assertion has to be able to fail, so each half is shown refusing a
# one-line mutation of the live files. Same shape as
# evals/scrape-baseline/harness/test_golden_coverage.py: break one declaration,
# re-grade, and fail if the verdict does not move.
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

# An extra declared suite that nothing runs - the 2026-08-14 defect, injected.
sed 's/^\( *shell | python.*\)$/        zzfake) run "$1" "check_$1" ;;\n\1/' "$CHECK" >"$workdir/check-unrun.sh"
grep -q 'zzfake' "$workdir/check-unrun.sh" || fail 'mutation 1 did not apply'
if assert_coverage "$workdir/check-unrun.sh" "$GATE" "$CI" 'mutation:unrun-suite' 2>/dev/null; then
    fail 'an unrun suite did not fail the check'
fi

# A suite the local gate does not run, with no comment naming it - the
# misreading above, injected. Every suite runs in `lint` here, so the mutation
# drops one from `lint` rather than from the comment, and the comment (which
# names none) must then fail the check.
dropped=$(lint_suites "$GATE" | tail -1)
[[ -n $dropped ]] || fail 'the lint line names no suite; mutation 2 has nothing to drop'
sed "s/^\( *lint: *\".*\) $dropped\"$/\1\"/; s/\`$dropped\`//g" "$GATE" >"$workdir/gate-silent.yaml"
lint_suites "$workdir/gate-silent.yaml" | grep -qxF "$dropped" && fail 'mutation 2 did not apply'
if assert_coverage "$CHECK" "$workdir/gate-silent.yaml" "$CI" 'mutation:unnamed-suite' 2>/dev/null; then
    fail "dropping \`$dropped\` from $GATE's lint line, unnamed in its comment, did not fail the check"
fi

((rc)) || printf 'suite coverage: %s suites, all run, all named\n' "$(declared_suites "$CHECK" | grep -c .)"
exit $rc
