#!/usr/bin/env bash
# Prove the machine-wide pre-push hook, rather than asserting it.
#
#   system-maintenance/git-hooks/test-global-hooks.sh
#   GLOBAL_HOOKS_E2E=1 system-maintenance/git-hooks/test-global-hooks.sh
#
# Everything runs against a throwaway HOME with its own global git config, so the
# real ~/.git-hooks and the real ~/.gitconfig are never read or written. That
# scoping is proved before anything destructive, the same way test-trash.py
# proves its own.
#
# The two properties that matter most are the two the hook could get wrong in
# opposite directions:
#   - a FRESH CLONE of an allowlisted repository refuses a secret with no
#     per-repo setup, because core.hooksPath is repository config and a clone
#     starts without it;
#   - a repository NOT on the allowlist is left completely alone, and in
#     particular its own .githooks/pre-push is never executed. That is the
#     clone-me-and-run-my-code hazard git avoids by not populating .git/hooks,
#     and a global hook that chained into the pushed repository would hand it
#     straight back.
#
# GLOBAL_HOOKS_E2E=1 adds one case that clones a real private fleet repository
# over the network. It is opt-in and deliberately NOT auto-skipped on failure:
# an auto-skip would report a pass on a machine where the network or the
# credentials are gone, which is the state where the answer matters.
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P) || exit 2
INSTALL="$ROOT/system-maintenance/git-hooks/install.sh"
CANONICAL="$ROOT/.githooks/pre-push"
FIXTURE="$ROOT/.ci/gitleaks/fixtures/known-secrets.tsv"

for f in "$INSTALL" "$CANONICAL" "$FIXTURE" "$ROOT/.gitleaks.toml"; do
    [ -r "$f" ] || { echo "test-global-hooks: cannot read $f" >&2; exit 2; }
done
command -v gitleaks >/dev/null 2>&1 || { echo "test-global-hooks: gitleaks not installed" >&2; exit 2; }
command -v git-lfs >/dev/null 2>&1 || { echo "test-global-hooks: git-lfs not installed" >&2; exit 2; }
# The no-watermarking gate's own dependencies. Refuse rather than skip: a run
# that quietly dropped section 10 would report a pass over an unmeasured gate.
command -v exiftool >/dev/null 2>&1 || { echo "test-global-hooks: exiftool not installed" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "test-global-hooks: python3 not installed" >&2; exit 2; }

SECRET=$(awk -F'\t' '$2 == "moonshot-kimi" { print $3 }' "$FIXTURE")
[ -n "$SECRET" ] || { echo "test-global-hooks: fixture has no moonshot-kimi row" >&2; exit 2; }

# Read while HOME is still real: gh keeps its token in the login keyring and
# cannot reach it from the throwaway HOME below. Held in a variable for the one
# clone that needs it and never written anywhere.
E2E_TOKEN=""
[ "${GLOBAL_HOOKS_E2E:-0}" = 1 ] && E2E_TOKEN=$(gh auth token 2>/dev/null)

TMP=$(mktemp -d) || exit 2
trap 'rm -rf "$TMP"' EXIT

# Captured before HOME is redirected: the containment proof reads the real hooks
# directory, and the opt-in networked case borrows the machine's gh credentials.
REAL_HOME="$HOME"

# --- containment ---------------------------------------------------------------
# Every git invocation below inherits these. GIT_CONFIG_SYSTEM silences
# /etc/gitconfig; HOME and XDG_CONFIG_HOME between them are where git looks for
# "global" config, and install.sh takes its deploy target from HOME too.
export HOME="$TMP/home"
export XDG_CONFIG_HOME="$TMP/home/.config"
export GIT_CONFIG_SYSTEM=/dev/null
mkdir -p "$HOME"

failed=0
fail() { printf '\033[31mFAIL\033[0m %s\n' "$1" >&2; failed=$((failed + 1)); }
check() { # description expected-outcome actual-rc
    [ "$2" = pass ] && [ "$3" -eq 0 ] && return 0
    [ "$2" = refuse ] && [ "$3" -ne 0 ] && return 0
    fail "$1 (expected $2, git push exited $3)"
}
# A nonzero exit says a push did not land, not why. Where the reason is the whole
# claim - "refused BECAUSE it carries a secret" - assert the gate's own words, so
# an auth failure or a typo in a fixture path cannot pass for a working gate.
said() { # description file pattern
    grep -q "$3" "$2" || fail "$1 (no '"'"'$3'"'"' in the push output)"
}

