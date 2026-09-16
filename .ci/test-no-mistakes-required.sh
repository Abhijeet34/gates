#!/usr/bin/env bash
# Regression tests for the check body inside
# .github/workflows/shared-no-mistakes-required.yml.
#
# The gate exists to be believed, so its three properties are pinned rather
# than argued: a pull request that satisfies it must not be left red, one that
# does not must still fail, and a body that could not be read must never pass.
#
# The check reads the body from the API because the event payload races the
# no-mistakes pipeline's own body write. That read is what these cases stub:
# `gh` and `sleep` are replaced on PATH, so a retry sequence runs in
# milliseconds and the attempt count is observable.
#
# Same extraction trick and same reason as test-guard-generated-files.sh: a
# reusable workflow's steps run in the caller's checkout and GITHUB_TOKEN
# cannot clone this private repository, so the logic has to live inline in the
# YAML where no linter reaches it.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

WORKFLOW=.github/workflows/shared-no-mistakes-required.yml
STEP='name: Verify no-mistakes signature in PR body'

UPSTREAM_MARKER='Updates from [git push no-mistakes](https://github.com/kunchenguid/no-mistakes)'
MIRROR_MARKER='Updates from [git push no-mistakes](https://github.com/Abhijeet34/no-mistakes)'
# The build this fleet runs writes the mirror's URL; an upstream build writes
# the upstream one. Both are signatures, so the unqualified marker is ours.
MARKER=$MIRROR_MARKER

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

check="$workdir/check.sh"
stub="$workdir/stub"
mkdir -p "$stub"

# Print the `run: |` block that follows $STEP, dedented. Ends at the first
# non-blank line indented less than the block.
awk -v step="$STEP" '
    index($0, step) { found = 1 }
    found && !inblock && /^[[:space:]]*run: \|[[:space:]]*$/ {
        match($0, /^ */); indent = RLENGTH + 2; inblock = 1; next
    }
    inblock {
        if ($0 ~ /^[[:space:]]*$/) { print ""; next }
        match($0, /^ */)
        if (RLENGTH < indent) exit
        print substr($0, indent + 1)
    }
' "$WORKFLOW" >"$check"

failed=0

# Extraction is the one failure that would make every case below pass
# vacuously, so it is asserted rather than assumed.
if ! grep -q 'read_ok' "$check"; then
    printf 'FAIL could not extract the check body from %s\n' "$WORKFLOW"
    exit 1
fi

# The retry budget is read from the step, not restated here, so a case that
# asserts "the loop exhausted" keeps meaning that when the budget changes.
attempts=$(sed -n 's/^attempts=\([0-9][0-9]*\).*/\1/p' "$check" | head -n 1)
if [[ -z $attempts ]]; then
    printf 'FAIL could not read the attempt budget from the extracted body\n'
    exit 1
fi

# `gh` stands in for `gh api ... --jq '.body // ""'`: it serves $stub/resp.<n>
# for the nth call, falling back to resp.default. Line 1 of a response file is
# the exit status, the rest is the body.
cat >"$stub/gh" <<'STUB'
#!/usr/bin/env bash
dir="${0%/*}"
n=$(( $(cat "$dir/count") + 1 ))
printf '%s' "$n" >"$dir/count"
f="$dir/resp.$n"
[ -f "$f" ] || f="$dir/resp.default"
tail -n +2 "$f"
exit "$(head -n 1 "$f")"
STUB

# Retries are the point of the check and the enemy of a fast test.
printf '#!/bin/sh\nexit 0\n' >"$stub/sleep"
chmod +x "$stub/gh" "$stub/sleep"

# $1 attempt number or `default`, $2 exit status, $3 body
respond() { printf '%s\n%s' "$2" "$3" >"$stub/resp.$1"; }

reset_stub() {
    rm -f "$stub"/resp.*
    printf '0' >"$stub/count"
}

