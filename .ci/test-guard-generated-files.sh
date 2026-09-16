#!/usr/bin/env bash
# Regression tests for the check body inside
# .github/workflows/shared-guard-generated-files.yml.
#
# That body is the only copy of the guard logic for five repositories, and it
# is a merge of four variants that had each been fixed differently. The cases
# below are the union of what those variants pinned: tasks-axi carried them as
# a vitest suite against a repo-local script, which the move to a shared
# workflow removes, so they live here now.
#
# The logic cannot be a script in this repository: a reusable workflow's steps
# run in the CALLER's checkout, and GITHUB_TOKEN cannot clone this private repo
# to fetch one. So the workflow holds the only copy and this test extracts it.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

WORKFLOW=.github/workflows/shared-guard-generated-files.yml
STEP='name: Check PR does not modify release-please-generated files'

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

# No global or system git config, as in .ci/gitleaks/test-pre-push.sh: the
# fixtures must not run this machine's core.hooksPath, whose hooks would run
# inside a fixture the trap above is already deleting. Its diff.* settings would
# leak into the check body too.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

guard="$workdir/guard.sh"

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
' "$WORKFLOW" >"$guard"

failed=0

# Extraction is the one failure that would make every case below pass
# vacuously, so it is asserted rather than assumed - at both ends. The awk above
# stops at the first line indented less than the block, so a continuation line
# written at column 2 inside a multi-line string truncates the body silently and
# every case then fails on a syntax error at whatever line it stopped on.
# Measured while writing this section, on the `notes=` assignment.
for marker in 'find-copies-harder' 'No release-please-generated files modified. OK.'; do
    if ! grep -qF "$marker" "$guard"; then
        printf 'FAIL check body extracted from %s is missing %s\n' "$WORKFLOW" "$marker"
        exit 1
    fi
done

git_q() { git -C "$1" "${@:2}" >/dev/null 2>&1; }

# Fresh repo with one seed commit; echoes its path.
init_repo() {
    local repo
    repo=$(mktemp -d "$workdir/repo.XXXXXX")
    git_q "$repo" init -b main
    git_q "$repo" config user.email test@example.com
    git_q "$repo" config user.name 'Test User'
    git_q "$repo" config commit.gpgsign false
    printf '%s\n' "$repo"
}

commit() {
    local repo="$1"
    git_q "$repo" add -A
    git_q "$repo" commit -m "$2"
    git -C "$repo" rev-parse HEAD
}

# $1 name, $2 expected exit (or `nonzero`), $3 allow_first_add, $4 repo,
# $5 base, $6 head
expect() {
    local name="$1" want="$2" allow="$3" repo="$4" base="$5" head="$6" got ok
    (
        cd "$repo" || exit 127
        BASE_SHA="$base" HEAD_SHA="$head" ALLOW_FIRST_ADD="$allow" bash -e "$guard"
    ) >"$workdir/out" 2>"$workdir/err"
    got=$?
    if [[ $want == nonzero ]]; then
        ((got != 0)) && ok=1 || ok=0
    else
        ((got == want)) && ok=1 || ok=0
    fi
    if ((ok)); then
        printf 'ok   %s\n' "$name"
    else
        printf 'FAIL %s: expected exit %s, got %d\n' "$name" "$want" "$got"
        sed 's/^/       /' "$workdir/err"
        failed=1
    fi
}

# Asserts against the stderr of the expect() immediately above. A refusal that
# names nothing is a puzzle, and only the exit code is otherwise pinned.
said() { # $1 name, $2 substring
    if grep -qF -- "$2" "$workdir/err"; then
        printf 'ok   %s\n' "$1"
    else
        printf 'FAIL %s: the refusal never said %s\n' "$1" "$2"
        sed 's/^/       /' "$workdir/err"
        failed=1
    fi
}

# --- first add: the case the callers disagree about -------------------------

# tasks-axi and quota-axi both allow the manifest's first appearance.
repo=$(init_repo)
echo seed >"$repo/README.md"
base=$(commit "$repo" seed)
echo '{}' >"$repo/.release-please-manifest.json"
head=$(commit "$repo" 'add manifest')
expect 'first add of an allowed path passes' 0 '.release-please-manifest.json' "$repo" "$base" "$head"
expect 'first add of an unlisted path fails' 1 '' "$repo" "$base" "$head"

