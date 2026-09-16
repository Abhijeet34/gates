#!/usr/bin/env bash
# Regression tests for the scan bodies inside
# .github/workflows/shared-secret-scan.yml.
#
#   .ci/test-secret-scan.sh
#
# The third shared workflow, and the last one to get a contract test. Same
# reason as the other two: a reusable workflow's steps run in the CALLER's
# checkout and GITHUB_TOKEN cannot clone this private repository to fetch a
# script, so the logic has to live inline in YAML where no linter reaches it.
# Without this file a broken body reaches main and every caller silently loses
# secret scanning - a scan that reports clean because it never ran is worse
# than no scan at all.
#
# Three properties, in the order that matters:
#   1. a planted secret fails,
#   2. a clean tree passes,
#   3. every way the scan can fail to ESTABLISH a verdict refuses.
#
# (3) is the one with teeth, and it is why the workflow was changed rather than
# only tested: `gitleaks git` exits 0 on a repository it could not read.
#
# Shape follows test-pre-push.sh (a real throwaway repository, the real
# canonical config, real gitleaks) and test-no-mistakes-required.sh (the body
# extracted from the YAML, with a stub gitleaks on PATH for the cases real
# gitleaks cannot be made to produce).

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

WORKFLOW=.github/workflows/shared-secret-scan.yml
CONFIG=.gitleaks.toml
FIXTURE=.ci/gitleaks/fixtures/known-secrets.tsv

for f in "$WORKFLOW" "$CONFIG" "$FIXTURE"; do
    [ -r "$f" ] || { echo "test-secret-scan: cannot read $f" >&2; exit 2; }
done
command -v gitleaks >/dev/null 2>&1 || { echo "test-secret-scan: gitleaks not installed" >&2; exit 2; }

workdir=$(mktemp -d) || exit 2
trap 'rm -rf "$workdir"' EXIT

failed=0

# --- extract the step bodies -------------------------------------------------

# Print the `run:` payload of the step named $2, dedented. Handles both a `run: |`
# block and a single-line `run: cmd`; ends at the first non-blank line indented
# less than the block.
extract() { # workflow step-name outfile
    awk -v step="name: $2" '
        index($0, step) { found = 1; next }
        found && !inblock && /^[[:space:]]*run:[[:space:]]*\|[[:space:]]*$/ {
            match($0, /^ */); indent = RLENGTH + 2; inblock = 1; next
        }
        found && !inblock && /^[[:space:]]*run:[[:space:]]*[^|[:space:]]/ {
            sub(/^[[:space:]]*run:[[:space:]]*/, ""); print; exit
        }
        inblock {
            if ($0 ~ /^[[:space:]]*$/) { print ""; next }
            match($0, /^ */)
            if (RLENGTH < indent) exit
            print substr($0, indent + 1)
        }
    ' "$1" >"$3"
}

history_scan="$workdir/history.sh"
tree_scan="$workdir/tree.sh"
extract "$WORKFLOW" 'Scan the full history' "$history_scan"
extract "$WORKFLOW" 'Scan the working tree' "$tree_scan"

# Extraction failing is the one defect that would make every case below pass
# vacuously, so it is asserted rather than assumed.
grep -q 'COULD NOT ESTABLISH' "$history_scan" || {
    printf 'FAIL could not extract the history-scan body from %s\n' "$WORKFLOW"
    exit 1
}
grep -q 'gitleaks dir' "$tree_scan" || {
    printf 'FAIL could not extract the working-tree scan body from %s\n' "$WORKFLOW"
    exit 1
}

# --- the two digests are pins, and a pin drifts ------------------------------
#
# .gitleaks.toml (the rules) and .githooks/pre-push (the gate) are both synced
# from this repository by .ci/gitleaks/sync.sh, and both are pinned in the
# workflow. What breaks the fleet is a pin going stale - edit either canonical
# file, forget its line, and all four callers go red for the wrong reason - so
# each is asserted against the command that prints the value to paste.
pin() { # env-key canonical-path sync-argument
    local pinned actual
    pinned=$(sed -n "s/^ *$1: *\"\([0-9a-f]*\)\".*/\1/p" "$WORKFLOW" | head -n 1)
    actual=$(./.ci/gitleaks/sync.sh --digest "$3")
    if [ "$pinned" != "$actual" ]; then
        printf 'FAIL %s in %s is %s, but %s digests to %s\n' \
            "$1" "$WORKFLOW" "${pinned:-<unset>}" "$2" "$actual"
        printf '       fix: update that line to the value .ci/gitleaks/sync.sh --digest %s prints\n' "$3"
        failed=1
    else
        printf 'ok   %s matches canonical %s\n' "$1" "$2"
    fi
}
pin CONFIG_SHA256 .gitleaks.toml     config
pin HOOK_SHA256   .githooks/pre-push hook

