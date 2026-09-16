#!/usr/bin/env bash
# Prove the pre-push gate, rather than asserting it.
#
#   .ci/gitleaks/test-pre-push.sh
#
# Builds a throwaway repository with a local bare remote, installs the hook, and
# checks both directions: a clean push lands, and every way the scan can fail to
# establish that a push is clean refuses it. The refusal cases matter more than
# the detection case - a scanner that reports clean because it did not run is
# worse than no scanner, and gitleaks' own exit code does not distinguish them.
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P) || exit 2
HOOK="$ROOT/.githooks/pre-push"
CONFIG="$ROOT/.gitleaks.toml"
FIXTURE="$ROOT/.ci/gitleaks/fixtures/known-secrets.tsv"

for f in "$HOOK" "$CONFIG" "$FIXTURE"; do
    [ -r "$f" ] || { echo "test-pre-push: cannot read $f" >&2; exit 2; }
done
command -v gitleaks >/dev/null 2>&1 || { echo "test-pre-push: gitleaks not installed" >&2; exit 2; }

TMP=$(mktemp -d) || exit 2
trap 'rm -rf "$TMP"' EXIT

# No global or system git config: the fixture must not inherit this machine's
# core.hooksPath, and git-lfs must not be pulled into a repository that has none.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

SECRET=$(awk -F'\t' '$2 == "moonshot-kimi" { print $3 }' "$FIXTURE")
[ -n "$SECRET" ] || { echo "test-pre-push: fixture has no moonshot-kimi row" >&2; exit 2; }

failed=0
check() { # description expected-outcome actual-rc
    if [ "$2" = pass ] && [ "$3" -eq 0 ]; then return 0; fi
    if [ "$2" = refuse ] && [ "$3" -ne 0 ]; then return 0; fi
    printf '\033[31mFAIL\033[0m %s (expected %s, git push exited %s)\n' "$1" "$2" "$3" >&2
    failed=$((failed + 1))
}

# A real 67-byte 1x1 PNG, byte-identical every run. Deterministic because git
# calls a blob binary on a NUL in its first 8000 bytes: the `head -c 64
# /dev/urandom` this used to be was binary in only 35 of 200 trials, so four
# runs in five the "binary-only" cases below committed ordinary text that
# gitleaks scans - and passed against the pre-numstat hook they exist to catch.
binary_fixture() { # path
    printf '\211PNG\r\n\032\n\0\0\0\015IHDR\0\0\0\1\0\0\0\1\10\6\0\0\0\037\25\304\211\0\0\0\012IDATx\234c\0\1\0\0\5\0\1\015\012\055\264\0\0\0\0IEND\256B\140\202' > "$1"
    git add "$1"
    # The fixture is only a fixture while git agrees it is binary. A numeric
    # numstat is a commit gitleaks can read, so the case using it would pass
    # without ever reaching the unscannable path it is written for.
    if [ "$(git diff --cached --numstat -- "$1" | cut -f1)" != "-" ]; then
        printf '\033[31mFAIL\033[0m %s is not binary to git, so the case using it proves nothing\n' "$1" >&2
        failed=$((failed + 1))
    fi
}

git init --quiet --bare "$TMP/remote.git"
git init --quiet -b main "$TMP/work"
cd "$TMP/work" || exit 2
git config user.name test
git config user.email test@example.invalid
git remote add origin "$TMP/remote.git"
mkdir -p .githooks
cp "$HOOK" .githooks/pre-push
chmod +x .githooks/pre-push
cp "$CONFIG" .gitleaks.toml
git config core.hooksPath .githooks

echo "print('hello')" > app.py
git add -A && git commit --quiet -m "clean"
git push --quiet origin main >/dev/null 2>&1
check "a clean push lands" pass $?

# gitleaks skips merge commits, so the expected count must skip them too or a
# merge is an unpushable commit. This runs before the secret exists, because
# every later push is refused for carrying it.
git checkout --quiet -b side
echo "print('side')" > side.py
git add side.py && git commit --quiet -m "side"
git checkout --quiet main
echo "print('trunk')" > trunk.py
git add trunk.py && git commit --quiet -m "trunk"
git merge --quiet --no-ff -m "merge side" side
git push --quiet origin main >/dev/null 2>&1
check "a clean push carrying a merge commit lands" pass $?

