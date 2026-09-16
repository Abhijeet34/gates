#!/usr/bin/env bash
# Prove sync.sh reaches a LINKED WORKTREE, and still refuses what it should.
#
#   .ci/gitleaks/test-sync.sh
#
# The distributor guarded its targets with `[ -d "$target/.git" ]`, and in a
# linked worktree `.git` is a pointer file, so it refused every worktree - which
# is where every task in this fleet runs, so the documented one-command re-sync
# had never been runnable in the place it is needed. It was found when the
# no-mistakes mirror's .gitleaks.toml had drifted and the fix would not run.
#
# Both directions are pinned: an ordinary checkout and a worktree both install
# and verify clean, while a plain directory and a subdirectory of a repository
# are still refused - a target that is not a working tree's top level would
# scatter the two files where nothing reads them.
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P) || exit 2
SYNC="$ROOT/.ci/gitleaks/sync.sh"
CONFIG="$ROOT/.gitleaks.toml"
HOOK="$ROOT/.githooks/pre-push"
[ -x "$SYNC" ] || { echo "test-sync: cannot execute $SYNC" >&2; exit 2; }

TMP=$(mktemp -d) || exit 2
trap 'rm -rf "$TMP"' EXIT

# Same reason as test-pre-push.sh: the fixtures must not inherit this machine's
# core.hooksPath, and sync.sh sets that key as part of installing.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

failed=0
check() { # description expected-rc actual-rc
    [ "$2" -eq "$3" ] && return 0
    printf '\033[31mFAIL\033[0m %s (expected exit %s, got %s)\n' "$1" "$2" "$3" >&2
    failed=$((failed + 1))
}

git init --quiet -b main "$TMP/checkout"
git -C "$TMP/checkout" config user.name test
git -C "$TMP/checkout" config user.email test@example.invalid
git -C "$TMP/checkout" config commit.gpgsign false
echo seed >"$TMP/checkout/README.md"
git -C "$TMP/checkout" add -A
git -C "$TMP/checkout" commit --quiet -m seed
git -C "$TMP/checkout" worktree add --quiet -b feature "$TMP/worktree"

[ -f "$TMP/worktree/.git" ] || { echo "test-sync: fixture worktree has no .git pointer file" >&2; exit 2; }

for target in "$TMP/checkout" "$TMP/worktree"; do
    kind=$(basename "$target")

    "$SYNC" "$target" >/dev/null 2>&1
    check "sync installs into a $kind" 0 $?

    "$SYNC" --check "$target" >/dev/null 2>&1
    check "--check is clean right after installing into a $kind" 0 $?

    [ -x "$target/.githooks/pre-push" ] && rc=0 || rc=1
    check "the hook lands executable in a $kind" 0 "$rc"

    printf '\n# drift\n' >>"$target/.gitleaks.toml"
    "$SYNC" --check "$target" >/dev/null 2>&1
    check "--check reports an edited config in a $kind" 1 $?

    "$SYNC" "$target" >/dev/null 2>&1
    "$SYNC" --check "$target" >/dev/null 2>&1
    check "re-syncing a $kind restores the canonical copy" 0 $?

    # The hook half of the same question. It went unasserted here while the
    # config half was pinned in CI as well, which is the asymmetry that let four
    # repositories run a hook 27 lines behind canonical with every check green.
    printf '\n# drift\n' >>"$target/.githooks/pre-push"
    "$SYNC" --check "$target" >/dev/null 2>&1
    check "--check reports an edited hook in a $kind" 1 $?

    "$SYNC" "$target" >/dev/null 2>&1
    "$SYNC" --check "$target" >/dev/null 2>&1
    check "re-syncing a $kind restores the canonical hook" 0 $?

    # Byte-perfect content, and the gate does not run. The exec bit is asked in
    # CI too, of the committed mode; core.hooksPath is asked only here, because
    # it is repository config no commit and no checkout carries.
    chmod -x "$target/.githooks/pre-push"
    "$SYNC" --check "$target" >/dev/null 2>&1
    check "--check reports a hook that is not executable in a $kind" 1 $?
    chmod +x "$target/.githooks/pre-push"

    git -C "$target" config --unset core.hooksPath
    "$SYNC" --check "$target" >/dev/null 2>&1
    check "--check reports core.hooksPath unset in a $kind" 1 $?
    git -C "$target" config core.hooksPath .githooks
done

mkdir -p "$TMP/plain" "$TMP/worktree/sub"
"$SYNC" --check "$TMP/plain" >/dev/null 2>&1
check "a directory that is no working tree is refused" 1 $?
"$SYNC" --check "$TMP/worktree/sub" >/dev/null 2>&1
check "a subdirectory of a working tree is refused" 1 $?
# A bare repository answers an empty --show-prefix at exit 0, so it is the one
# target the top-level test cannot refuse on its own.
git init --quiet --bare "$TMP/bare.git"
"$SYNC" --check "$TMP/bare.git" >/dev/null 2>&1
check "a bare repository is refused" 1 $?

# The digests are what shared-secret-scan.yml pins, one per synced file, so each
# must answer without a target and answer its own canonical file. A `--digest`
# that printed the wrong file's hash would pin the workflow to a value no caller
# can ever match, so the pairing is asserted rather than the format.
digest=$("$SYNC" --digest)
check "--digest exits clean with no target" 0 $?
[ "$digest" = "$(shasum -a 256 "$CONFIG" | cut -d' ' -f1)" ] && rc=0 || rc=1
check "--digest prints the canonical config's sha256" 0 "$rc"

hook_digest=$("$SYNC" --digest hook)
check "--digest hook exits clean with no target" 0 $?
[ "$hook_digest" = "$(shasum -a 256 "$HOOK" | cut -d' ' -f1)" ] && rc=0 || rc=1
check "--digest hook prints the canonical hook's sha256" 0 "$rc"
[ "$hook_digest" != "$digest" ] && rc=0 || rc=1
check "the two digests are of different files" 0 "$rc"

"$SYNC" --digest nonsense >/dev/null 2>&1
check "--digest refuses a name that is neither file" 2 $?

# A clean --check is silent about everything EXCEPT what it opened, and that one
# line is the difference between "no drift" and "reached nothing". The topgrade
# fleet step reads this output, so a silent success there would certify five
# repositories nobody looked at.
out=$("$SYNC" --check "$TMP/checkout" 2>&1)
[ "$out" = "checked $TMP/checkout" ] && rc=0 || rc=1
check "a clean --check names the repository it examined" 0 "$rc"
[ -z "$("$SYNC" --check "$TMP/plain" 2>/dev/null)" ] && rc=0 || rc=1
check "a target it could not examine gets no 'checked' line" 0 "$rc"

[ "$failed" -eq 0 ] && printf 'test-sync: all cases passed\n'
exit $((failed > 0))