# --- the gate-verify step body, actually executed ----------------------------
#
# The pin above only says the number is current. This runs the step that READS
# it, against a fixture standing in for a caller's checkout, because a pin whose
# checker never runs is the defect one layer up - and it is the defect that
# happened: only the config was pinned, four repositories sat 27 lines behind
# canonical on the hook, and every check stayed green until a task tripped over
# it. The body is written with a string comparison rather than `sha256sum
# --check --strict` precisely so it is runnable here as well as on the runner;
# the two scan bodies below still cannot be, and that asymmetry is the reason.
gate_step="$workdir/gate.sh"
extract "$WORKFLOW" 'Verify this repository carries the canonical gate' "$gate_step"
grep -q 'has DRIFTED' "$gate_step" || {
    printf 'FAIL could not extract the gate-verify body from %s\n' "$WORKFLOW"
    exit 1
}

CONFIG_PIN=$(./.ci/gitleaks/sync.sh --digest config)
HOOK_PIN=$(./.ci/gitleaks/sync.sh --digest hook)

caller="$workdir/caller"
mkdir -p "$caller/.githooks"
cp "$CONFIG" "$caller/.gitleaks.toml"
cp .githooks/pre-push "$caller/.githooks/pre-push"
chmod +x "$caller/.githooks/pre-push"

gate() { # name expected-rc substring-the-output-must-contain (or -)
    local name="$1" want="$2" must="$3" got ok=1
    (
        cd "$caller" || exit 2
        CONFIG_SHA256="$CONFIG_PIN" HOOK_SHA256="$HOOK_PIN" bash -e "$gate_step"
    ) >"$workdir/out" 2>&1
    got=$?
    if [ "$got" -ne "$want" ]; then
        ok=0
        printf 'FAIL %s: expected exit %s, got %s\n' "$name" "$want" "$got"
    elif [ "$must" != - ] && ! grep -qF -- "$must" "$workdir/out"; then
        ok=0
        printf 'FAIL %s: output missing %s\n' "$name" "$must"
    fi
    if [ "$ok" -eq 1 ]; then
        printf 'ok   %s\n' "$name"
    else
        sed 's/^/       /' "$workdir/out"
        failed=1
    fi
}

gate 'a caller carrying both canonical copies passes' 0 -

# One appended line is the whole defect: content that still runs, still exits 0,
# and is no longer the reviewed gate.
printf '\n# drift\n' >>"$caller/.githooks/pre-push"
gate 'a drifted pre-push hook fails, naming the file' 1 '.githooks/pre-push has DRIFTED'
gate 'the drift refusal names the remedy' 1 'sync.sh'

cp .githooks/pre-push "$caller/.githooks/pre-push"
chmod +x "$caller/.githooks/pre-push"
gate 'restoring the canonical hook passes again' 0 -

# git skips a non-executable hook silently, so identical content with the wrong
# mode is a gate that never runs. The mode is tracked, so this one is visible to
# CI; core.hooksPath is not, and only sync.sh --check reaches it.
chmod -x "$caller/.githooks/pre-push"
gate 'a hook that is not executable fails' 1 'not executable'
chmod +x "$caller/.githooks/pre-push"

mv "$caller/.githooks/pre-push" "$caller/.githooks/pre-push.away"
gate 'a caller with no pre-push hook at all fails' 1 '.githooks/pre-push is missing'
mv "$caller/.githooks/pre-push.away" "$caller/.githooks/pre-push"

# The half that already existed must survive the rewrite that added the other.
printf '\n# drift\n' >>"$caller/.gitleaks.toml"
gate 'a drifted .gitleaks.toml still fails' 1 '.gitleaks.toml has DRIFTED'
cp "$CONFIG" "$caller/.gitleaks.toml"
gate 'both copies canonical again passes' 0 -

# --- a repository to scan ----------------------------------------------------

# No global or system git config: the fixture must not inherit this machine's
# core.hooksPath, and git-lfs must not be pulled into a repository that has none.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