# gitleaks counts only the commits it could read a patch from, and a commit
# whose every change is BINARY is not one of them - measured on 8.30.1, a
# one-commit range holding only a PNG reports "0 commits scanned". The old
# `rev-list --count --no-merges` expected 1 and refused the push, so a
# repository could not push an image on its own. Two cases, because the fix must
# not cost the check its teeth: the binary-only push must land, and a secret in
# a text file pushed alongside it must still be refused.
binary_fixture logo.png
git commit --quiet -m "a binary-only commit"
git push --quiet origin main >/dev/null 2>&1
check "a push whose only commit is binary lands" pass $?

binary_fixture logo2.png
git commit --quiet -m "another binary-only commit"
printf 'MODELS = {"kimi": "%s"}\n' "$SECRET" > leak_beside_binary.py
git add leak_beside_binary.py && git commit --quiet -m "a secret beside it"
git push origin main >/dev/null 2>&1
check "a secret pushed alongside a binary-only commit is still refused" refuse $?
git reset --quiet --hard origin/main

# --- whole-file deletions: the third skip in gitleaks' counter ----------------
#
# gitleaks 8.30.1 drops a whole-file deletion before it can emit a fragment
# (`if gitdiffFile.IsDelete { continue }`, sources/git.go), so a deletion-only
# commit reports "0 commits scanned" while `--numstat` alone prints a numeric
# `0\t1\tpath` for it and the hook refused the push. These cases were red
# against the hook as shipped on 2026-09-02 and are green with `--diff-filter=d`.
echo "print('d1')" > d1.py
git add d1.py && git commit --quiet -m "add d1"
git push --quiet origin main >/dev/null 2>&1
git rm --quiet d1.py && git commit --quiet -m "remove d1"
git push origin main >/dev/null 2>&1
check "a push whose only commit deletes a file lands" pass $?
git reset --quiet --hard origin/main

# The everyday way such a commit arrives. Guarded, because a push with nothing
# to push exits 0 vacuously: the first draft of this case passed against the
# unfixed hook because a bad flag meant the revert never ran.
echo "x = 1" > d2.py
git add d2.py && git commit --quiet -m "add d2"
git push --quiet origin main >/dev/null 2>&1
git revert --no-edit HEAD >/dev/null 2>&1
ahead=$(git rev-list --count origin/main..HEAD)
if [ "$ahead" -ne 1 ]; then
    printf '\033[31mFAIL\033[0m the revert case is vacuous: %s commits ahead, expected 1\n' "$ahead" >&2
    failed=$((failed + 1))
fi
git push origin main >/dev/null 2>&1
check "a push whose only commit reverts a file-add lands" pass $?
git reset --quiet --hard origin/main

# One deletion-only commit anywhere in a push made the whole push unpushable,
# clean additive work included - the blast radius the one-commit shape hides.
echo keep > k1.txt
git add k1.txt && git commit --quiet -m "add k1"
git rm --quiet k1.txt && git commit --quiet -m "remove k1"
echo "y = 2" > k2.py
git add k2.py && git commit --quiet -m "add k2"
git push origin main >/dev/null 2>&1
check "a deletion-only commit among additive commits lands" pass $?
git reset --quiet --hard origin/main

# The behavioural pin against upstream counter drift: one commit of every shape
# the matrix measured, in one push. No local predicate can survive gitleaks
# changing what it counts, and gitleaks arrives through unpinned brew, so this
# case is what turns such a change into a red suite at the next gated push
# instead of a machine-wide refusal. It fails if `--diff-filter=d` is removed.
echo "print('ks')" > ks_a.py
echo "echo ks" > ks_m.sh
echo renameme > ks_r.txt
echo typeme > ks_t.txt
git add ks_a.py ks_m.sh ks_r.txt ks_t.txt && git commit --quiet -m "ks additive"
binary_fixture ks.png
git commit --quiet -m "ks binary"
git rm --quiet ks_a.py && git commit --quiet -m "ks deletion"
chmod +x ks_m.sh && git add ks_m.sh && git commit --quiet -m "ks mode-only"
git mv ks_r.txt ks_r2.txt && git commit --quiet -m "ks rename-only"
rm -f ks_t.txt && ln -s ks_r2.txt ks_t.txt && git add ks_t.txt
git commit --quiet -m "ks typechange"
git commit --quiet --allow-empty -m "ks empty"
git checkout --quiet -b ks-side
echo "print('ks side')" > ks_side.py
git add ks_side.py && git commit --quiet -m "ks side"
git checkout --quiet main
git merge --quiet --no-ff -m "ks merge" ks-side
ahead=$(git rev-list --count origin/main..HEAD)
if [ "$ahead" -ne 9 ]; then
    printf '\033[31mFAIL\033[0m the kitchen-sink case is not the shape it claims: %s commits ahead, expected 9\n' "$ahead" >&2
    failed=$((failed + 1))
