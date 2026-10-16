#!/usr/bin/env bash
# Regression tests for the secret scan shared-secret-scan.yml runs,
# run/secret-scan.sh and its parts under run/secret-scan/.
#
#   .ci/test-secret-scan.sh
#
# The parts run in the CALLER's checkout from the gates commit its lock names.
# Without this file a broken part reaches main and every caller silently loses
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
# canonical config, real gitleaks) and test-no-mistakes-required.sh (a stub
# gitleaks on PATH for the cases real gitleaks cannot be made to produce).

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

PARTS="$PWD/run/secret-scan"
CONFIG=.gitleaks.toml
FIXTURE=.ci/gitleaks/fixtures/known-secrets.tsv

for f in "$PARTS/verify.sh" "$CONFIG" "$FIXTURE"; do
    [ -r "$f" ] || { echo "test-secret-scan: cannot read $f" >&2; exit 2; }
done
command -v gitleaks >/dev/null 2>&1 || { echo "test-secret-scan: gitleaks not installed" >&2; exit 2; }

workdir=$(mktemp -d) || exit 2
trap 'rm -rf "$workdir"' EXIT

failed=0

history_scan="$PARTS/history.sh"
tree_scan="$PARTS/tree.sh"
gate_step="$PARTS/verify.sh"

# --- the copies, compared with the gates checkout ----------------------------
#
# This runs the part that compares a caller's copies with gates as checked out
# for the run, against a fixture standing in for a caller's checkout. Only the
# config was compared until 2026-09-01: four repositories sat 27 lines behind
# canonical on the hook, and every check stayed green until a task tripped
# over it.
GATES="$PWD"

caller="$workdir/caller"
mkdir -p "$caller/.githooks"
cp "$CONFIG" "$caller/.gitleaks.toml"
cp .githooks/pre-push "$caller/.githooks/pre-push"
chmod +x "$caller/.githooks/pre-push"
cp system-maintenance/git-hooks/commit-msg "$caller/.githooks/commit-msg"
chmod +x "$caller/.githooks/commit-msg"

gate() { # name expected-rc substring-the-output-must-contain (or -)
    local name="$1" want="$2" must="$3" got ok=1
    (
        cd "$caller" || exit 2
        GATES_DIR="$GATES" bash -e "$gate_step"
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

printf '\n# drift\n' >>"$caller/.githooks/commit-msg"
gate 'a drifted commit-msg hook fails, naming the file' 1 '.githooks/commit-msg has DRIFTED'
cp system-maintenance/git-hooks/commit-msg "$caller/.githooks/commit-msg"
chmod -x "$caller/.githooks/commit-msg"
gate 'a commit-msg hook that is not executable fails' 1 '.githooks/commit-msg is not executable'
chmod +x "$caller/.githooks/commit-msg"
mv "$caller/.githooks/commit-msg" "$caller/.githooks/commit-msg.away"
gate 'a caller with no commit-msg hook at all fails' 1 '.githooks/commit-msg is missing'
mv "$caller/.githooks/commit-msg.away" "$caller/.githooks/commit-msg"
gate 'restoring the canonical commit-msg passes again' 0 -

# The half that already existed must survive the rewrite that added the other.
printf '\n# drift\n' >>"$caller/.gitleaks.toml"
gate 'a drifted .gitleaks.toml still fails' 1 '.gitleaks.toml has DRIFTED'
cp "$CONFIG" "$caller/.gitleaks.toml"
gate 'both copies canonical again passes' 0 -

# The lock may name a commit whose copies differ from the bootstrap's, and the
# copies are judged against the commit the run checked out, never a digest
# pinned in the workflow. A caller still carrying this tree's rules fails
# against gates checked out where the rules have moved on.
moved="$workdir/gates-moved"
mkdir -p "$moved/.githooks" "$moved/system-maintenance/git-hooks"
cp .githooks/pre-push "$moved/.githooks/pre-push"
cp system-maintenance/git-hooks/commit-msg "$moved/system-maintenance/git-hooks/commit-msg"
{ cat "$CONFIG"; printf '\n# a later release\n'; } >"$moved/.gitleaks.toml"
GATES="$moved" gate 'copies canonical at one commit fail against a checkout at another' 1 '.gitleaks.toml has DRIFTED'
rm "$moved/.gitleaks.toml"
GATES="$moved" gate 'a checkout carrying no canonical copy refuses' 1 'COULD NOT ESTABLISH'
GATES='' gate 'an unset gates checkout refuses' 1 'GATES_DIR'

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