# tasks-axi rejects a first-added CHANGELOG.md; quota-axi allows it. Both
# behaviours have to remain reachable from the input.
repo=$(init_repo)
echo seed >"$repo/README.md"
base=$(commit "$repo" seed)
echo '# Changelog' >"$repo/CHANGELOG.md"
head=$(commit "$repo" 'add changelog')
expect 'first add of a changelog fails when only the manifest is allowed' 1 \
    '.release-please-manifest.json' "$repo" "$base" "$head"
expect 'first add of a changelog passes when it is allowed too' 0 \
    '.release-please-manifest.json
CHANGELOG.md' "$repo" "$base" "$head"

# --- edits to a file that already exists ------------------------------------

repo=$(init_repo)
echo '# Changelog' >"$repo/CHANGELOG.md"
base=$(commit "$repo" 'seed changelog')
echo 'hand-written note' >>"$repo/CHANGELOG.md"
head=$(commit "$repo" 'edit changelog')
expect 'modifying an existing generated file fails even when it is allowed' 1 \
    'CHANGELOG.md' "$repo" "$base" "$head"

repo=$(init_repo)
echo seed >"$repo/README.md"
echo '{}' >"$repo/.release-please-manifest.json"
base=$(commit "$repo" seed)
rm "$repo/.release-please-manifest.json"
head=$(commit "$repo" 'delete manifest')
expect 'deleting an existing generated file fails' 1 '.release-please-manifest.json' \
    "$repo" "$base" "$head"

# --- rename and copy: only tasks-axi's variant caught these -----------------

repo=$(init_repo)
echo '# Changelog' >"$repo/CHANGELOG.md"
base=$(commit "$repo" 'seed changelog')
git_q "$repo" mv CHANGELOG.md NOTES.md
head=$(commit "$repo" 'rename changelog')
expect 'renaming an existing generated file fails' 1 '' "$repo" "$base" "$head"

repo=$(init_repo)
printf '{".":"0.1.0"}\n' >"$repo/README.md"
base=$(commit "$repo" seed)
printf '{".":"0.1.0"}\n' >"$repo/.release-please-manifest.json"
head=$(commit "$repo" 'copy manifest')
expect 'copying a file into a generated path fails despite the first-add allowance' 1 \
    '.release-please-manifest.json' "$repo" "$base" "$head"

# --- provenance: a merge is not a hand edit ---------------------------------

# The case that reddened every fork's upstream sync (lavish-axi#18): the branch
# merges upstream, whose own release commits wrote both generated files, and
# types nothing into either. Built once and then walked forward, so the passing
# and the failing side are the same branch one commit apart.
repo=$(init_repo)
printf '# Changelog\n\n## 0.1.52\n' >"$repo/CHANGELOG.md"
printf '{".":"0.1.52"}\n' >"$repo/.release-please-manifest.json"
echo lib >"$repo/src.txt"
base=$(commit "$repo" 'seed at 0.1.52')
git_q "$repo" checkout -b upstream
echo 'lib, fixed upstream' >"$repo/src.txt"
commit "$repo" 'fix: upstream bug' >/dev/null
printf '# Changelog\n\n## 0.1.53\n\n## 0.1.52\n' >"$repo/CHANGELOG.md"
printf '{".":"0.1.53"}\n' >"$repo/.release-please-manifest.json"
commit "$repo" 'chore(main): release 0.1.53' >/dev/null
git_q "$repo" checkout -b sync main
echo note >"$repo/FORK.md"
commit "$repo" 'docs: fork note' >/dev/null
git_q "$repo" merge --no-edit upstream
head=$(git -C "$repo" rev-parse HEAD)
expect 'a merge carrying upstream release commits passes' 0 '' "$repo" "$base" "$head"

# Still upstream's content, so still not a hand edit, however far down the
# branch the merge now sits.
echo 'more fork work' >>"$repo/FORK.md"
head=$(commit "$repo" 'docs: more fork notes')
expect 'a later unrelated commit does not re-arm the merge' 0 '' "$repo" "$base" "$head"

