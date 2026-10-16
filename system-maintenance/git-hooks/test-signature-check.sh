#!/usr/bin/env bash
# Prove the signature-presence check, through the ref lines git feeds a pre-push hook.
#
#   system-maintenance/git-hooks/test-signature-check.sh
#
# The policy under test: main takes signed commits only, a branch may carry
# unsigned ones (an app-token commit is unsigned by design), so the check refuses
# a push to refs/heads/main that introduces an unsigned commit and is silent
# about every other ref. Offline; the signing key is generated here and used only
# in a throwaway repository.
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P) || exit 2
CHECK="$ROOT/system-maintenance/git-hooks/signature-check.py"
ZERO=0000000000000000000000000000000000000000
command -v ssh-keygen >/dev/null 2>&1 || { echo "test-signature-check: ssh-keygen not installed" >&2; exit 2; }
[ -x "$CHECK" ] || { printf '\033[31mFAIL\033[0m %s is missing or not executable\n' "$CHECK" >&2; exit 1; }

TMP=$(mktemp -d) || exit 2
trap 'rm -rf "$TMP"' EXIT
export HOME="$TMP/home" XDG_CONFIG_HOME="$TMP/home/.config" GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
mkdir -p "$HOME"
ssh-keygen -q -t ed25519 -N '' -C test -f "$TMP/key" || exit 2

failed=0
fail() { printf '\033[31mFAIL\033[0m %s\n' "$1" >&2; failed=$((failed + 1)); }
run_check() { # <ref line fields: ref local remote-sha> [url]
    printf '%s %s %s %s\n' "$1" "$2" "$1" "$3" | "$CHECK" push origin "${4:-$TMP/none.git}" > "$TMP/log" 2>&1
}
expect() { # description pass|refuse rc
    { [ "$2" = pass ] && [ "$3" -eq 0 ]; } || { [ "$2" = refuse ] && [ "$3" -ne 0 ]; } ||
        fail "$1 (expected $2, exit $3)"
}
said() { grep -qF -- "$2" "$TMP/log" || fail "$1 (no '$2' in the output: $(head -c 300 "$TMP/log"))"; }
silent() { [ ! -s "$TMP/log" ] || fail "$1 (output: $(head -c 300 "$TMP/log"))"; }

W="$TMP/work"
git init -q -b main "$W"
git -C "$W" config user.name test
git -C "$W" config user.email test@users.noreply.github.com
git -C "$W" config gpg.format ssh
git -C "$W" config user.signingkey "$TMP/key"
commit() { # <signed|unsigned> <message...>
    local mode=$1
    shift
    echo "$*" >> "$W/f"
    git -C "$W" add f
    if [ "$mode" = signed ]; then git -C "$W" commit -q -S -m "$*"; else git -C "$W" commit -q --no-gpg-sign -m "$*"; fi
}
sha() { git -C "$W" rev-parse "$1"; }
cd "$W" || exit 2

commit unsigned base
UNSIGNED=$(sha HEAD)
run_check refs/heads/main "$UNSIGNED" "$ZERO"
expect "an unsigned commit pushed to main is refused" refuse $?
said "the refusal names the commit" "${UNSIGNED:0:12}"
said "the refusal names the ref" refs/heads/main
said "the refusal says what to do instead" "pull request"

run_check refs/heads/feature "$UNSIGNED" "$ZERO"
expect "the same unsigned commit pushed to a branch passes" pass $?
silent "a branch push says nothing"

commit signed "signed tip"
SIGNED=$(sha HEAD)
run_check refs/heads/main "$SIGNED" "$UNSIGNED"
expect "a signed commit on top of history the destination holds passes" pass $?

run_check refs/heads/main "$SIGNED" "$ZERO"
expect "a signed tip over an unsigned parent, pushed to a destination holding neither, is refused" refuse $?
said "only the unsigned parent is named" "${UNSIGNED:0:12}"
grep -qF "${SIGNED:0:12}" "$TMP/log" && fail "the signed tip is named as unsigned"

run_check refs/heads/main "$ZERO" "$SIGNED"
expect "deleting main passes" pass $?

run_check refs/heads/main deadbeefdeadbeefdeadbeefdeadbeefdeadbeef "$ZERO"
expect "a local sha that does not resolve is refused, not waved through" refuse $?

# The header block ends at the first blank line. A message that merely QUOTES a
# gpgsig header is an unsigned commit.
commit unsigned "$(printf 'looks signed\n\ngpgsig -----BEGIN SSH SIGNATURE-----\ngpgsig-sha256 x')"
FAKE=$(sha HEAD)
run_check refs/heads/main "$FAKE" "$SIGNED"
expect "a message quoting a gpgsig header does not count as a signature" refuse $?
said "that commit is named" "${FAKE:0:12}"

# Several unsigned commits are all named, not only the first.
commit unsigned second
commit unsigned third
TIP=$(sha HEAD)
run_check refs/heads/main "$TIP" "$SIGNED"
expect "three unsigned commits are refused" refuse $?
said "the count is stated" "3 commit(s)"

# One batch read for the whole range (report 2.5: 20 ms against 3.4 s for %G?).
GIT_TRACE="$TMP/trace" run_check refs/heads/main "$TIP" "$SIGNED"
n=$(grep -c 'git cat-file --batch$' "$TMP/trace" 2>/dev/null)
[ "${n:-0}" -eq 1 ] || fail "expected exactly one cat-file --batch, saw ${n:-0}"
grep -qE 'git (log|verify-commit|show)\b.*(%G|--show-signature)|verify-commit' "$TMP/trace" && fail "the check verifies signatures instead of reading headers"

if [ "$failed" -ne 0 ]; then
    printf '\033[31m%d signature-check case(s) failed\033[0m\n' "$failed" >&2
    exit 1
fi
echo "ok test-signature-check"
