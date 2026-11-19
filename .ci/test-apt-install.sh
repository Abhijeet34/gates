#!/usr/bin/env bash
# Regression tests for run/apt-install.sh, the install the shared watermark
# workflow and ci.yml run. Run under `bash -e`, the way the runner runs it.
#
#   1. a broken third-party source is never handed to `apt-get update`,
#   2. the Ubuntu archive sources are, and the install keeps --no-install-recommends,
#   3. an archive source failing still fails the step,
#   4. a host with no archive source refuses rather than updating nothing.
#   5. lookalike hosts are refused and the retry and timeout options reach apt-get,
#   6. a download that stops sending is killed and retried, then gives up named,
#   7. a download that keeps sending runs past the old fixed bound to completion,
#   8. the overall ceiling fails the install, naming bytes fetched and time taken,
#   9. APT_ARCHIVES is apt's archive cache: a deb already there is used, not refetched.
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
parts= list= cache=$STUB_DEFAULT_CACHE
while [ "$#" -gt 0 ]; do
  case "$1" in
    Dir::Etc::sourceparts=*) parts=${1#*=} ;;
    Dir::Etc::sourcelist=*) list=${1#*=} ;;
    Dir::Cache::archives=*) cache=${1#*=} ;;
    update)
      for f in "$parts"/* "$list"; do
        [ -f "$f" ] || continue
        if grep -Eq "${FAIL_ON:-packages\.microsoft\.com}" "$f"; then
          echo "E: Failed to fetch $f 403 Forbidden" >&2
          exit 100
        fi
      done ;;
    install)
      # The download fills Dir::Cache::archives the way apt does: a deb already there
      # is used, and anything else is written to partial/ and moved in when complete.
      # The first SLOW_ATTEMPTS send a little and then stop; with TRICKLE_SECS set,
      # a byte every half second. The install that may not download needs the deb.
      case " ${*} " in
        *" --no-download "*)
          [ -f "$cache/pkg.deb" ] || { echo "E: pkg.deb is not in $cache" >&2; exit 100; } ;;
        *)
          n=$(($(cat "$STUB_STATE" 2>/dev/null || echo 0) + 1)); echo "$n" >"$STUB_STATE"
          [ -f "$cache/pkg.deb" ] && exit 0
          echo "fetched pkg.deb" >>"$STUB_LOG"
          mkdir -p "$cache/partial"
          if [ "$n" -le "${SLOW_ATTEMPTS:-0}" ]; then
            printf 'started' >>"$cache/partial/pkg.deb"; sleep 30
          fi
          for ((t = 0; t < ${TRICKLE_SECS:-0} * 2; t++)); do
            printf x >>"$cache/partial/pkg.deb"; sleep 0.5
          done
          printf 'deb' >>"$cache/partial/pkg.deb"
          mv "$cache/partial/pkg.deb" "$cache/pkg.deb" ;;
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
  rm -rf -- "${workdir:?}/cache"
  mkdir -p "$workdir/cache/lists/partial" "$workdir/cache/archives/partial" "$workdir/cache/default/partial"
  [ -z "${PREPLACE:-}" ] || printf '%s' "$PREPLACE" >"$workdir/cache/archives/pkg.deb"
  APT_ETC=$1 APT_GET="$workdir/apt-get" APT_SUDO='' STUB_LOG="$workdir/log" STUB_STATE="$workdir/state" \
    STUB_DEFAULT_CACHE="$workdir/cache/default" \
    APT_LISTS="$workdir/cache/lists" APT_ARCHIVES="$workdir/cache/archives" \
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

# A download that stops sending is killed once no byte has moved for APT_STALL_SECS,
# well before its 30s sleep ends, and retried; the install never downloads.
# APT_ATTEMPT_SECS is the fixed bound the watchdog replaced (75s, here 2s), set so that
# on the old script these cases show what it did instead.
export APT_STALL_SECS=2 APT_ATTEMPT_SECS=2 SLOW_ATTEMPTS=1
SECONDS=0; run "$workdir/etc1"; took=$SECONDS
[ "$rc" -eq 0 ] && [ "$took" -lt 15 ] && [ "$(grep -c 'install -y --no-install-recommends --download-only libimage-exiftool-perl' "$workdir/log")" -eq 2 ] \
  && grep -q 'install -y --no-install-recommends --no-download libimage-exiftool-perl' "$workdir/log" \
  && grep -q 'apt-install: download attempt 1 of 3 made no progress for 2s, retrying' "$workdir/out" \
  && pass "a download that stops sending is killed after APT_STALL_SECS and retried" || fail "stalled download not killed and retried (rc=$rc, ${took}s): $(cat "$workdir/out" "$workdir/log")"
SLOW_ATTEMPTS=9; run "$workdir/etc1"; unset SLOW_ATTEMPTS
[ "$rc" -ne 0 ] && grep -q 'apt-install: download gave up after 3 attempts that each made no progress for 2s' "$workdir/out" \
  && ! grep -q -- '--no-download' "$workdir/log" \
  && pass "a mirror that stops on every attempt fails named, not at the step timeout" || fail "persistently stalled mirror (rc=$rc): $(cat "$workdir/out")"

# 2026-11-19: every ubuntu.com host was slow but sending. A download that keeps
# writing for twice the old bound and twice APT_STALL_SECS runs once, to completion.
TRICKLE_SECS=4; export TRICKLE_SECS; run "$workdir/etc1"; unset TRICKLE_SECS
[ "$rc" -eq 0 ] && [ "$(grep -c -- '--download-only' "$workdir/log")" -eq 1 ] \
  && grep -q -- '--no-download libimage-exiftool-perl' "$workdir/log" && ! grep -q 'apt-install:' "$workdir/out" \
  && pass "a slow download that keeps making progress runs to completion" || fail "a progressing download was killed (rc=$rc): $(cat "$workdir/out" "$workdir/log")"

# The ceiling: a download still sending at APT_TOTAL_SECS fails, naming bytes and time.
export TRICKLE_SECS=60 APT_TOTAL_SECS=4
SECONDS=0; run "$workdir/etc1"; took=$SECONDS; unset TRICKLE_SECS APT_TOTAL_SECS APT_STALL_SECS APT_ATTEMPT_SECS
[ "$rc" -eq 124 ] && [ "$took" -lt 20 ] && ! grep -q -- '--no-download' "$workdir/log" \
  && grep -Eq 'apt-install: download stopped at the APT_TOTAL_SECS ceiling of 4s after [0-9]+s, [1-9][0-9]* bytes fetched' "$workdir/out" \
  && pass "the overall ceiling fails the install, naming bytes fetched and elapsed time" || fail "ceiling (rc=$rc, ${took}s): $(cat "$workdir/out")"

# The secret scan caches APT_ARCHIVES between runs: a deb restored there is what the
# download finds, so nothing is fetched and the install uses the restored file.
PREPLACE='restored deb'; export PREPLACE; run "$workdir/etc1"; unset PREPLACE
[ "$rc" -eq 0 ] && grep -q "Dir::Cache::archives=$workdir/cache/archives/" "$workdir/log" \
  && grep -q -- '--download-only libimage-exiftool-perl' "$workdir/log" && ! grep -q '^fetched' "$workdir/log" \
  && grep -q -- '--no-download libimage-exiftool-perl' "$workdir/log" \
  && [ "$(cat "$workdir/cache/archives/pkg.deb")" = 'restored deb' ] \
  && pass "a deb already in APT_ARCHIVES is used and nothing is re-downloaded" || fail "archive cache not honoured (rc=$rc): $(cat "$workdir/out" "$workdir/log")"
run "$workdir/etc1"
[ "$rc" -eq 0 ] && [ "$(grep -c '^fetched' "$workdir/log")" -eq 1 ] && [ -f "$workdir/cache/archives/pkg.deb" ] \
  && [ "$(ls -A "$workdir/cache/default")" = partial ] && [ -z "$(ls -A "$workdir/cache/default/partial")" ] \
  && pass "control: an empty APT_ARCHIVES is downloaded into, not apt's default cache" || fail "download did not land in APT_ARCHIVES (rc=$rc): $(cat "$workdir/out" "$workdir/log"; ls -R "$workdir/cache")"

# Control: handing apt-get every source reproduces the original failure.
mkdir -p "$workdir/etc3"; cp -R "$workdir/etc1/." "$workdir/etc3/"
: >"$workdir/log"
crc=0
STUB_LOG="$workdir/log" "$workdir/apt-get" -o "Dir::Etc::sourcelist=/dev/null" -o "Dir::Etc::sourceparts=$workdir/etc3/sources.list.d" update 2>/dev/null || crc=$?
[ "$crc" -eq 100 ] && pass "control: the stub fails when handed the Microsoft source" || fail "control did not fail"

exit "$failed"