echo 'hand-written note' >>"$repo/CHANGELOG.md"
head=$(commit "$repo" 'edit changelog after the merge')
expect 'a hand edit after that merge still fails' 1 '' "$repo" "$base" "$head"

# The other half of the provenance rule: content the MERGE COMMIT itself
# invented is nobody's parent's, so a resolution that types into a generated
# file is caught even though a merge is what carries it.
repo=$(init_repo)
printf '# Changelog\n\n## 0.1.52\n' >"$repo/CHANGELOG.md"
base=$(commit "$repo" 'seed at 0.1.52')
git_q "$repo" checkout -b upstream
printf '# Changelog\n\n## 0.1.53\n\n## 0.1.52\n' >"$repo/CHANGELOG.md"
commit "$repo" 'chore(main): release 0.1.53' >/dev/null
git_q "$repo" checkout -b sync main
echo note >"$repo/FORK.md"
commit "$repo" 'docs: fork note' >/dev/null
git_q "$repo" merge --no-commit --no-ff upstream
printf 'slipped in during the merge\n' >>"$repo/CHANGELOG.md"
head=$(commit "$repo" 'merge upstream')
expect 'content invented by the merge commit fails' 1 '' "$repo" "$base" "$head"

# --- declared one-time exceptions -------------------------------------------
#
# The case these exist for is Abhijeet34/lavish-axi#31, which truncates
# CHANGELOG.md from 449 lines to 22 at the fork point. That is a designed change
# to a generated file rather than a slip - release-please 17.11.2's Changelog
# updater inserts before the first `\n###? v?[0-9[]` match and, with no match
# left, demotes every H1 and buries the prose, so one inherited version heading
# has to stay - and the guard cannot tell it from a hand edit.
# `.ci/fixtures/lavish-axi-31-CHANGELOG.md` is that pull request's CHANGELOG.md
# at HEAD, byte for byte.
#
# Every property of the mechanism has a case here that goes red if that property
# is deleted from the workflow: remove the parser and `passes` fails, remove the
# blob comparison and `wrong blob` passes, remove the base-branch comparison and
# `already spent` passes, remove the strict parse and the four malformed files
# pass.
#
# The base side of each diff is synthesised, not captured. The guard's verdict
# reads the diff STATUS, the blob at HEAD and the declaration, never the base
# content, so a captured 449-line original would change no assertion here while
# adding 135 lines naming an upstream maintainer to a repository that has been
# removing them.

PR31_BLOB=a68c31a36fe2de13f8e148e0d610e8032da05860
PR31_FIXTURE=.ci/fixtures/lavish-axi-31-CHANGELOG.md

# Without this the suite would happily certify a lookalike: every case below
# rests on the fixture still being the content GitHub reports under that blob id
# for lavish-axi#31, and nothing else here would notice an edit to it.
if [[ $(git hash-object "$PR31_FIXTURE") != "$PR31_BLOB" ]]; then
    printf 'FAIL %s is no longer blob %s (lavish-axi#31 HEAD)\n' "$PR31_FIXTURE" "$PR31_BLOB"
    failed=1
fi

# Write a declaration file into $1's working tree from stdin.
declare_exception() {
    mkdir -p "$1/.github"
    cat >"$1/.github/generated-file-exceptions"
}

# A repository sitting where lavish-axi's main sits: an inherited changelog
# opening on someone else's release line, and a manifest already reset to ours.
pr31_repo() {
    local repo v
    repo=$(init_repo)
    {
        printf '# Changelog\n\n'
        for v in 53 52 51 50; do
            printf '## [0.1.%d](https://example.invalid/compare) (2026-08-18)\n\n' "$v"
            printf '### Bug Fixes\n\n* an inherited entry\n\n' 
        done
    } >"$repo/CHANGELOG.md"
    printf '{".":"0.1.0"}\n' >"$repo/.release-please-manifest.json"
    printf '%s\n' "$repo"
}