fi
git push origin main >/dev/null 2>&1
check "a push carrying one commit of every diff shape lands" pass $?
git reset --quiet --hard origin/main

# The teeth: narrowing the expected count must not let a secret ride beside a
# deletion-only commit, and the commit carrying it must not reach the remote.
echo drop > drop.txt
git add drop.txt && git commit --quiet -m "add drop"
git push --quiet origin main >/dev/null 2>&1
git rm --quiet drop.txt && git commit --quiet -m "remove drop"
printf 'MODELS = {"kimi": "%s"}\n' "$SECRET" > leak_beside_deletion.py
git add leak_beside_deletion.py && git commit --quiet -m "a secret beside a deletion"
DEL_LEAK_SHA=$(git rev-parse HEAD)
git push origin main >/dev/null 2>&1
check "a secret beside a deletion-only commit is still refused" refuse $?
if git --git-dir="$TMP/remote.git" cat-file -e "$DEL_LEAK_SHA^{commit}" 2>/dev/null; then
    printf '\033[31mFAIL\033[0m the secret-carrying commit reached the remote\n' >&2
    failed=$((failed + 1))
fi
git reset --quiet --hard origin/main

# Buffering stdin is the one step whose failure hides refs instead of reporting
# them: the scan loop reads that buffer, so a short write silently shrinks the
# set of refs examined and the push goes out unscanned. A `cat` that fails
# reproduces a full disk or a permission race without needing either. This runs
# while pushes still succeed, so a refusal can only come from the failed buffer.
mkdir -p "$TMP/stub"
cat > "$TMP/stub/cat" <<'STUB'
#!/bin/sh
while read -r _; do :; done
exit 1
STUB
chmod +x "$TMP/stub/cat"
echo "print('buffered')" > buffered.py
git add buffered.py && git commit --quiet -m "something to push"
PATH="$TMP/stub:$PATH" git push origin main >/dev/null 2>&1
check "a failed buffer of the ref list refuses" refuse $?
rm -f "$TMP/stub/cat"
git push --quiet origin main >/dev/null 2>&1
check "the same push lands once the buffer works" pass $?

printf 'MODELS = {"kimi": "%s"}\n' "$SECRET" > leak.py
git add leak.py && git commit --quiet -m "carries a secret"
LEAK_SHA=$(git rev-parse HEAD)
git push origin main >/dev/null 2>&1
check "a push carrying a secret is refused" refuse $?
if git --git-dir="$TMP/remote.git" cat-file -e "$LEAK_SHA^{commit}" 2>/dev/null; then
    printf '\033[31mFAIL\033[0m the secret-carrying commit reached the remote\n' >&2
    failed=$((failed + 1))
fi

# gitleaks absent. /usr/bin:/bin has git but not Homebrew's gitleaks.
PATH=/usr/bin:/bin git push origin main >/dev/null 2>&1
check "no gitleaks on PATH refuses" refuse $?

mv .gitleaks.toml .gitleaks.toml.away
git push origin main >/dev/null 2>&1
check "an unreadable config refuses" refuse $?
mv .gitleaks.toml.away .gitleaks.toml

# The measured fail-open: on an invalid range or a non-repository, gitleaks 8.30.1
# logs an error, reports "0 commits scanned" and exits 0. These two stubs
# reproduce each half of that signature and assert the hook is not fooled.
mkdir -p "$TMP/stub"
cat > "$TMP/stub/gitleaks" <<'STUB'
#!/bin/sh
echo "11:00AM ERR [git] fatal: Invalid revision range" >&2
echo "11:00AM INF 0 commits scanned."
echo "11:00AM INF no leaks found"
exit 0
STUB
chmod +x "$TMP/stub/gitleaks"
PATH="$TMP/stub:$PATH" git push origin main >/dev/null 2>&1
check "a scan that errors but exits 0 refuses" refuse $?