# $1 name, $2 expected exit (or `nonzero`), $3 expected attempt count (or `-`),
# $4 substring the output must contain, $5 substring it must not contain (or `-`)
expect() {
    local name="$1" want="$2" want_n="$3" must="$4" mustnt="$5" got n ok=1
    PATH="$stub:$PATH" \
        PR_REPO=Abhijeet34/example PR_NUMBER=42 PR_AUTHOR=someone \
        PIPELINE_STEPS=review/test/lint/CI GH_TOKEN=stub \
        bash -e "$check" >"$workdir/out" 2>&1
    got=$?
    n=$(cat "$stub/count")
    if [[ $want == nonzero ]]; then
        ((got != 0)) || ok=0
    else
        ((got == want)) || ok=0
    fi
    if ((ok)) && [[ $want_n != - && $n != "$want_n" ]]; then
        ok=0
        printf 'FAIL %s: expected %s API reads, got %s\n' "$name" "$want_n" "$n"
    elif ((ok)) && ! grep -qF -- "$must" "$workdir/out"; then
        ok=0
        printf 'FAIL %s: output missing %s\n' "$name" "$must"
    elif ((ok)) && [[ $mustnt != - ]] && grep -qF -- "$mustnt" "$workdir/out"; then
        ok=0
        printf 'FAIL %s: output should not contain %s\n' "$name" "$mustnt"
    elif ((!ok)); then
        printf 'FAIL %s: expected exit %s, got %d\n' "$name" "$want" "$got"
    fi
    if ((ok)); then
        printf 'ok   %s\n' "$name"
    else
        sed 's/^/       /' "$workdir/out"
        failed=1
    fi
}

# --- the race: a body written after the pull request was created ------------

# The whole defect. The pipeline creates the pull request, the gate reads a
# body without the marker, and the marker arrives moments later.
reset_stub
respond 1 0 'no pipeline section yet'
respond default 0 "## Pipeline

$MARKER"
expect 'a body written after creation is seen on a retry' 0 2 'Found no-mistakes signature' -

reset_stub
respond default 0 "## Pipeline

$MARKER"
expect 'a body that already carries the marker passes on the first read' 0 1 \
    'Found no-mistakes signature' -

# --- which build wrote it: either no-mistakes URL is a signature -------------

reset_stub
respond default 0 "## Pipeline

$MIRROR_MARKER"
expect 'a marker written by our mirror build passes' 0 1 'Found no-mistakes signature' -

reset_stub
respond default 0 "## Pipeline

$UPSTREAM_MARKER"
expect 'a marker written by an upstream build passes' 0 1 'Found no-mistakes signature' -

# --- the negative: the gate is not a formality -------------------------------

reset_stub
respond default 0 'Hand-raised, no pipeline section.'
expect 'a body that never carries the marker fails' 1 "$attempts" \
    'was not raised through no-mistakes' 'Could not establish'

# A body the API reports as null reads as empty, which is a verdict of
# "no marker" and not of "could not read".
reset_stub
respond default 0 ''
expect 'an empty body fails as no-marker, not as unreadable' 1 "$attempts" \
    'was not raised through no-mistakes' 'Could not establish'

# The marker is matched whole. A body that merely mentions no-mistakes, or
# points at a different repository, is not a signature.
reset_stub
respond default 0 'Updates from [git push no-mistakes](https://github.com/someone/no-mistakes)'
expect 'a marker naming a different repository fails' 1 "$attempts" \
    'was not raised through no-mistakes' -

# --- fail closed: could-not-establish is its own answer ----------------------

reset_stub
respond default 1 'gh: HTTP 403'
expect 'an unreadable body fails closed' 1 "$attempts" \
    'Could not establish' 'was not raised through no-mistakes'

# A single API hiccup is not a verdict either: the retry that exists for the
# race covers it.
reset_stub
respond 1 1 'gh: HTTP 502'
respond default 0 "$MARKER"
expect 'a transient API error recovers on the next attempt' 0 2 \
    'Found no-mistakes signature' -

# The inverse of the case above: a read that succeeds and then starts failing
# must not be reported as could-not-establish, because one read did establish
# something.
reset_stub
respond 1 0 'nothing yet'
respond default 1 'gh: HTTP 403'
expect 'a later API failure does not erase an earlier successful read' 1 "$attempts" \
    'was not raised through no-mistakes' 'Could not establish'

exit "$failed"