# The red half, on the real content: this is what lavish-axi#31 gets today.
repo=$(pr31_repo)
base=$(commit "$repo" 'seed at the fork point')
cp "$PR31_FIXTURE" "$repo/CHANGELOG.md"
head=$(commit "$repo" 'docs(changelog): truncate to our own release line')
expect 'lavish-axi#31 without a declaration fails' 1 '' "$repo" "$base" "$head"

# A declaration file carrying no stanza is not a loosening either.
declare_exception "$repo" <<'DECL'
# Nothing declared yet.
DECL
head=$(commit "$repo" 'add an empty declaration file')
expect 'a comment-only declaration file changes nothing' 1 '' "$repo" "$base" "$head"

# The green half: the same change, declared.
repo=$(pr31_repo)
base=$(commit "$repo" 'seed at the fork point')
cp "$PR31_FIXTURE" "$repo/CHANGELOG.md"
declare_exception "$repo" <<DECL
path: CHANGELOG.md
blob: ${PR31_BLOB}
reason: The file opened on 0.1.53 and descended through 52 inherited entries, so
  the first version heading - what a reader and release-please both take for the
  current release - asserted a release line this repository has never cut.
one-time: The truncation happens once, at the fork point. Every later entry is
  written by release-please above the retained 0.1.53 heading, so no second
  rewrite of this file is reachable from here.
DECL
head=$(commit "$repo" 'docs(changelog): truncate to our own release line')
expect 'lavish-axi#31 with its declaration passes' 0 '' "$repo" "$base" "$head"

# The declaration excuses one CONTENT, not the path: a further edit on the same
# branch is a different blob and the stanza stops applying.
printf '\n* and one more line, typed by hand\n' >>"$repo/CHANGELOG.md"
head=$(commit "$repo" 'edit the changelog again')
expect 'an edit past the declared blob fails' 1 '' "$repo" "$base" "$head"

# Same change, same repository, a declaration that is valid for a path this pull
# request does not touch. The manifest blob is the real one at HEAD, so the only
# thing wrong with this stanza is which file it names.
repo=$(pr31_repo)
base=$(commit "$repo" 'seed at the fork point')
cp "$PR31_FIXTURE" "$repo/CHANGELOG.md"
manifest_blob=$(git -C "$repo" hash-object .release-please-manifest.json)
declare_exception "$repo" <<DECL
path: .release-please-manifest.json
blob: ${manifest_blob}
reason: Declares the file this pull request did not change.
one-time: Names the wrong path, so it excuses nothing.
DECL
head=$(commit "$repo" 'docs(changelog): truncate to our own release line')
expect 'a declaration naming a different path fails' 1 '' "$repo" "$base" "$head"
said 'and names what it did declare instead' 'no stanza names it'

# Right path, wrong content: the blob declared is the changelog BEFORE the
# truncation, which is exactly the mistake of declaring one edit and shipping
# another.
repo=$(pr31_repo)
base=$(commit "$repo" 'seed at the fork point')
stale_blob=$(git -C "$repo" hash-object CHANGELOG.md)
cp "$PR31_FIXTURE" "$repo/CHANGELOG.md"
declare_exception "$repo" <<DECL
path: CHANGELOG.md
blob: ${stale_blob}
reason: Declares the content that was already there.
one-time: Pins a blob this pull request replaces.
DECL
head=$(commit "$repo" 'docs(changelog): truncate to our own release line')
expect 'a declaration naming the wrong blob fails' 1 '' "$repo" "$base" "$head"
said 'and names both blobs' "the declared blob is ${stale_blob}, but HEAD holds ${PR31_BLOB}"

# Spent. Landing a permissive stanza in one pull request and drawing on it in
# the next is the shape the base-branch comparison exists to refuse, so both
# halves are asserted: the staging pull request passes, because it touches no
# generated file, and the one that would use it does not.
repo=$(pr31_repo)
seed=$(commit "$repo" 'seed at the fork point')
declare_exception "$repo" <<DECL
path: CHANGELOG.md
blob: ${PR31_BLOB}
reason: Landed on its own, ahead of the change it would excuse.
one-time: Claims to be one-time; the point is that saying so is not enough.
DECL
staged=$(commit "$repo" 'chore: declare an exception and nothing else')
expect 'a declaration landing on its own passes' 0 '' "$repo" "$seed" "$staged"
cp "$PR31_FIXTURE" "$repo/CHANGELOG.md"
head=$(commit "$repo" 'docs(changelog): truncate to our own release line')
expect 'a declaration already on the base branch is spent and fails' 1 '' "$repo" "$staged" "$head"
said 'and says it was spent' 'already on the base branch, so it has been spent'