cat > "$TMP/stub/gitleaks" <<'STUB'
#!/bin/sh
echo "11:00AM INF 0 commits scanned."
echo "11:00AM INF no leaks found"
exit 0
STUB
err=$(PATH="$TMP/stub:$PATH" git push origin main 2>&1 >/dev/null)
check "a scan covering 0 of N commits refuses" refuse $?
# A short count is the one refusal an operator can meet on an ordinary push, and
# the only bypass git offers drops three other gates with it, so the message has
# to say so rather than leaving --no-verify as the obvious next move.
case "$err" in
    *"do not push --no-verify"*) ;;
    *) printf '\033[31mFAIL\033[0m the short-count refusal offers the operator no guidance\n' >&2
       failed=$((failed + 1)) ;;
esac

cat > "$TMP/stub/gitleaks" <<'STUB'
#!/bin/sh
echo "11:00AM INF scan complete"
exit 0
STUB
PATH="$TMP/stub:$PATH" git push origin main >/dev/null 2>&1
check "output with no commit count refuses" refuse $?

# A partial scan is the case no exit code can express: the same "N commits
# scanned / no leaks found / exit 0" is printed whether gitleaks read the whole
# range or one commit of it. A second unpushed commit makes the stub's count
# genuinely short rather than merely equal.
echo "print('more')" > more.py
git add more.py && git commit --quiet -m "second unpushed commit"
cat > "$TMP/stub/gitleaks" <<'STUB'
#!/bin/sh
echo "11:00AM INF 1 commits scanned."
echo "11:00AM INF no leaks found"
exit 0
STUB
PATH="$TMP/stub:$PATH" git push origin main >/dev/null 2>&1
check "a scan covering fewer commits than the range holds refuses" refuse $?

# Only after removing the secret does the push go through, which is what makes
# the refusals above meaningful rather than a hook that refuses everything.
git rm --quiet leak.py && git commit --quiet -m "remove the secret"
git push --quiet origin main >/dev/null 2>&1
check "a push is still refused while the secret is in the range" refuse $?

# --- the new-branch range: what the DESTINATION already has -------------------
#
# Every case above pushes either an existing branch or a new branch to an EMPTY
# destination, so none of them ever exercised a new branch going somewhere that
# already holds most of its history - which is the shape of every fork and of
# every no-mistakes delivery push, and the shape whose range used to be computed
# from remote-tracking refs that a push by URL does not have. That widened the
# scan to the branch's whole history and refused pushes over inherited commits.
SECRET2=$(awk -F'\t' '$2 == "deepseek" { print $3 }' "$FIXTURE")
[ -n "$SECRET2" ] || { echo "test-pre-push: fixture has no deepseek row" >&2; exit 2; }

# A fork, built the way one really arrives: c1 clean, c2 carrying a secret, c3
# removing it, all on the destination BEFORE the hook is armed, then one clean
# local commit that is the only thing a push actually publishes.
forked() { # <dir> - armed, history already on <dir>-github.git, tip is clean c4
    git init --quiet --bare "$1-github.git"
    git init --quiet -b main "$1"
    git -C "$1" config user.name test
    git -C "$1" config user.email test@example.invalid
    echo "print('c1')" > "$1/app.py"
    git -C "$1" add app.py && git -C "$1" commit --quiet -m c1
    printf 'MODELS = {"kimi": "%s"}\n' "$SECRET" > "$1/inherited.py"
    git -C "$1" add inherited.py && git -C "$1" commit --quiet -m c2
    git -C "$1" rm --quiet inherited.py && git -C "$1" commit --quiet -m c3
    git -C "$1" remote add origin "$1-github.git"
    git -C "$1" push --quiet origin main
    mkdir -p "$1/.githooks"
    cp "$HOOK" "$1/.githooks/pre-push"
    chmod +x "$1/.githooks/pre-push"
    cp "$CONFIG" "$1/.gitleaks.toml"
    git -C "$1" config core.hooksPath .githooks
    echo "print('c4')" > "$1/c4.py"
    git -C "$1" add c4.py && git -C "$1" commit --quiet -m c4
}

