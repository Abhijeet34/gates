#!/usr/bin/env bash
# Regression tests for run/apt-install.sh, the install the shared watermark
# workflow and ci.yml run. Run under `bash -e`, the way the runner runs it.
#
#   1. a broken third-party source is never handed to `apt-get update`,
#   2. the Ubuntu archive sources are, and the install keeps --no-install-recommends,
#   3. an archive source failing still fails the step,
#   4. a host with no archive source refuses rather than updating nothing.
#   5. lookalike hosts are refused and the retry and timeout options reach apt-get,
#   6. a download slower than its bound is killed and retried, then gives up named.
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
while [ "$#" -gt 0 ]; do
  case "$1" in
    Dir::Etc::sourceparts=*) parts=${1#*=} ;;
    Dir::Etc::sourcelist=*) list=${1#*=} ;;
    update)
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
  rm -f "$workdir/state"
  APT_ETC=$1 APT_GET="$workdir/apt-get" APT_SUDO='' STUB_LOG="$workdir/log" STUB_STATE="$workdir/state" \
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

# Control: handing apt-get every source reproduces the original failure.
mkdir -p "$workdir/etc3"; cp -R "$workdir/etc1/." "$workdir/etc3/"
: >"$workdir/log"
crc=0
STUB_LOG="$workdir/log" "$workdir/apt-get" -o "Dir::Etc::sourcelist=/dev/null" -o "Dir::Etc::sourceparts=$workdir/etc3/sources.list.d" update 2>/dev/null || crc=$?
[ "$crc" -eq 100 ] && pass "control: the stub fails when handed the Microsoft source" || fail "control did not fail"

exit "$failed"