# Containment proof: the deploy must land inside the throwaway tree and the real
# hooks directory must be untouched by it.
REAL_HOOKS="$REAL_HOME/.git-hooks"
before=$(shasum -a 256 "$REAL_HOOKS"/* 2>/dev/null | shasum -a 256)
bash "$INSTALL" deploy >/dev/null 2>&1 || { echo "test-global-hooks: deploy into the throwaway HOME failed" >&2; exit 2; }
[ -x "$HOME/.git-hooks/pre-push" ] || { echo "test-global-hooks: deploy did not land in $HOME" >&2; exit 2; }
after=$(shasum -a 256 "$REAL_HOOKS"/* 2>/dev/null | shasum -a 256)
[ "$before" = "$after" ] || { echo "test-global-hooks: the real $REAL_HOOKS changed - refusing to continue" >&2; exit 2; }

git config --global user.name test
git config --global user.email test@example.invalid
git config --global init.defaultBranch main
bash "$INSTALL" check >/dev/null 2>&1 || fail "install.sh check reports drift immediately after deploy"

# Intercepts `git lfs` without touching PATH: git searches GIT_EXEC_PATH before
# PATH for git-lfs, and the hook's Homebrew prepend would otherwise always win.
mkdir -p "$TMP/exec"
cat > "$TMP/exec/git-lfs" <<STUB
#!/bin/sh
cat > "$TMP/lfs-stdin"
exit 0
STUB
chmod +x "$TMP/exec/git-lfs"

seed() { # <dir> - a repository with a bare origin and one clean commit
    git init --quiet --bare "$1.git"
    git init --quiet -b main "$1"
    git -C "$1" remote add origin "$1.git"
    echo "print('hello')" > "$1/app.py"
    git -C "$1" add -A
    git -C "$1" commit --quiet -m clean
    git -C "$1" push --quiet origin main
}

allow() { printf '%s\n' "$1" >> "$HOME/.git-hooks/pre-push-allow"; }

# ==============================================================================
# 1. A FRESH CLONE of an allowlisted repository, with no per-repo setup at all.
# ==============================================================================
seed "$TMP/up"
allow "${TMP}/up"          # the clone's origin, normalised (trailing .git stripped)

git clone --quiet "$TMP/up.git" "$TMP/fresh"
[ -e "$TMP/fresh/.githooks" ] && fail "the fixture clone carries a .githooks directory; it must not"
[ -e "$TMP/fresh/.gitleaks.toml" ] && fail "the fixture clone carries a .gitleaks.toml; it must not"
[ -z "$(git -C "$TMP/fresh" config --local --get core.hooksPath)" ] || fail "the fixture clone has a local core.hooksPath; it must not"

echo "print('more')" > "$TMP/fresh/more.py"
git -C "$TMP/fresh" add -A && git -C "$TMP/fresh" commit --quiet -m clean2
git -C "$TMP/fresh" push --quiet origin main >/dev/null 2>&1
check "fresh clone: a clean push lands" pass $?

printf 'MODELS = {"kimi": "%s"}\n' "$SECRET" > "$TMP/fresh/leak.py"
git -C "$TMP/fresh" add -A && git -C "$TMP/fresh" commit --quiet -m leak
LEAK_SHA=$(git -C "$TMP/fresh" rev-parse HEAD)
git -C "$TMP/fresh" push origin main >"$TMP/push.log" 2>&1
check "fresh clone: a push carrying a secret is refused, with no per-repo setup" refuse $?
said "fresh clone: refused for the secret" "$TMP/push.log" "secrets found in the commits being pushed"
if git --git-dir="$TMP/up.git" cat-file -e "$LEAK_SHA^{commit}" 2>/dev/null; then
    fail "fresh clone: the secret-carrying commit reached the remote"
fi

# ==============================================================================
# 2. A repository NOT on the allowlist is left completely alone.
#    Its own .githooks/pre-push must never execute, and it must not be refused.
# ==============================================================================
seed "$TMP/hostile"
mkdir -p "$TMP/hostile/.githooks"
cat > "$TMP/hostile/.githooks/pre-push" <<STUB
#!/bin/sh
: > "$TMP/PWNED"
exit 0
STUB
chmod +x "$TMP/hostile/.githooks/pre-push"
cp "$ROOT/.gitleaks.toml" "$TMP/hostile/.gitleaks.toml"
printf 'MODELS = {"kimi": "%s"}\n' "$SECRET" > "$TMP/hostile/leak.py"
git -C "$TMP/hostile" add -A && git -C "$TMP/hostile" commit --quiet -m "hostile hook and a secret"
# Still not refused: the watermark gate runs here now (it runs everywhere) and
# finds nothing, and the SECRET scan is what the allowlist gates.
git -C "$TMP/hostile" push --quiet origin main >/dev/null 2>&1
check "unlisted repo: its push is not refused, secret and all" pass $?
[ -e "$TMP/PWNED" ] && fail "unlisted repo: its .githooks/pre-push EXECUTED - remote code execution by clone"

# The same repository once it is allowlisted: now it is scanned, and it is still
# the deployed scanner that runs, never the copy that came with the repository.
# A fresh secret, because the first one is already on the remote and so is not
# in the range this push covers - scanning the pushed range, not full history,
# is what keeps every push off a full-history scan.
allow "${TMP}/hostile"
/bin/rm -f "$TMP/PWNED"
printf 'TOKEN = "%s"\n' "$SECRET" > "$TMP/hostile/leak2.py"
git -C "$TMP/hostile" add -A && git -C "$TMP/hostile" commit --quiet -m "a second secret"
git -C "$TMP/hostile" push origin main >/dev/null 2>&1
check "allowlisted repo: the push is refused for the secret in range" refuse $?
[ -e "$TMP/PWNED" ] && fail "allowlisted repo: its .githooks/pre-push EXECUTED - the allowlist must gate scanning, not trust repo code"

# ==============================================================================
# 3. A repository with no origin at all is left alone (the local-notes case).
# ==============================================================================
git init --quiet -b main "$TMP/noremote"
git init --quiet --bare "$TMP/noremote-target.git"
printf 'MODELS = {"kimi": "%s"}\n' "$SECRET" > "$TMP/noremote/leak.py"
git -C "$TMP/noremote" add -A && git -C "$TMP/noremote" commit --quiet -m leak
git -C "$TMP/noremote" push --quiet "$TMP/noremote-target.git" main >/dev/null 2>&1
check "repo with no origin: left alone" pass $?

# ==============================================================================
# 4. Fail closed: an allowlisted repository whose scanner or rules are missing
#    is a repository nobody scanned, and must not look like a clean push.
# ==============================================================================
echo "print('clean')" > "$TMP/fresh/clean3.py"
git -C "$TMP/fresh" rm --quiet leak.py
git -C "$TMP/fresh" add -A && git -C "$TMP/fresh" commit --quiet -m "drop the leak"
git -C "$TMP/fresh" push --quiet origin main >/dev/null 2>&1
check "fresh clone: still refused while the secret is in the range" refuse $?
# Squash the leak out of the range so later cases start from a clean push.
git -C "$TMP/fresh" reset --quiet --hard origin/main
echo "print('clean')" > "$TMP/fresh/clean3.py"
git -C "$TMP/fresh" add -A && git -C "$TMP/fresh" commit --quiet -m clean3
git -C "$TMP/fresh" push --quiet origin main >/dev/null 2>&1
check "fresh clone: a clean push lands again once the leak is out of the range" pass $?

echo "print('clean4')" > "$TMP/fresh/clean4.py"
git -C "$TMP/fresh" add -A && git -C "$TMP/fresh" commit --quiet -m clean4
mv "$HOME/.git-hooks/gitleaks-pre-push" "$TMP/scanner.away"
git -C "$TMP/fresh" push origin main >/dev/null 2>&1
check "allowlisted repo with no deployed scanner refuses" refuse $?
mv "$TMP/scanner.away" "$HOME/.git-hooks/gitleaks-pre-push"

mv "$HOME/.git-hooks/gitleaks.toml" "$TMP/rules.away"
git -C "$TMP/fresh" push origin main >/dev/null 2>&1
check "allowlisted repo with no deployed rules refuses" refuse $?
mv "$TMP/rules.away" "$HOME/.git-hooks/gitleaks.toml"

git -C "$TMP/fresh" push --quiet origin main >/dev/null 2>&1
check "the same push lands once the scanner and rules are back" pass $?

# ==============================================================================
# 5. check-orphan-dirs still fires, on both the scanned and the unscanned path.
# ==============================================================================
mkdir -p "$TMP/fresh/orphan"
echo "work nobody tracks" > "$TMP/fresh/orphan/notes.txt"
echo "print('c5')" > "$TMP/fresh/clean5.py"
git -C "$TMP/fresh" add clean5.py && git -C "$TMP/fresh" commit --quiet -m clean5
git -C "$TMP/fresh" push origin main >/dev/null 2>&1
check "scanned repo: an orphan directory still blocks the push" refuse $?
/bin/rm -rf "$TMP/fresh/orphan"
git -C "$TMP/fresh" push --quiet origin main >/dev/null 2>&1
check "scanned repo: the push lands once the orphan directory is gone" pass $?

seed "$TMP/plain"
mkdir -p "$TMP/plain/orphan"
echo "work nobody tracks" > "$TMP/plain/orphan/notes.txt"
echo "print('p')" > "$TMP/plain/p.py"
git -C "$TMP/plain" add p.py && git -C "$TMP/plain" commit --quiet -m p
git -C "$TMP/plain" push origin main >/dev/null 2>&1
check "unscanned repo: an orphan directory still blocks the push" refuse $?

# ==============================================================================
# 6. git lfs pre-push still receives the ref list - on BOTH paths, because the
#    scanned one hands it a buffered copy and the unscanned one an untouched pipe.
#    A starved consumer is silent, so this asserts on what the stub actually read.
# ==============================================================================
/bin/rm -rf "$TMP/plain/orphan"
: > "$TMP/lfs-stdin"
echo "print('p2')" > "$TMP/plain/p2.py"
git -C "$TMP/plain" add -A && git -C "$TMP/plain" commit --quiet -m p2
GIT_EXEC_PATH="$TMP/exec" git -C "$TMP/plain" push --quiet origin main >/dev/null 2>&1
grep -q "refs/heads/main" "$TMP/lfs-stdin" || fail "unscanned path: git-lfs got no ref list on stdin"

: > "$TMP/lfs-stdin"
echo "print('c6')" > "$TMP/fresh/clean6.py"
git -C "$TMP/fresh" add -A && git -C "$TMP/fresh" commit --quiet -m clean6
GIT_EXEC_PATH="$TMP/exec" git -C "$TMP/fresh" push --quiet origin main >/dev/null 2>&1
grep -q "refs/heads/main" "$TMP/lfs-stdin" || fail "scanned path: git-lfs got no ref list on stdin"

# ==============================================================================
# 7. A repository whose own core.hooksPath already ran the canonical scanner is
#    not scanned twice - and the skip is earned by a byte comparison, not by
#    anything the push can assert.
# ==============================================================================
seed "$TMP/selfhook"
allow "${TMP}/selfhook"
mkdir -p "$TMP/selfhook/.githooks"
cp "$CANONICAL" "$TMP/selfhook/.githooks/pre-push"
chmod +x "$TMP/selfhook/.githooks/pre-push"
cp "$ROOT/.gitleaks.toml" "$TMP/selfhook/.gitleaks.toml"
git -C "$TMP/selfhook" add -A && git -C "$TMP/selfhook" commit --quiet -m "arm the repo hook"
git -C "$TMP/selfhook" config core.hooksPath .githooks
git -C "$TMP/selfhook" push --quiet origin main >/dev/null 2>&1
check "self-hooked repo: a clean push lands" pass $?

# Removing the DEPLOYED rules is invisible if the global hook correctly skips its
# own scan, and fatal if it runs one - which is what makes this a real assertion
# about the skip rather than about the push.
echo "print('s2')" > "$TMP/selfhook/s2.py"
git -C "$TMP/selfhook" add -A && git -C "$TMP/selfhook" commit --quiet -m s2
mv "$HOME/.git-hooks/gitleaks.toml" "$TMP/rules.away"
git -C "$TMP/selfhook" push --quiet origin main >/dev/null 2>&1
check "self-hooked repo: the global hook does not scan a second time" pass $?

# The same push with a DRIFTED repo hook must NOT be skipped: cmp is the whole
# basis for trusting that the scan already happened.
echo "# drifted" >> "$TMP/selfhook/.githooks/pre-push"
echo "print('s3')" > "$TMP/selfhook/s3.py"
git -C "$TMP/selfhook" add -A && git -C "$TMP/selfhook" commit --quiet -m s3
git -C "$TMP/selfhook" push --quiet origin main >/dev/null 2>&1
check "self-hooked repo with a drifted hook: the global hook scans, and refuses without rules" refuse $?
mv "$TMP/rules.away" "$HOME/.git-hooks/gitleaks.toml"

# ==============================================================================
# 8. The origin-URL normalisation, which is the security boundary: everything
#    else in this file trusts its verdict. Each fixture pushes a secret to a
#    remote that is NOT its origin, so what is asserted is purely whether the
#    origin URL put it inside the allowlist.
# ==============================================================================
/bin/rm -f "$HOME/.git-hooks/pre-push-allow"   # base allowlist only, as deployed
n=0
identity_case() { # <expected: scan|alone> <origin url>
    n=$((n + 1))
    local d="$TMP/id$n"
    git init --quiet -b main "$d"
    git init --quiet --bare "$d-target.git"
    git -C "$d" remote add origin "$2"
    git -C "$d" remote add sandbox "$d-target.git"
    printf 'TOKEN = "%s"\n' "$SECRET" > "$d/leak.py"
    git -C "$d" add -A && git -C "$d" commit --quiet -m leak
    git -C "$d" push sandbox HEAD:refs/heads/main >/dev/null 2>&1
    local rc=$?   # captured before the `if`, which would overwrite $? with its own
    if [ "$1" = scan ]; then
        check "origin $2 is scanned" refuse "$rc"
    else
        check "origin $2 is left alone" pass "$rc"
    fi
}
identity_case scan  "https://github.com/Abhijeet34/automation.git"
identity_case scan  "git@github.com:Abhijeet34/automation.git"
identity_case scan  "ssh://git@github.com/Abhijeet34/automation.git"
identity_case scan  "https://github.com:443/Abhijeet34/automation.git"
# A userinfo strip scoped only to a bare `${url#*@}` on the whole authority
# (rather than stopping at the first authority-internal ":") would leave the
# colon-bearing credential in the identity and miss this legitimate repo.
identity_case scan  "https://user:pass@github.com/Abhijeet34/automation.git"
identity_case scan  "https://user@github.com:443/Abhijeet34/automation.git"
# A userinfo strip applied to the whole URL rather than the authority turns this
# one into github.com/Abhijeet34/y. It must stay evil.example/....
identity_case alone "https://evil.example/x@github.com/Abhijeet34/y.git"
identity_case alone "https://github.com.evil.example/Abhijeet34/y.git"
identity_case alone "https://evil.example/github.com/Abhijeet34/y.git"
identity_case alone "https://github.com/Abhijeet34x/y.git"
identity_case alone "https://gitlab.gnome.org/GNOME/meld.git"

# ==============================================================================
# 9. install.sh check reports drift instead of absorbing it.
# ==============================================================================
echo "# edited live" >> "$HOME/.git-hooks/check-orphan-dirs"
bash "$INSTALL" check >/dev/null 2>&1
[ $? -eq 0 ] && fail "install.sh check passed while the live copy had drifted"
bash "$INSTALL" deploy >/dev/null 2>&1
bash "$INSTALL" check >/dev/null 2>&1 || fail "install.sh check still reports drift after a redeploy"

chmod -x "$HOME/.git-hooks/pre-push"
bash "$INSTALL" check >/dev/null 2>&1
[ $? -eq 0 ] && fail "install.sh check passed while a hook was not executable"
chmod +x "$HOME/.git-hooks/pre-push"

# ==============================================================================
# 10. The no-watermarking gate fires through a real push, and fails closed.
#     The unit-level proof is test-watermark-scan.py; what is asserted here is
#     the DISPATCH - that the gate reaches EVERY push including one from a
#     repository on no allowlist, that the written opt-out is what takes one out
#     of scope, and that the secret scan's core.hooksPath skip does not carry
#     the watermark gate away with it.
# ==============================================================================
# A 1x1 PNG carrying a JUMBF box with a C2PA watermark assertion. Built here
# rather than committed: this repository is pushed through the very gate under
# test, so a checked-in fixture would make it unpushable by its own rule.
marked_png() { # <path>
    python3 - "$1" <<'PY'
import struct, sys, zlib
def chunk(kind, payload):
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
box = b"\x00\x00\x00\x20jumb\x00\x00\x00\x18jumdc2pa\x00\x11\x00\x10c2pa.assertions c2pa.watermarked.unbound"
png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
       + chunk(b"caBX", box) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b""))
open(sys.argv[1], "wb").write(png)
PY
}

seed "$TMP/marked"
allow "${TMP}/marked"
marked_png "$TMP/marked/hero.png"
git -C "$TMP/marked" add -A && git -C "$TMP/marked" commit --quiet -m "an AI-generated hero image"
git -C "$TMP/marked" push origin main >"$TMP/wm.log" 2>&1
check "allowlisted repo: a push carrying a watermarked asset is refused" refuse $?
said "watermark: refused in the gate's own words" "$TMP/wm.log" "DETECTIVE PROVENANCE"
# The remedy contract, corrected 2026-09-02: an unremovable mark is reported as
# unverified with every remedy named, not as "replace the asset" - that wording
# assumes a replacement exists, which is false when the artefact is the work.
said "watermark: the refusal states the mark is in the pixels" "$TMP/wm.log" "PIXELS"
said "watermark: the refusal names every remedy, not only replacement" "$TMP/wm.log" "Use it as it is"
said "watermark: the refusal names the clean remedy" "$TMP/wm.log" "watermark-scan.py clean"

# Fail closed on each half of its own payload, the same way the secret scan does.
mv "$HOME/.git-hooks/watermark-scan.py" "$TMP/wmscan.away"
echo "print('x')" > "$TMP/marked/x.py"
git -C "$TMP/marked" add -A && git -C "$TMP/marked" commit --quiet -m x
git -C "$TMP/marked" push origin main >/dev/null 2>&1
check "allowlisted repo with no deployed watermark scanner refuses" refuse $?
mv "$TMP/wmscan.away" "$HOME/.git-hooks/watermark-scan.py"
mv "$HOME/.git-hooks/watermark-allow.conf" "$TMP/wmallow.away"
git -C "$TMP/marked" push origin main >/dev/null 2>&1
check "allowlisted repo with no deployed watermark allowlist refuses" refuse $?
mv "$TMP/wmallow.away" "$HOME/.git-hooks/watermark-allow.conf"

# The asset out of the range, and the same push lands.
git -C "$TMP/marked" reset --quiet --hard origin/main
echo "print('clean')" > "$TMP/marked/ok.py"
git -C "$TMP/marked" add -A && git -C "$TMP/marked" commit --quiet -m ok
git -C "$TMP/marked" push --quiet origin main >/dev/null 2>&1
check "allowlisted repo: the push lands once the marked asset is out of the range" pass $?

# The property the opposite default exists for: a repository on NO allowlist,
# created just now, is gated from its very first push. Under the old wiring -
# the watermark gate sharing the secret scan's opt-in allowlist - this push
# landed, which is how every future project would have been born ungated.
seed "$TMP/wmnew"
marked_png "$TMP/wmnew/hero.png"
echo "print('new')" > "$TMP/wmnew/n.py"
git -C "$TMP/wmnew" add -A && git -C "$TMP/wmnew" commit --quiet -m "a brand new project, on no allowlist"
git -C "$TMP/wmnew" push origin main >"$TMP/wmnew.log" 2>&1
check "a repository on no allowlist is watermark-scanned from its first push" refuse $?
said "new repo: refused by the watermark gate" "$TMP/wmnew.log" "DETECTIVE PROVENANCE"

# ...and the opt-out is what takes one out of scope, only with a written reason.
echo "${TMP}/wmnew  # a fixture standing in for a third-party upstream" > "$HOME/.git-hooks/watermark-optout.conf"
git -C "$TMP/wmnew" push --quiet origin main >/dev/null 2>&1
check "an opted-out repository is left alone" pass $?
echo "${TMP}/wmnew" > "$HOME/.git-hooks/watermark-optout.conf"
echo "print('n2')" > "$TMP/wmnew/n2.py"
git -C "$TMP/wmnew" add -A && git -C "$TMP/wmnew" commit --quiet -m n2
git -C "$TMP/wmnew" push origin main >"$TMP/wmoptout.log" 2>&1
check "an opt-out entry with no reason is refused, not honoured" refuse $?
said "opt-out: refused for the missing reason" "$TMP/wmoptout.log" "must name its reason"
bash "$INSTALL" deploy >/dev/null 2>&1   # restore the shipped opt-out list

# The regression this gate is one `allowed=0` away from: a repository whose own
# core.hooksPath already ran the SECRET scan must still be watermark-scanned.
# 32 clones set core.hooksPath, `automation` among them, so folding the skip
# into `allowed` would silently exempt every one of them.
seed "$TMP/wmself"
allow "${TMP}/wmself"
mkdir -p "$TMP/wmself/.githooks"
cp "$CANONICAL" "$TMP/wmself/.githooks/pre-push"
chmod +x "$TMP/wmself/.githooks/pre-push"
cp "$ROOT/.gitleaks.toml" "$TMP/wmself/.gitleaks.toml"
git -C "$TMP/wmself" add -A && git -C "$TMP/wmself" commit --quiet -m "arm the repo hook"
git -C "$TMP/wmself" config core.hooksPath .githooks
git -C "$TMP/wmself" push --quiet origin main >/dev/null 2>&1
check "self-hooked repo: a clean push lands" pass $?
marked_png "$TMP/wmself/hero.png"
git -C "$TMP/wmself" add -A && git -C "$TMP/wmself" commit --quiet -m "marked asset"
git -C "$TMP/wmself" push origin main >"$TMP/wmself.log" 2>&1
check "self-hooked repo: the watermark gate still runs (the secret-scan skip must not carry it)" refuse $?
said "self-hooked repo: refused by the watermark gate" "$TMP/wmself.log" "DETECTIVE PROVENANCE"

# ==============================================================================
# 11. Opt-in: a real clone of a real allowlisted repository over the network.
#    This is the case the base allowlist actually covers - no pre-push-allow
#    entry, no per-repo setup, nothing but `git clone`.
# ==============================================================================
if [ "${GLOBAL_HOOKS_E2E:-0}" = 1 ]; then
    /bin/rm -f "$HOME/.git-hooks/pre-push-allow"
    # The throwaway HOME has no credential helper, so the clone - and only the
    # clone - borrows the machine's. Nothing else in this file reads real config.
    # --depth 1 so the push below scans two commits instead of the whole
    # history; the property under test is that the clone is scanned AT ALL.
    if GH_TOKEN="$E2E_TOKEN" \
        git -c "credential.https://github.com.helper=!gh auth git-credential" \
        clone --quiet --depth 1 https://github.com/Abhijeet34/papertrace.git "$TMP/e2e" 2>"$TMP/clone.err"; then
        git init --quiet --bare "$TMP/e2e-target.git"
        git -C "$TMP/e2e" remote add sandbox "$TMP/e2e-target.git"
        printf 'MODELS = {"kimi": "%s"}\n' "$SECRET" > "$TMP/e2e/leak.py"
        git -C "$TMP/e2e" add -A && git -C "$TMP/e2e" commit --quiet -m leak
        git -C "$TMP/e2e" push sandbox HEAD:refs/heads/main >"$TMP/e2e-push.log" 2>&1
        check "E2E: a real fresh clone refuses a secret with no setup at all" refuse $?
        said "E2E: refused for the secret" "$TMP/e2e-push.log" "secrets found in the commits being pushed"
    else
        fail "E2E: could not clone Abhijeet34/papertrace ($(head -1 "$TMP/clone.err"))"
    fi
else
    echo "note: the networked fresh-clone case did NOT run (set GLOBAL_HOOKS_E2E=1)"
fi

if [ "$failed" -ne 0 ]; then
    printf '\033[31m%d global-hook checks failed\033[0m\n' "$failed" >&2
    exit 1
fi
printf '\033[32mok\033[0m global pre-push scans allowlisted repos for secrets and watermarks, and leaves every other repo alone\n'