# N1. A push by URL has no remote-tracking refs anywhere, and every delivery
# push no-mistakes makes is one. The destination serves c1..c3 itself, so the
# only thing this publishes is c4.
forked "$TMP/fork1"
git -C "$TMP/fork1" push "$TMP/fork1-github.git" HEAD:refs/heads/pr-1 >/dev/null 2>&1
check "a new branch pushed by URL to a destination holding its history lands" pass $?

# N2. The pipeline's staging bare is genuinely empty, so the destination's
# advertisement excludes nothing and only the custody narrowing can tell
# inherited history from new work.
mkdir -p "$TMP/home/.no-mistakes/repos"
forked "$TMP/fork2"
git init --quiet --bare "$TMP/home/.no-mistakes/repos/gate2.git"
git -C "$TMP/fork2" remote add no-mistakes "$TMP/home/.no-mistakes/repos/gate2.git"
HOME="$TMP/home" git -C "$TMP/fork2" push no-mistakes main >/dev/null 2>&1
check "a first push to the pipeline's staging bare scans only work no remote has seen" pass $?

# N3. The teeth of N2: the narrowing excludes what a remote already carries and
# nothing else, so a secret in the new work is still refused - and must not
# reach the bare.
forked "$TMP/fork3"
git init --quiet --bare "$TMP/home/.no-mistakes/repos/gate3.git"
git -C "$TMP/fork3" remote add no-mistakes "$TMP/home/.no-mistakes/repos/gate3.git"
printf 'MODELS = {"deepseek": "%s"}\n' "$SECRET2" > "$TMP/fork3/new_leak.py"
git -C "$TMP/fork3" add new_leak.py && git -C "$TMP/fork3" commit --quiet -m c5
N3_SHA=$(git -C "$TMP/fork3" rev-parse HEAD)
HOME="$TMP/home" git -C "$TMP/fork3" push no-mistakes main >/dev/null 2>&1
check "a new secret in a first push to the staging bare is still refused" refuse $?
if git --git-dir="$TMP/home/.no-mistakes/repos/gate3.git" cat-file -e "$N3_SHA^{commit}" 2>/dev/null; then
    printf '\033[31mFAIL\033[0m the secret-carrying commit reached the staging bare\n' >&2
    failed=$((failed + 1))
fi

# N4. An empty advertisement means the destination holds none of this history,
# so the whole branch is genuinely being published and all of it is scanned.
# This is the case the fix must not narrow.
git init --quiet --bare "$TMP/n4-target.git"
git init --quiet -b main "$TMP/n4"
git -C "$TMP/n4" config user.name test
git -C "$TMP/n4" config user.email test@example.invalid
printf 'MODELS = {"kimi": "%s"}\n' "$SECRET" > "$TMP/n4/leak.py"
git -C "$TMP/n4" add leak.py && git -C "$TMP/n4" commit --quiet -m "old commit carrying a secret"
mkdir -p "$TMP/n4/.githooks"
cp "$HOOK" "$TMP/n4/.githooks/pre-push"
chmod +x "$TMP/n4/.githooks/pre-push"
cp "$CONFIG" "$TMP/n4/.gitleaks.toml"
git -C "$TMP/n4" config core.hooksPath .githooks
echo "print('later')" > "$TMP/n4/later.py"
git -C "$TMP/n4" add later.py && git -C "$TMP/n4" commit --quiet -m "a clean commit on top"
git -C "$TMP/n4" remote add target "$TMP/n4-target.git"
git -C "$TMP/n4" push target main >/dev/null 2>&1
check "a first push to a destination holding none of the history scans all of it" refuse $?

# N5. The fall-back is the only new way this range computation can fail, and it
# must fail toward the WIDER range. No real push can reach it in a local
# fixture - ls-remote and receive-pack take the same transport to the same URL,
# so a destination ls-remote cannot read is one the push cannot write either -
# so the hook is invoked directly, twice, with the same ref line and the same
# $1, and only the destination in $2 differing.
ZERO=0000000000000000000000000000000000000000
forked "$TMP/fork5"
REFLINE="refs/heads/main $(git -C "$TMP/fork5" rev-parse HEAD) refs/heads/pr-1 $ZERO"
( cd "$TMP/fork5" && printf '%s\n' "$REFLINE" | ./.githooks/pre-push nowhere "$TMP/nowhere.git" ) >/dev/null 2>&1
check "a destination that cannot be asked falls back to the wider range" refuse $?
( cd "$TMP/fork5" && printf '%s\n' "$REFLINE" | ./.githooks/pre-push nowhere "$TMP/fork5-github.git" ) >/dev/null 2>&1
check "the same ref line passes once that destination can be asked" pass $?

