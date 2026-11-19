#!/usr/bin/env bash
# Regression tests for run/apt-install.sh, the install the shared watermark
# workflow and ci.yml run. Run under `bash -e`, the way the runner runs it.
#
#   1. a broken third-party source is never handed to `apt-get update`,
#   2. the Ubuntu archive sources are, and the install keeps --no-install-recommends,
#   3. an archive source failing still fails the step,
#   4. a host with no archive source refuses rather than updating nothing.
#   5. lookalike hosts are refused and the retry and timeout options reach apt-get,
#   6. a download slower than its bound is killed and retried, then gives up named,
#   7. a mirror slow on every attempt is left for another ubuntu.com host, and only
#      ubuntu.com hosts are ever used.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

workdir=$(mktemp -d) || exit 2
trap 'rm -rf "$workdir"' EXIT
failed=0
pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failed=1; }

# A stand-in apt-get that fails the way the runner did when it is handed the
# Microsoft source, or any source named in FAIL_ON.
cat >"$workdir/apt-get" <<'STUB'
#!/usr/bin/env bash
echo "$*" >>"$STUB_LOG"
parts= list=
# Every host the sources handed to this call name, a mirror+file: list's included.
hosts() {
  local f m
  for f in "$parts"/* "$list"; do
    [ -f "$f" ] || continue
    grep -oE 'https?://[^/[:space:]]+' "$f" || true
    for m in $(grep -oE 'mirror\+file:[^[:space:]]+' "$f" | sed 's/^mirror+file://'); do
      [ -f "$m" ] || m="$APT_ETC/$(basename "$m")"
      grep -oE '^https?://[^/[:space:]]+' "$m" || true
    done
  done
}
# A mirror slow on every attempt: any call whose sources name SLOW_HOST outlives its bound.
slow_host() {
  hosts >>"${STUB_HOSTS:-/dev/null}"
  [ -z "${SLOW_HOST:-}" ] || ! hosts | grep -Eq "$SLOW_HOST" || sleep 5
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    Dir::Etc::sourceparts=*) parts=${1#*=} ;;
    Dir::Etc::sourcelist=*) list=${1#*=} ;;
    update)
      slow_host
      for f in "$parts"/* "$list"; do
        [ -f "$f" ] || continue
        if grep -Eq "${FAIL_ON:-packages\.microsoft\.com}" "$f"; then
          echo "E: Failed to fetch $f 403 Forbidden" >&2
          exit 100
        fi
      done ;;
    install)
      # A trickling mirror: the first SLOW_ATTEMPTS downloads outlive their bound.
      case " ${*} " in *" --no-download "*) ;; *)
        slow_host
        n=$(($(cat "$STUB_STATE" 2>/dev/null || echo 0) + 1)); echo "$n" >"$STUB_STATE"
        [ "$n" -gt "${SLOW_ATTEMPTS:-0}" ] || sleep 5 ;;
      esac ;;
  esac
  shift
done
STUB
chmod +x "$workdir/apt-get"

mketc() {
  local etc=$1
  mkdir -p "$etc/sources.list.d"
  printf 'Types: deb\nURIs: http://azure.archive.ubuntu.com/ubuntu/\nSuites: noble\n' >"$etc/sources.list.d/ubuntu.sources"
  printf 'Types: deb\nURIs: https://packages.microsoft.com/ubuntu/24.04/prod\nSuites: noble\n' >"$etc/sources.list.d/microsoft-prod.sources"
  printf 'deb https://ppa.example.test/x noble main\n' >"$etc/sources.list.d/other.list"
}
run() { # run <etc> -> sets rc, log
  : >"$workdir/log"
  rc=0
  rm -f "$workdir/state" "$workdir/hosts"
  APT_ETC=$1 APT_GET="$workdir/apt-get" APT_SUDO='' STUB_LOG="$workdir/log" STUB_STATE="$workdir/state" STUB_HOSTS="$workdir/hosts" \
    bash -e run/apt-install.sh libimage-exiftool-perl >"$workdir/out" 2>&1 || rc=$?
}

mketc "$workdir/etc1"
run "$workdir/etc1"
[ "$rc" -eq 0 ] && pass "broken Microsoft source does not fail the install" || fail "install failed with only a third-party source broken: $(cat "$workdir/out")"
grep -q 'update' "$workdir/log" && grep -q 'install -y --no-install-recommends --no-download libimage-exiftool-perl' "$workdir/log" \
  && pass "update then a recommends-free install of the named package" || fail "unexpected apt-get calls: $(cat "$workdir/log")"

[ "$(grep -c 'Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30' "$workdir/log")" -eq 3 ] \
  && pass "retry and timeout options reach update, download and install" || fail "apt-get missed the retry/timeout options: $(cat "$workdir/log")"

# The archive source itself failing must still fail the job.
export FAIL_ON='ubuntu\.com'; run "$workdir/etc1"; unset FAIL_ON
[ "$rc" -ne 0 ] && pass "a failing archive source still fails" || fail "archive failure was swallowed"

mkdir -p "$workdir/etc2/sources.list.d"
printf 'Types: deb\nURIs: https://packages.microsoft.com/x\n' >"$workdir/etc2/sources.list.d/m.sources"
run "$workdir/etc2"
[ "$rc" -ne 0 ] && [ ! -s "$workdir/log" ] && pass "no archive source refuses before calling apt-get" || fail "ran without an archive source"

mkdir -p "$workdir/etc4/sources.list.d"
printf 'Types: deb\nURIs: https://notubuntu.com/ubuntu/\n' >"$workdir/etc4/sources.list.d/n.sources"
printf 'deb https://ppa.example.test/ubuntu.com/x noble main\n' >"$workdir/etc4/sources.list.d/p.list"
run "$workdir/etc4"
[ "$rc" -ne 0 ] && [ ! -s "$workdir/log" ] && pass "lookalike hosts and paths are not archive sources" || fail "a lookalike source was kept"

# The ubuntu-24.04 runner layout: the archive is a deb822 file that points at a
# mirror list, beside a one-line Microsoft source that answers 403.
etc5="$workdir/etc5"; mkdir -p "$etc5/sources.list.d"
printf 'Types: deb\nURIs: mirror+file:/etc/apt/apt-mirrors.txt\nSuites: noble noble-updates\nComponents: main\n' >"$etc5/sources.list.d/ubuntu.sources"
printf 'http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\nhttp://archive.ubuntu.com/ubuntu/\tpriority:2\n' >"$etc5/apt-mirrors.txt"
printf 'deb [arch=amd64,arm64,armhf signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/ubuntu/24.04/prod noble main\n' >"$etc5/sources.list.d/microsoft-prod.list"
run "$etc5"
[ "$rc" -eq 0 ] && pass "runner layout: archive via mirror list kept, Microsoft .list left out" || fail "runner layout failed: $(cat "$workdir/out")"
FAIL_ON='mirror\+file'; export FAIL_ON; run "$etc5"; unset FAIL_ON
[ "$rc" -ne 0 ] && pass "runner layout: the mirror-listed archive failing still fails" || fail "mirror archive failure was swallowed"

# A slow mirror (2026-10-26: 67.9 MB at about 150 kB/s, where main fetched it at
# 21 MB/s): each download is bounded, retried, and the install never downloads.
export APT_ATTEMPT_SECS=1 SLOW_ATTEMPTS=1; run "$workdir/etc1"
[ "$rc" -eq 0 ] && [ "$(grep -c 'install -y --no-install-recommends --download-only libimage-exiftool-perl' "$workdir/log")" -eq 2 ] \
  && grep -q 'install -y --no-install-recommends --no-download libimage-exiftool-perl' "$workdir/log" \
  && grep -q 'apt-install: download attempt 1 of 3 exceeded 1s, retrying' "$workdir/out" \
  && pass "a slow download is killed at its bound and retried" || fail "slow download not bounded and retried (rc=$rc): $(cat "$workdir/out" "$workdir/log")"
SLOW_ATTEMPTS=9; run "$workdir/etc1"; unset APT_ATTEMPT_SECS SLOW_ATTEMPTS
[ "$rc" -ne 0 ] && grep -q 'apt-install: download gave up after 3 attempts of 1s each' "$workdir/out" \
  && ! grep -q -- '--no-download' "$workdir/log" \
  && pass "a mirror slow on every attempt fails named, not at the step timeout" || fail "persistently slow mirror (rc=$rc): $(cat "$workdir/out")"

# 2026-11-19: the runner's mirror list put a regional EC2 mirror first, and every
# retry went back to it. A retry now moves the archive to the next ubuntu.com host;
# ports.ubuntu.com's /ubuntu-ports/ path keeps its host, and the third-party source
# is still left out.
etc6="$workdir/etc6"; mkdir -p "$etc6/sources.list.d"
printf 'Types: deb\nURIs: mirror+file:/etc/apt/apt-mirrors.txt\nSuites: noble noble-updates\nComponents: main\n\nTypes: deb\nURIs: http://security.ubuntu.com/ubuntu/\nSuites: noble-security\nComponents: main\n' >"$etc6/sources.list.d/ubuntu.sources"
printf 'http://us-east-2.ec2.archive.ubuntu.com/ubuntu/\tpriority:1\nhttp://archive.ubuntu.com/ubuntu/\tpriority:2\nhttp://security.ubuntu.com/ubuntu/\tpriority:3\n' >"$etc6/apt-mirrors.txt"
printf 'deb [arch=arm64] http://ports.ubuntu.com/ubuntu-ports noble main\n' >"$etc6/sources.list"
printf 'deb [arch=amd64,arm64,armhf signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/ubuntu/24.04/prod noble main\n' >"$etc6/sources.list.d/microsoft-prod.list"
ubuntu_only() { ! grep -Ev '^https?://([A-Za-z0-9-]+\.)*ubuntu\.com$' "$workdir/hosts"; }
export APT_ATTEMPT_SECS=1 SLOW_HOST='us-east-2\.ec2\.archive\.ubuntu\.com'; run "$etc6"
last=$(tail -1 "$workdir/log")
[ "$rc" -eq 0 ] && grep -q 'apt-install: update attempt 1 of 3 exceeded 1s, retrying via archive.ubuntu.com' "$workdir/out" \
  && [[ $last == *'install -y --no-install-recommends --no-download libimage-exiftool-perl' ]] \
  && [[ $last == *'sourceparts='*'/via/archive.ubuntu.com '* ]] \
  && [ "$(grep -c '^http://archive.ubuntu.com$' "$workdir/hosts")" -ge 2 ] \
  && [ "$(tail -4 "$workdir/hosts" | sort -u | tr '\n' ' ')" = 'http://archive.ubuntu.com http://ports.ubuntu.com ' ] \
  && pass "a mirror slow on every attempt is left for archive.ubuntu.com, ports kept" || fail "slow regional mirror not left (rc=$rc): $(cat "$workdir/out" "$workdir/log" "$workdir/hosts")"
ubuntu_only && pass "the fallback never uses a host outside ubuntu.com" || fail "a non-ubuntu.com host was used"
SLOW_HOST='ubuntu\.com'; run "$etc6"; unset APT_ATTEMPT_SECS SLOW_HOST
[ "$rc" -eq 124 ] && grep -q 'apt-install: update gave up after 3 attempts of 1s each (the configured mirror, archive.ubuntu.com, azure.archive.ubuntu.com)' "$workdir/out" \
  && ! grep -q -- '--download-only' "$workdir/log" && ubuntu_only \
  && pass "an archive slow from every ubuntu.com host fails naming each one" || fail "slow everywhere (rc=$rc): $(cat "$workdir/out" "$workdir/hosts")"
FAIL_ON='ubuntu\.com'; export FAIL_ON; run "$etc6"; unset FAIL_ON
[ "$rc" -eq 100 ] && grep -q 'apt-install: update failed (exit 100) via the configured mirror; not a timeout, so not retried' "$workdir/out" \
  && [ "$(grep -c ' update$' "$workdir/log")" -eq 1 ] \
  && pass "an archive source that is down fails at once, named" || fail "down source (rc=$rc): $(cat "$workdir/out")"

# Control: handing apt-get every source reproduces the original failure.
mkdir -p "$workdir/etc3"; cp -R "$workdir/etc1/." "$workdir/etc3/"
: >"$workdir/log"
crc=0
STUB_LOG="$workdir/log" "$workdir/apt-get" -o "Dir::Etc::sourcelist=/dev/null" -o "Dir::Etc::sourceparts=$workdir/etc3/sources.list.d" update 2>/dev/null || crc=$?
[ "$crc" -eq 100 ] && pass "control: the stub fails when handed the Microsoft source" || fail "control did not fail"

exit "$failed"
