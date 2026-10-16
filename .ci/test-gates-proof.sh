#!/usr/bin/env bash
# gates-proof must be able to fail: each case below breaks a copy of the live
# registry or tree in one way and requires the proof to refuse it, after the
# live registry is shown passing. The structural cases run `--static`; the
# executable ones prove a single gate with `--gate`, so neither re-runs the
# whole proof that check.sh has just run.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

failed=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failed=1; }

workdir=$(mktemp -d) || exit 2
trap 'rm -rf "$workdir"' EXIT

if python3 .ci/gates-proof.py --static >/dev/null; then
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

# <label> <proof flags> <expression run inside the copy> [text the refusal must carry]
refuses() {
    local label=$1 flags=$2 dir out
    dir=$(fresh)
    (cd "$dir" && eval "$3") || { fail "$label: the mutation did not apply"; return; }
    # shellcheck disable=SC2086 # the flags are one or two words, split on purpose
    if out=$(python3 .ci/gates-proof.py $flags "$dir" "$dir/.ci/gate-registry.toml" 2>&1); then
        fail "$label was not refused"
    elif [ -n "${4:-}" ] && ! grep -qF -- "$4" <<<"$out"; then
        fail "$label was refused, but not for '$4':"
        printf '%s\n' "$out" | sed 's/^/     /'
    else
        pass "refuses $label"
    fi
}

# Appends one gate whose command is the commit-msg hook: <id> <red> <clean> <run> <finding>
row() {
    printf '\n[[gate]]\nid = "%s"\nsurface = "x"\ngates = ["%s"]\ntest = "%s"\nsuite = "shell"\n' \
        "$1" system-maintenance/git-hooks/commit-msg system-maintenance/git-hooks/test-commit-msg.sh
    printf 'command = "%s"\n' system-maintenance/git-hooks/commit-msg
    printf "red = '''%s'''\nclean = '''%s'''\nrun = '''%s'''\nfinding = '''%s'''\n" "$2" "$3" "$4" "$5"
} >>.ci/gate-registry.toml

AGENT="printf 'feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n' > msg"
HUMAN="printf 'feat: x\n\nCo-authored-by: Jane Doe <jane@example.com>\n' > msg"
FINDING='refusing agent attribution line'

refuses "an unregistered gate" --static 'printf "#!/bin/sh\n" > .githooks/pre-commit'
refuses "a deleted test file" --static 'rm .ci/test-no-mistakes-required.sh'
refuses "a test that check.sh no longer runs" --static "sed -i.orig '/test-no-mistakes-required.sh/d' .ci/check.sh && rm .ci/check.sh.orig"
refuses "a registered gate file that is gone" --static 'rm .github/workflows/shared-guard-generated-files.yml'
refuses "a suite invocation left only in a comment" --static "sed -i.orig 's|^\( *\)bash .ci/test-no-mistakes-required.sh|\1# bash .ci/test-no-mistakes-required.sh|' .ci/check.sh && rm .ci/check.sh.orig"
refuses "a suite that runs the proof --static" --static "sed -i.orig 's|python3 .ci/gates-proof.py |python3 .ci/gates-proof.py --static |' .ci/check.sh && rm .ci/check.sh.orig" \
    "does not run \`python3 .ci/gates-proof.py\` bare"
refuses "an empty registry" --static ': > .ci/gate-registry.toml'
refuses "a red input equal to the clean input" --static "row same \"$AGENT\" \"$AGENT\" 'sh \"\$GATE\" msg' '$FINDING'"
refuses "a command that is not one of its gates" --static "sed -i.orig 's|^command = \"system-maintenance/git-hooks/commit-msg\"\$|command = \".githooks/pre-push\"|' .ci/gate-registry.toml && rm .ci/gate-registry.toml.orig" \
    "command .githooks/pre-push is not one of its gates"
refuses "a workflow step that no longer exists" --static "sed -i.orig 's/- name: Verify no-mistakes signature in PR body/- name: Renamed/' .github/workflows/shared-no-mistakes-required.yml && rm .github/workflows/shared-no-mistakes-required.yml.orig" \
    "no single step named 'Verify no-mistakes signature in PR body'"

refuses "a run that decides the verdict without its gate" '--gate fake' \
    "row fake \"$AGENT\" \"$HUMAN\" 'grep -q Claude msg && echo \"$FINDING\" && exit 1; exit 0' '$FINDING'" \
    "with system-maintenance/git-hooks/commit-msg neutered the red input is still refused"
refuses "a red input its gate lets through" '--gate leaky' \
    "row leaky \"$HUMAN\" \"printf 'feat: x\n' > msg\" 'sh \"\$GATE\" msg' '$FINDING'" \
    "leaky: red input was not refused"
refuses "a red input refused for another reason" '--gate misnamed' \
    "row misnamed \"$AGENT\" \"$HUMAN\" 'sh \"\$GATE\" msg' 'a finding the hook never prints'" \
    "without naming 'a finding the hook never prints'"
refuses "a clean input its gate refuses" '--gate strict' \
    "row strict \"$AGENT\" \"$AGENT # differs\" 'sh \"\$GATE\" msg' '$FINDING'" \
    "strict: clean input was refused"
refuses "a red input whose setup fails" '--gate broken' \
    "row broken 'false' \"$HUMAN\" 'sh \"\$GATE\" msg' '$FINDING'" \
    "broken: red input setup failed"
refuses "a proof that executed no case" '--gate no-such-gate' ':' "no case was executed"

((failed)) && { printf '\ngates-proof regression tests FAILED\n' >&2; exit 1; }
printf '\ngates-proof regression tests passed\n'