# There is no blob to pin when the content is going away, so neither shape is
# declarable and both stay a human read.
repo=$(pr31_repo)
base=$(commit "$repo" 'seed at the fork point')
rm "$repo/CHANGELOG.md"
declare_exception "$repo" <<DECL
path: CHANGELOG.md
blob: ${PR31_BLOB}
reason: Tries to declare a deletion.
one-time: There is no content at HEAD for the blob to pin.
DECL
head=$(commit "$repo" 'delete the changelog')
expect 'a declaration cannot excuse a deletion' 1 '' "$repo" "$base" "$head"

repo=$(pr31_repo)
base=$(commit "$repo" 'seed at the fork point')
git_q "$repo" mv CHANGELOG.md NOTES.md
moved_blob=$(git -C "$repo" hash-object NOTES.md)
declare_exception "$repo" <<DECL
path: CHANGELOG.md
blob: ${moved_blob}
reason: Tries to declare a rename away from a generated path.
one-time: The content left CHANGELOG.md, so nothing at that path is pinned.
DECL
head=$(commit "$repo" 'rename the changelog')
expect 'a declaration cannot excuse a rename away' 1 '' "$repo" "$base" "$head"

# A malformed declaration fails the step on the pull request that malforms it,
# even when that pull request touches nothing generated - the parse is not
# conditional on there being a violation to excuse. Each of the four is a way a
# stanza can look declarative and mean nothing.
malformed_case() { # $1 name, $2 stanza on stdin
    local repo base head
    repo=$(init_repo)
    echo seed >"$repo/README.md"
    base=$(commit "$repo" seed)
    declare_exception "$repo"
    echo more >>"$repo/README.md"
    head=$(commit "$repo" 'an innocent change')
    expect "$1" nonzero '' "$repo" "$base" "$head"
}

malformed_case 'a stanza with no one-time justification fails the step' <<DECL
path: CHANGELOG.md
blob: ${PR31_BLOB}
reason: Says why, never says why once.
DECL

malformed_case 'a stanza carrying an unknown key fails the step' <<DECL
path: CHANGELOG.md
blob: ${PR31_BLOB}
reason: Everything a stanza needs.
one-time: Plus a key nobody reads.
expires: 2099-01-01
DECL

malformed_case 'a stanza naming a file the guard does not own fails the step' <<DECL
path: README.md
blob: ${PR31_BLOB}
reason: README.md is not release-please's to write.
one-time: Nothing here is guarded, so nothing here is declarable.
DECL

malformed_case 'a stanza whose blob is not an object id fails the step' <<DECL
path: CHANGELOG.md
blob: the-one-i-meant
reason: A blob that pins nothing pins nothing.
one-time: Reads as a declaration, matches no content.
DECL

# --- the properties above, deleted one at a time ----------------------------
#
# A case can pass for the wrong reason, and two of these did while this section
# was being written: `a declaration landing on its own passes` is green with the
# apply rules deleted, because that pull request touches nothing generated, and
# `a declaration cannot excuse a deletion` is green with the blob comparison
# deleted, because a path with no content at HEAD never enters the declared set
# at all. Neither is a defect and neither is evidence, so neither is asserted
# here. What is asserted is that each property has an input that turns green -
# wrongly allowed - the moment the property leaves the workflow.
#
# The mutation is applied to the extracted body, not to the workflow, and every
# sed is checked for having changed something: a mutation that silently no-ops
# certifies a property nobody measured, which is the defect this section exists
# to refuse.