SECRET=$(awk -F'\t' '$2 == "moonshot-kimi" { print $3 }' "$FIXTURE")
[ -n "$SECRET" ] || { echo "test-secret-scan: fixture has no moonshot-kimi row" >&2; exit 2; }

repo="$workdir/repo"
git init --quiet -b main "$repo"
cp "$CONFIG" "$repo/.gitleaks.toml"
(
    cd "$repo" || exit 1
    git config user.name test
    git config user.email test@example.invalid
    echo "print('hello')" >app.py
    git add -A && git commit --quiet -m clean
) || exit 2

stub="$workdir/stub"
mkdir -p "$stub"

# $1 name, $2 expected exit (or `nonzero`), $3 substring the output must contain
# (or `-`), $4 substring it must not contain (or `-`). Remaining behaviour comes
# from $SCAN, $SCAN_DIR and $STUB_PATH set by the caller.
expect() {
    local name="$1" want="$2" must="$3" mustnt="$4" got ok=1
    (
        cd "${SCAN_DIR:-$repo}" || exit 2
        PATH="${STUB_PATH:-$PATH}" bash -e "${SCAN:-$history_scan}"
    ) >"$workdir/out" 2>&1
    got=$?
    if [[ $want == nonzero ]]; then
        ((got != 0)) || ok=0
    else
        ((got == want)) || ok=0
    fi
    if ((!ok)); then
        printf 'FAIL %s: expected exit %s, got %d\n' "$name" "$want" "$got"
    elif [[ $must != - ]] && ! grep -qF -- "$must" "$workdir/out"; then
        ok=0
        printf 'FAIL %s: output missing %s\n' "$name" "$must"
    elif [[ $mustnt != - ]] && grep -qF -- "$mustnt" "$workdir/out"; then
        ok=0
        printf 'FAIL %s: output should not contain %s\n' "$name" "$mustnt"
    fi
    if ((ok)); then
        printf 'ok   %s\n' "$name"
    else
        sed 's/^/       /' "$workdir/out"
        failed=1
    fi
}

# --- real gitleaks: the scan detects, and does not cry wolf ------------------

expect 'a clean history passes' 0 'no leaks found' 'COULD NOT ESTABLISH'
SCAN="$tree_scan" expect 'a clean working tree passes' 0 - -

(
    cd "$repo" || exit 1
    printf 'MODELS = {"kimi": "%s"}\n' "$SECRET" >leak.py
    git add leak.py && git commit --quiet -m "carries a secret"
) || exit 2

# The case without which this whole file asserts nothing.
expect 'a planted secret in the history fails' 1 'secrets found' 'COULD NOT ESTABLISH'
SCAN="$tree_scan" expect 'a planted secret in the working tree fails' nonzero - -

(cd "$repo" && git rm --quiet leak.py && git commit --quiet -m "remove the secret") || exit 2

# Deleting the file does not un-publish the commit, and this is the reason the
# workflow fetches the full history rather than the tip.
expect 'a secret removed from the tree still fails the history scan' 1 'secrets found' -
SCAN="$tree_scan" expect 'a secret removed from the tree passes the tree scan' 0 - -

(cd "$repo" && git reset --quiet --hard HEAD~2) || exit 2

# --- fail closed: could-not-establish is its own answer ----------------------
#
# These are the measured fail-open signatures of gitleaks 8.30.1, reproduced by
# a stub because real gitleaks cannot be asked to produce them on demand. Each
# one is a scan that reported success having established nothing.

stub_gitleaks() { cat >"$stub/gitleaks"; chmod +x "$stub/gitleaks"; }
export STUB_PATH="$stub:$PATH"

# The signature measured on 8.30.1 against a target it cannot read.
stub_gitleaks <<'STUB'
#!/bin/sh
echo "11:00AM ERR [git] fatal: not a git repository"
echo "11:00AM INF 0 commits scanned."
echo "11:00AM INF no leaks found"
exit 0
STUB
expect 'a scan that errors and read nothing refuses' 1 'COULD NOT ESTABLISH' 'secrets found'

# Same ERR, but with a plausible count beside it, so the refusal can only come
# from the ERR check. Without this case that check is dead weight: the zero-count
# guard above catches the signature they share, and deleting the ERR line leaves
# every other case still green.
stub_gitleaks <<'STUB'
#!/bin/sh
echo "11:00AM INF 3 commits scanned."
echo "11:00AM ERR [git] error: object file is empty"
echo "11:00AM INF no leaks found"
exit 0
STUB
expect 'a scan that errors part-way through refuses despite a nonzero count' \
    1 'COULD NOT ESTABLISH' 'secrets found'