# N6. The advertisement has to come from where the push is GOING. $2 is that;
# $1 names the fetch URL, and `remote.<name>.pushurl` splits the two. Taking it
# from $1 excluded everything the fetch side held and sent a secret to an empty
# bare unscanned - a push the range this replaces refused.
git init --quiet --bare "$TMP/n6-fetch.git"
git init --quiet --bare "$TMP/n6-push.git"
git init --quiet -b main "$TMP/n6"
git -C "$TMP/n6" config user.name test
git -C "$TMP/n6" config user.email test@example.invalid
echo "print('n6')" > "$TMP/n6/app.py"
git -C "$TMP/n6" add app.py && git -C "$TMP/n6" commit --quiet -m c1
printf 'MODELS = {"kimi": "%s"}\n' "$SECRET" > "$TMP/n6/leak.py"
git -C "$TMP/n6" add leak.py && git -C "$TMP/n6" commit --quiet -m "c2 carries a secret"
N6_SHA=$(git -C "$TMP/n6" rev-parse HEAD)
echo "print('n6 c3')" > "$TMP/n6/more.py"
git -C "$TMP/n6" add more.py && git -C "$TMP/n6" commit --quiet -m c3
git -C "$TMP/n6" push --quiet "$TMP/n6-fetch.git" main
mkdir -p "$TMP/n6/.githooks"
cp "$HOOK" "$TMP/n6/.githooks/pre-push"
chmod +x "$TMP/n6/.githooks/pre-push"
cp "$CONFIG" "$TMP/n6/.gitleaks.toml"
git -C "$TMP/n6" config core.hooksPath .githooks
git -C "$TMP/n6" remote add mirror "$TMP/n6-fetch.git"
git -C "$TMP/n6" config remote.mirror.pushurl "$TMP/n6-push.git"
git -C "$TMP/n6" push mirror main >/dev/null 2>&1
check "a push to a pushurl the fetch url does not serve is scanned against the pushurl" refuse $?
if git --git-dir="$TMP/n6-push.git" cat-file -e "$N6_SHA^{commit}" 2>/dev/null; then
    printf '\033[31mFAIL\033[0m the secret-carrying commit reached the pushurl destination\n' >&2
    failed=$((failed + 1))
fi

# N7. The staging-bare narrowing is the one place this scans LESS than a plain
# "what the destination lacks", so it must fire on that path and nowhere else.
# Same repository, same commits, two equally empty destinations, the same $HOME:
# only the destination's path differs.
forked "$TMP/fork7"
git init --quiet --bare "$TMP/n7-outside.git"
git init --quiet --bare "$TMP/home/.no-mistakes/repos/gate7.git"
HOME="$TMP/home" git -C "$TMP/fork7" push "$TMP/n7-outside.git" main >/dev/null 2>&1
check "an empty destination outside the staging path is still scanned in full" refuse $?
HOME="$TMP/home" git -C "$TMP/fork7" push "$TMP/home/.no-mistakes/repos/gate7.git" main >/dev/null 2>&1
check "the same commits to an equally empty staging bare are narrowed" pass $?

# N8. The count cross-check has to keep guarding the narrowed range, not just
# the old wide one: gitleaks reports "0 commits scanned" and exit 0 on a range
# it could not read, so the push N1 now lets through must still refuse when the
# scan says it read nothing.
cat > "$TMP/stub/gitleaks" <<'STUB'
#!/bin/sh
echo "11:00AM INF 0 commits scanned."
echo "11:00AM INF no leaks found"
exit 0
STUB
chmod +x "$TMP/stub/gitleaks"
forked "$TMP/fork8"
PATH="$TMP/stub:$PATH" git -C "$TMP/fork8" push "$TMP/fork8-github.git" HEAD:refs/heads/pr-1 >/dev/null 2>&1
check "a scan reporting 0 commits still refuses on the narrowed new-branch range" refuse $?

if [ "$failed" -ne 0 ]; then
    printf '\033[31m%d pre-push checks failed\033[0m\n' "$failed" >&2
    exit 1
fi
printf '\033[32mok\033[0m pre-push gate refuses secrets and refuses when it cannot establish\n'
