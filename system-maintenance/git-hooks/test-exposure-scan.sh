#!/usr/bin/env bash
# Prove the exposure gate through real pushes, rather than asserting it.
#
#   system-maintenance/git-hooks/test-exposure-scan.sh
#
# Offline. Every machine value a case plants is read from THIS machine at run
# time, the way the scanner derives it, and never written into this file: the
# repository is public and is pushed through the gate under test. The same
# holds for the vulnerability count, which is assembled from a variable.
#
# A public destination is modelled by an origin on example.invalid, which no
# lookup can prove private, so a finding refuses exactly as it does toward a
# public repository; nothing is ever sent there. A private one is a repository
# with no network origin at all. The dispatch around the scanner is pinned in
# test-global-hooks.sh.
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P) || exit 2
SCANNER="$ROOT/system-maintenance/git-hooks/exposure-scan.py"
PYTHON=$(command -v python3) || { echo "test-exposure-scan: python3 not installed" >&2; exit 2; }
[ -x "$SCANNER" ] || { echo "test-exposure-scan: $SCANNER is not executable" >&2; exit 2; }
ZERO=0000000000000000000000000000000000000000

TMP=$(mktemp -d) || exit 2
trap 'rm -rf "$TMP"' EXIT

# --- the values to plant, derived as the scanner derives them ------------------
HOME_PATH=$("$PYTHON" -c 'import os, pwd; print(pwd.getpwuid(os.getuid()).pw_dir)')
HOST=$(uname -n | cut -d. -f1)
case "$(uname -s)" in
    Darwin)
        MAC=$(PATH="$PATH:/sbin" ifconfig | awk '$1 == "ether" && $2 !~ /^(00:)+00$/ { print $2; exit }')
        HWID=$(PATH="$PATH:/usr/sbin" ioreg -rd1 -c IOPlatformExpertDevice | sed -n 's/.*"IOPlatformSerialNumber" = "\(.*\)"/\1/p')
        ;;
    *)
        MAC=$(cat /sys/class/net/*/address 2>/dev/null | grep -v '^00:00:00:00:00:00$' | head -1)
        HWID=$(cat /etc/machine-id 2>/dev/null)
        ;;
esac
for v in HOME_PATH HOST MAC HWID; do
    [ -n "${!v}" ] || { echo "test-exposure-scan: could not read $v from this machine" >&2; exit 2; }
done
# Uppercase and hyphenated, so the case proves the address is matched in any
# spelling rather than only the one ifconfig prints.
MAC_SPELLED=$(printf '%s' "$MAC" | tr 'a-f:' 'A-F-')

# --- containment ---------------------------------------------------------------
export HOME="$TMP/home" XDG_CONFIG_HOME="$TMP/home/.config" GH_CONFIG_DIR="$TMP/gh"
export GIT_CONFIG_GLOBAL="$TMP/gitconfig" GIT_CONFIG_SYSTEM=/dev/null
unset GH_TOKEN GITHUB_TOKEN
mkdir -p "$HOME" "$TMP/hooks"
git config --global user.name test
git config --global user.email test@users.noreply.github.com
git config --global init.defaultBranch main
git config --global core.hooksPath "$TMP/hooks"
# The deployed dispatcher's own call, minus the other gates.
cat > "$TMP/hooks/pre-push" <<HOOK
#!/bin/sh
PATH="\$PATH:/usr/sbin:/sbin" exec "$SCANNER" push "\$@"
HOOK
chmod +x "$TMP/hooks/pre-push"

failed=0 ran=0
fail() { printf '\033[31mFAIL\033[0m %s\n' "$1" >&2; failed=$((failed + 1)); }
expect() { # description pass|refuse rc
    ran=$((ran + 1))
    { [ "$2" = pass ] && [ "$3" -eq 0 ]; } || { [ "$2" = refuse ] && [ "$3" -ne 0 ]; } ||
        fail "$1 (expected $2, exit $3)"
}
said() { grep -q -- "$2" "$TMP/log" || fail "$1 (no '$2' in the output)"; }
never_said() { grep -qiF -- "$2" "$TMP/log" && fail "$1 (the output repeats the planted value)"; }

repo() { # public|private -> prints only the path of a fresh repository whose destination holds one clean commit
    local d
    d=$(mktemp -d "$TMP/r.XXXXXX") || exit 2
    {
        git init --quiet --bare "$d.git"
        git init --quiet "$d"
        [ "$1" = public ] && git -C "$d" remote add origin "https://example.invalid/fixture/$(basename "$d").git"
        git -C "$d" remote add dest "$d.git"
        echo seed > "$d/seed.txt"
        git -C "$d" add -A && git -C "$d" commit --quiet -m seed
        git -C "$d" push --quiet dest main || echo "FAIL seed push of $d" >&2
    } >/dev/null 2>&1
    echo "$d"
}
push() { git -C "$1" push dest "${2:-main}" >"$TMP/log" 2>&1; }
commit_file() { # repo file text [git -c args...]
    local d=$1 f=$2 text=$3
    shift 3
    printf '%s\n' "$text" > "$d/$f"
    git -C "$d" add -A && git -C "$d" "$@" commit --quiet -m "add $f"
}

# ==============================================================================
# 1. A clean push lands, and says what it examined.
# ==============================================================================
d=$(repo public)
commit_file "$d" ok.txt "nothing to see"
push "$d"
expect "clean push to a public destination lands" pass $?
said "clean push reports its verdict" "No identity or machine exposure found"

# ==============================================================================
# 2. Identity: every field and trailer, each refused on its own.
# ==============================================================================
d=$(repo public)
commit_file "$d" a.txt a -c user.email=someone@example.com
push "$d"
expect "a non-noreply AUTHOR and committer are refused" refuse $?
said "author finding named" "identity.author"
said "committer finding named" "identity.committer"
said "the address is masked" "s\*\*\*@example.com"
never_said "the refusal does not repeat the address" "someone@example.com"
git -C "$d" commit --quiet --amend --reset-author --no-edit
push "$d"
expect "the same change lands once re-authored with the noreply address" pass $?

d=$(repo public)
echo c > "$d/c.txt" && git -C "$d" add -A
GIT_COMMITTER_EMAIL=ci@example.com git -C "$d" commit --quiet -m "committer only"
push "$d"
expect "a non-noreply COMMITTER alone is refused" refuse $?
said "committer-only finding named" "identity.committer"

for trailer in Co-authored-by Signed-off-by; do
    d=$(repo public)
    echo t > "$d/t.txt" && git -C "$d" add -A
    git -C "$d" commit --quiet -m "trailer" -m "$trailer: Someone <someone@example.com>"
    push "$d"
    expect "a non-noreply $trailer trailer is refused" refuse $?
    said "$trailer finding named" "identity.$(printf '%s' "$trailer" | tr 'A-Z' 'a-z')"
done

d=$(repo public)
git -C "$d" -c user.email=someone@example.com tag -a v1 -m "release"
git -C "$d" push dest v1 >"$TMP/log" 2>&1
expect "an annotated tag with a non-noreply TAGGER is refused" refuse $?
said "tagger finding named" "identity.tagger"

# The legitimate identities that already push to this fleet.
d=$(repo public)
for who in "49699333+dependabot[bot]@users.noreply.github.com" "41898282+github-actions[bot]@users.noreply.github.com" noreply@github.com; do
    echo "$who" >> "$d/bots.txt" && git -C "$d" add -A
    GIT_COMMITTER_EMAIL=$who git -C "$d" -c user.email="$who" commit --quiet -m "by $who" \
        -m "Co-authored-by: Someone <12345+someone@users.noreply.github.com>" \
        -m "Signed-off-by: dependabot[bot] <support@github.com>"
done
push "$d"
expect "bot and web-flow noreply identities land" pass $?

# ==============================================================================
# 3. Content: each machine identifier, in a file, a message, and an address.
# ==============================================================================
content_case() { # label rule value
    local d
    d=$(repo public)
    commit_file "$d" notes.md "see $3 for details"
    push "$d"
    expect "$1 in an added line is refused" refuse $?
    said "$1 finding named" "$2"
    said "$1 finding located" "notes.md:1"
    never_said "$1 is not repeated by the refusal" "$3"
}
content_case "the home path" machine.home-path "$HOME_PATH/Developer/x"
content_case "the hostname" machine.hostname "$HOST"
content_case "a MAC address, re-spelled" machine.mac-address "$MAC_SPELLED"
content_case "the hardware serial or machine id" machine.hardware-id "$HWID"

d=$(repo public)
echo m > "$d/m.txt" && git -C "$d" add -A
git -C "$d" commit --quiet -m "diagnostics" -m "ran on $HOST"
push "$d"
expect "the hostname in a commit MESSAGE is refused" refuse $?
said "message finding named" "machine.hostname"

d=$(repo public)
echo h > "$d/h.txt" && git -C "$d" add -A
git -C "$d" -c user.email="me@$HOST.local" commit --quiet -m "an address built from the hostname"
push "$d"
expect "a <login>@<hostname> address is refused" refuse $?
said "hostname address: identity finding" "identity.author"
said "hostname address: machine finding" "machine.hostname"
never_said "hostname address: the masked address does not keep the hostname" "$HOST"

# ==============================================================================
# 4. The vulnerability count: refused toward public, silent toward private.
# ==============================================================================
count=7
d=$(repo public)
commit_file "$d" status.md "there are $count open Dependabot alerts"
push "$d"
expect "a vulnerability count toward a public destination is refused" refuse $?
said "count finding named" "content.vulnerability-count"
d=$(repo public)
commit_file "$d" runbook.md "page when $count alerts fire in an hour"
push "$d"
expect "a plain alert count with no security qualifier lands" pass $?

# ==============================================================================
# 5. Private: warned, never refused.
# ==============================================================================
d=$(repo private)
commit_file "$d" notes.md "see $HOME_PATH and $count open Dependabot alerts" -c user.email=someone@example.com
push "$d"
expect "exposure toward a private destination lands" pass $?
said "private push warns" "WARNING - exposure in a push to a PRIVATE destination"
said "private push still names the identity" "identity.author"
said "private push still names the machine value" "machine.home-path"
grep -q "content.vulnerability-count" "$TMP/log" && fail "a vulnerability count is reported toward a private destination"

# ==============================================================================
# 6. What the destination already has is not re-read: a leak already published
#    must not make every later push unpushable.
# ==============================================================================
d=$(repo public)
commit_file "$d" old.md "see $HOST" -c user.email=someone@example.com
git -C "$d" push --quiet --no-verify dest main >/dev/null 2>&1
git -C "$d" checkout --quiet -b feature
commit_file "$d" new.md "clean"
push "$d" feature
expect "a new branch over an already-published leak lands" pass $?

# ==============================================================================
# 7. No identifier source: a clear refusal, never a silent pass - but only when
#    there is something to scan. A delete-only push has nothing to examine and
#    must land even on a machine that cannot derive its own identifiers.
# ==============================================================================
if [ "$(uname -s)" = Darwin ]; then
    DELETED_SHA=$(printf 'a%.0s' $(seq 1 40))
    printf 'refs/heads/gone %s refs/heads/gone %s\n' "$ZERO" "$DELETED_SHA" |
        env PATH=/usr/bin:/bin "$PYTHON" "$SCANNER" push dest /nonexistent >"$TMP/log" 2>&1
    expect "a delete-only push needs no identifiers and lands" pass $?
    said "and says there is nothing to examine" "nothing to examine"

    PARENT_SHA=$(git -C "$ROOT" rev-parse HEAD~1)
    HEAD_SHA=$(git -C "$ROOT" rev-parse HEAD)
    printf 'refs/heads/main %s refs/heads/main %s\n' "$HEAD_SHA" "$PARENT_SHA" |
        (cd "$ROOT" && env PATH=/usr/bin:/bin "$PYTHON" "$SCANNER" push dest /nonexistent) >"$TMP/log" 2>&1
    expect "a push that introduces commits refuses without scutil/ioreg on PATH" refuse $?
    said "the missing tool is named" "is not on PATH, so this machine's identifiers cannot be derived"
    said "and it is a refusal" "Push refused"
else
    echo "note: section 7 is macOS-only; this platform derives its identifiers from /sys and /etc/machine-id"
fi

# ==============================================================================
# 8. This repository's own history, fixtures included, is clean: a gate that
#    fired on the values it tests with would make this repository unpushable.
# ==============================================================================
printf 'refs/heads/x %s refs/heads/x %s\n' "$(git -C "$ROOT" rev-parse HEAD)" "$ZERO" |
    (cd "$ROOT" && "$SCANNER" push nowhere "$TMP/empty-destination.git") >"$TMP/log" 2>&1
expect "the gates repository's whole history, as a first push, is clean" pass $?
said "gates history verdict" "No identity or machine exposure found"

if [ "$failed" -ne 0 ]; then
    printf '\033[31m%d of %d exposure-gate checks failed\033[0m\n' "$failed" "$ran" >&2
    exit 1
fi
printf '\033[32mall %d exposure-gate checks passed\033[0m\n' "$ran"