stub_gitleaks <<'STUB'
#!/bin/sh
echo "11:00AM INF 0 commits scanned."
echo "11:00AM INF no leaks found"
exit 0
STUB
expect 'a scan covering 0 of N commits refuses' 1 'COULD NOT ESTABLISH' -

stub_gitleaks <<'STUB'
#!/bin/sh
echo "11:00AM INF scan complete"
exit 0
STUB
expect 'output carrying no commit count refuses' 1 'COULD NOT ESTABLISH' -

stub_gitleaks <<'STUB'
#!/bin/sh
echo "11:00AM INF 9 commits scanned."
exit 2
STUB
expect 'a scan that crashed after counting refuses' 1 'COULD NOT ESTABLISH' -

# The inverse, so the guard above is a filter and not a blanket refusal: a real
# leak must be reported as a leak, not as could-not-establish.
stub_gitleaks <<'STUB'
#!/bin/sh
echo "11:00AM INF 9 commits scanned."
echo "11:00AM WRN leak found"
exit 1
STUB
expect 'a genuine leak reports as a leak, not as unreadable' 1 'secrets found' 'COULD NOT ESTABLISH'

unset STUB_PATH

# The guard is deliberately "it read something", not "it read every commit", and
# this is the case that stops the stricter version being reinstated. Measured on
# gitleaks 8.30.1: it counts commits that contributed added content, so an empty
# commit and every merge commit go uncounted. A repository with a merge scans
# strictly fewer commits than `git rev-list --count --all` reports and is clean.
merged="$workdir/merged"
git init --quiet -b main "$merged"
cp "$CONFIG" "$merged/.gitleaks.toml"
(
    cd "$merged" || exit 1
    git config user.name test
    git config user.email test@example.invalid
    echo one >one.txt && git add -A && git commit --quiet -m one
    echo two >two.txt && git add -A && git commit --quiet -m two
    git checkout --quiet -b side HEAD~1
    echo side >side.txt && git add -A && git commit --quiet -m side
    git checkout --quiet main
    git merge --quiet --no-ff side -m merge
    git commit --quiet --allow-empty -m empty
) || exit 2

# The premise is asserted, not assumed: without a scan count genuinely below
# git's own the case would pass while proving nothing.
merged_expected=$(cd "$merged" && git rev-list --count --all)
merged_scanned=$(cd "$merged" && gitleaks git . --config .gitleaks.toml --no-banner \
    --log-level info 2>&1 | sed -n 's/.*[^0-9]\([0-9][0-9]*\) commits scanned\..*/\1/p' | tail -1)
if [ "${merged_scanned:-0}" -lt "$merged_expected" ]; then
    SCAN_DIR="$merged" expect \
        'a history with merge and empty commits passes despite a lower scan count' \
        0 'no leaks found' 'COULD NOT ESTABLISH'
else
    printf 'FAIL merge/empty fixture scanned %s of %s commits; the case proves nothing\n' \
        "${merged_scanned:-<none>}" "$merged_expected"
    failed=1
fi

# --- fail closed: the checkout itself ----------------------------------------

# `fetch-depth: 0` is what makes the full-history claim true. A shallow
# checkout scans the tip with real gitleaks, exits 0, and pronounces a
# repository clean whose history was never fetched.
shallow="$workdir/shallow"
if git clone --quiet --depth 1 "file://$repo" "$shallow" 2>/dev/null; then
    cp "$CONFIG" "$shallow/.gitleaks.toml"
    SCAN_DIR="$shallow" expect 'a shallow checkout refuses' 1 'shallow' 'no leaks found'
else
    printf 'FAIL could not build a shallow clone to test against\n'
    failed=1
fi

# Not a git repository at all: the case where gitleaks' own exit code is 0 and
# every other signal has to carry the verdict.
notrepo="$workdir/notrepo"
mkdir -p "$notrepo"
cp "$CONFIG" "$notrepo/.gitleaks.toml"
SCAN_DIR="$notrepo" expect 'a target that is not a repository refuses' \
    nonzero 'COULD NOT ESTABLISH' 'no leaks found'

if [ "$failed" -ne 0 ]; then
    printf '\033[31mtest-secret-scan: failures above\033[0m\n' >&2
    exit 1
fi
printf '\033[32mok\033[0m shared-secret-scan detects secrets and refuses when it cannot establish\n'