# $1 name, $2 sed program, $3 repo, $4 base, $5 head. Asserts the real guard
# refuses and the mutant allows.
expect_load_bearing() {
    local name="$1" mutation="$2" repo="$3" base="$4" head="$5" mutant="$workdir/mutant.sh" got
    sed "$mutation" "$guard" >"$mutant"
    if cmp -s "$guard" "$mutant"; then
        printf 'FAIL %s: the mutation changed nothing, so it proves nothing\n' "$name"
        failed=1
        return
    fi
    ( cd "$repo" && BASE_SHA="$base" HEAD_SHA="$head" ALLOW_FIRST_ADD="" bash -e "$guard" ) \
        >/dev/null 2>&1
    got=$?
    if ((got == 0)); then
        printf 'FAIL %s: the unmutated guard already allows this input\n' "$name"
        failed=1
        return
    fi
    ( cd "$repo" && BASE_SHA="$base" HEAD_SHA="$head" ALLOW_FIRST_ADD="" bash -e "$mutant" ) \
        >/dev/null 2>&1
    got=$?
    if ((got == 0)); then
        printf 'ok   %s\n' "$name"
    else
        printf 'FAIL %s: still refused with the property removed (exit %d)\n' "$name" "$got"
        failed=1
    fi
}

# The wrong blob, declared for the right path.
repo=$(pr31_repo)
base=$(commit "$repo" 'seed at the fork point')
stale_blob=$(git -C "$repo" hash-object CHANGELOG.md)
cp "$PR31_FIXTURE" "$repo/CHANGELOG.md"
declare_exception "$repo" <<DECL
path: CHANGELOG.md
blob: ${stale_blob}
reason: Declares the content that was already there.
one-time: Pins a blob this pull request replaces.
DECL
head=$(commit "$repo" 'docs(changelog): truncate to our own release line')
expect_load_bearing 'the blob comparison is load-bearing' \
    's/grep -Fxq -- "$pair" || continue/grep -Fq -- "$path" || continue/' \
    "$repo" "$base" "$head"

# A stanza that reached the base branch on its own.
repo=$(pr31_repo)
seed=$(commit "$repo" 'seed at the fork point')
declare_exception "$repo" <<DECL
path: CHANGELOG.md
blob: ${PR31_BLOB}
reason: Landed on its own, ahead of the change it would excuse.
one-time: Claims to be one-time.
DECL
staged=$(commit "$repo" 'chore: declare an exception and nothing else')
cp "$PR31_FIXTURE" "$repo/CHANGELOG.md"
head=$(commit "$repo" 'docs(changelog): truncate to our own release line')
expect_load_bearing 'the base-branch comparison is load-bearing' \
    '/"\$decl_base" | grep/d' \
    "$repo" "$staged" "$head"

# A stanza with no one-time justification at all.
repo=$(pr31_repo)
base=$(commit "$repo" 'seed at the fork point')
cp "$PR31_FIXTURE" "$repo/CHANGELOG.md"
declare_exception "$repo" <<DECL
path: CHANGELOG.md
blob: ${PR31_BLOB}
reason: Says why, never says why once.
DECL
head=$(commit "$repo" 'docs(changelog): truncate to our own release line')
# Both halves of the strict parse, because either alone leaves a refusal: with
# only the exit relaxed the incomplete stanza still never prints, and with only
# the print relaxed the step still aborts on the exit. Relaxing both is what
# turns a stanza carrying no one-time justification into a working exception.
expect_load_bearing 'the required-field check is load-bearing' \
    's/exit bad ? 2 : 0/exit 0/; s/if (!bad) print/print/' \
    "$repo" "$base" "$head"

# --- the boring cases -------------------------------------------------------

repo=$(init_repo)
echo seed >"$repo/README.md"
base=$(commit "$repo" seed)
echo more >>"$repo/README.md"
head=$(commit "$repo" 'edit readme')
expect 'a pull request touching nothing generated passes' 0 '' "$repo" "$base" "$head"

# git's own exit code, whatever it is, must reach the step. The variant this
# replaced only guaranteed "not zero", so that is what is pinned.
expect 'an unresolvable diff range fails closed' nonzero '' "$repo" "$base" not-a-sha

exit "$failed"
