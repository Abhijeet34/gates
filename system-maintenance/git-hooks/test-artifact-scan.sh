#!/usr/bin/env bash
# Prove the release-artifact scan over fixture binaries, rather than asserting it.
#
#   system-maintenance/git-hooks/test-artifact-scan.sh
#
# Offline. Every machine value a case plants is read from THIS machine at run
# time, the way the scanner derives it, and never written into this file: the
# repository is public and the exposure gate refuses such a value in tracked
# text. A fixture is random bytes with the value planted between NULs, which is
# what a path inside a compiled binary's string table looks like.
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P) || exit 2
SCANNER="${ARTIFACT_SCAN:-$ROOT/system-maintenance/git-hooks/artifact-scan.py}"
PYTHON=$(command -v python3) || { echo "test-artifact-scan: python3 not installed" >&2; exit 2; }
[ -x "$SCANNER" ] || { echo "test-artifact-scan: $SCANNER is not executable" >&2; exit 2; }

TMP=$(mktemp -d) || exit 2
trap 'rm -rf "$TMP"' EXIT

HOME_PATH=$("$PYTHON" -c 'import os, pwd; print(pwd.getpwuid(os.getuid()).pw_dir)')
HOST=$(uname -n | cut -d. -f1)
case "$(uname -s)" in
    Darwin)
        MAC=$(PATH="$PATH:/sbin" ifconfig | awk '$1 == "ether" && $2 !~ /^(00:)+00$/ { print $2; exit }')
        ;;
    *)
        MAC=$(cat /sys/class/net/*/address 2>/dev/null | grep -v '^00:00:00:00:00:00$' | head -1)
        ;;
esac
for v in HOME_PATH HOST MAC; do
    [ -n "${!v}" ] || { echo "test-artifact-scan: could not read $v from this machine" >&2; exit 2; }
done

failed=0 ran=0
fail() { printf '\033[31mFAIL\033[0m %s\n' "$1" >&2; failed=$((failed + 1)); }
expect_rc() { # description want-rc got-rc
    ran=$((ran + 1))
    [ "$2" -eq "$3" ] || fail "$1 (expected exit $2, got $3)"
}
said() { ran=$((ran + 1)); grep -qE -- "$2" "$TMP/log" || fail "$1 (no '$2' in the output)"; }
never_said() { # description value...
    local d=$1 v
    shift
    for v in "$@"; do
        ran=$((ran + 1))
        ! grep -qiF -- "$v" "$TMP/log" || fail "$d (the output repeats a planted value)"
    done
}

# LC_ALL=C: macOS tr rejects random bytes as invalid UTF-8 otherwise.
noise() { head -c "${1:-4096}" /dev/urandom | LC_ALL=C tr -d '\0'; }
# binary file text...: noise, then each value between NULs, then noise.
binary() {
    local out=$1
    shift
    { noise; local v; for v in "$@"; do printf '\0%s\0' "$v"; noise 512; done; } > "$out"
}
scan() { "$PYTHON" -I "$SCANNER" "$@" > "$TMP/log" 2>&1; }

# 1. A binary with nothing planted passes.
binary "$TMP/clean.bin"
scan "$TMP/clean.bin"
expect_rc "a clean binary passes" 0 $?
said "a clean pass says what it scanned" "1 file\(s\) scanned, no machine identifier"

# 2. A home path, planted three times, is refused with its class and count.
binary "$TMP/home.bin" "$HOME_PATH/.cargo/registry/src/x" "$HOME_PATH/.rustup/y" "$HOME_PATH"
scan "$TMP/home.bin"
expect_rc "a binary carrying the home path is refused" 1 $?
said "the home-path class and its count of 3" "machine\.home-path +3 "
never_said "the home-path refusal" "$HOME_PATH"

# 3. The hostname, in either case.
HOST_UPPER=$(printf '%s' "$HOST" | LC_ALL=C tr 'a-z' 'A-Z')
binary "$TMP/host.bin" "$HOST" "build-host=$HOST_UPPER"
scan "$TMP/host.bin"
expect_rc "a binary carrying the hostname is refused" 1 $?
said "the hostname class and its count of 2" "machine\.hostname +2 "
never_said "the hostname refusal" "$HOST"

# 4. A hardware address in an uppercase, hyphenated spelling.
MAC_SPELLED=$(printf '%s' "$MAC" | LC_ALL=C tr 'a-f:' 'A-F-')
binary "$TMP/mac.bin" "$MAC_SPELLED"
scan "$TMP/mac.bin"
expect_rc "a binary carrying a hardware address is refused" 1 $?
said "the mac-address class" "machine\.mac-address +1 "
never_said "the mac refusal" "$MAC" "$MAC_SPELLED"

# 5. A high byte straight after the path is a boundary, as a NUL is, because a
# byte scan has no Unicode word characters.
{ noise; printf '\0%s' "$HOME_PATH"; printf '\377\0'; noise 64; } > "$TMP/high.bin"
scan "$TMP/high.bin"
expect_rc "a path followed by a high byte is still refused" 1 $?
{ noise; printf '\0%sx\0' "$HOME_PATH"; noise 64; } > "$TMP/longer.bin"
scan "$TMP/longer.bin"
expect_rc "a longer name that merely begins with the path is not a hit" 0 $?

# 6. A directory is walked, a symlink's target text is read, and every file is
# counted, not just the first hit.
mkdir -p "$TMP/app/Contents/MacOS" "$TMP/app/Contents/Resources"
binary "$TMP/app/Contents/MacOS/main" "$HOME_PATH/a"
binary "$TMP/app/Contents/Resources/side" "$HOME_PATH/b" "$HOME_PATH/c"
binary "$TMP/app/Contents/Resources/ok"
ln -s "$HOME_PATH/elsewhere" "$TMP/app/Contents/link"
scan "$TMP/app"
expect_rc "a bundle with planted files is refused" 1 $?
said "the nested main binary is reported" "machine\.home-path +1 .*Contents/MacOS/main"
said "the nested sidecar is reported" "machine\.home-path +2 .*Contents/Resources/side"
said "the symlink target is reported" "machine\.home-path +1 .*Contents/link"
never_said "the bundle refusal" "$HOME_PATH"

# 7. A path that itself holds a needle is redacted in the report.
# A directory of that name: the hostname needle needs a boundary on both sides.
mkdir "$TMP/$HOST"
binary "$TMP/$HOST/named.bin" "$HOME_PATH"
scan "$TMP/$HOST/named.bin"
expect_rc "a planted binary under a directory named for the host is refused" 1 $?
never_said "the report" "$HOST"

# 8. An artifact that was not read never passes.
scan "$TMP/does-not-exist"
expect_rc "a missing path is a usage error, not a pass" 2 $?
scan
expect_rc "no argument is a usage error, not a pass" 2 $?
if [ "$(id -u)" -ne 0 ]; then
    binary "$TMP/locked.bin"
    chmod 000 "$TMP/locked.bin"
    scan "$TMP/locked.bin"
    expect_rc "an unreadable file is a usage error, not a pass" 2 $?
    chmod 600 "$TMP/locked.bin"
fi

# 9. One clean artifact beside a bad one does not dilute the verdict.
scan "$TMP/clean.bin" "$TMP/home.bin"
expect_rc "a clean artifact beside a bad one still refuses" 1 $?

if [ "$failed" -ne 0 ]; then
    printf '\033[31m%d of %d assertion(s) failed\033[0m\n' "$failed" "$ran" >&2
    exit 1
fi
printf 'ok %d assertion(s)\n' "$ran"
