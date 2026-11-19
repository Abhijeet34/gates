#!/usr/bin/env bash
# Install apt packages from the Ubuntu archive alone. A hosted runner image ships
# third-party sources (Microsoft, Azure CLI, ...) that a job installing from the
# archive never uses, and one of them answering 403 fails a plain `apt-get update`
# with exit 100 (measured 2026-10-25). An archive source being down still fails.
#
#   run/apt-install.sh PACKAGE...
#
# A slow mirror is not killed for being slow, only for stopping. 2026-10-26 one runner
# fetched at about 150 kB/s what main fetched at 21 MB/s, never idle long enough for
# apt's own Timeout. By 2026-11-19, 17 of gates' last 30 CI runs had failed, and that
# day a fixed 75s kill timed out on the regional mirror, then archive.ubuntu.com, then
# azure.archive.ubuntu.com: every host was slow, and a host switch forces a fresh
# package-list update. So an attempt is killed and retried only when the bytes apt
# has fetched stop changing for APT_STALL_SECS; finished debs stay in apt's cache, so a
# retry resumes. APT_TOTAL_SECS still bounds the whole install, so a job never hangs.
#
# APT_ARCHIVES moves apt's archive cache (Dir::Cache::archives) to a directory a job
# can carry between runs; ci.yml's secret scan does, because at the 30-50 kB/s seen
# 2026-11-19 its 70 MB of debs outlasts the step. apt checks each cached deb against
# the signed package lists it fetches fresh, so a stale or altered one is refetched.
#
# APT_ETC, APT_GET, APT_SUDO and APT_LISTS exist for .ci/test-apt-install.sh.
set -euo pipefail

[ "$#" -gt 0 ] || { echo "apt-install: no packages named" >&2; exit 2; }
etc=${APT_ETC:-/etc/apt}
apt=${APT_GET:-apt-get}
read -r -a sudo <<<"${APT_SUDO-sudo}"

parts=$(mktemp -d)
trap 'rm -rf "$parts"' EXIT

# A source is the archive's when it names an ubuntu.com host (archive, security,
# azure.archive mirror), directly or through a `mirror+file:` list, which is how a
# hosted runner image writes it (deb822 ubuntu.sources or a one-line entry). Anything
# else is left out of this run, not removed.
host='https?://([A-Za-z0-9-]+\.)*ubuntu\.com/'
is_archive() {
  local f=$1 m
  grep -Eq "^(URIs:|deb(-src)?[[:space:]]).*$host" "$f" && return 0
  while read -r m; do
    [ -f "$m" ] || m="$etc/$(basename "$m")"
    [ -f "$m" ] && grep -Eq "^$host" "$m" && return 0
  done < <(grep -E '^(URIs:|deb(-src)?[[:space:]])' "$f" | grep -oE 'mirror\+file:[^[:space:]]+' | sed 's/^mirror+file://' || true)
  return 1
}

kept=0
for f in "$etc"/sources.list.d/*.sources "$etc"/sources.list.d/*.list; do
  [ -f "$f" ] || continue
  if is_archive "$f"; then
    ln -s "$f" "$parts/$(basename "$f")"
    kept=$((kept + 1))
  fi
done
if [ -f "$etc/sources.list" ] && is_archive "$etc/sources.list"; then
  main_list="$etc/sources.list"
  kept=$((kept + 1))
else
  main_list=/dev/null
fi
[ "$kept" -gt 0 ] || { echo "apt-install: no Ubuntu archive source under $etc" >&2; exit 1; }

archives=${APT_ARCHIVES:-/var/cache/apt/archives}
mkdir -p "$archives/partial" 2>/dev/null || ${sudo[@]+"${sudo[@]}"} mkdir -p "$archives/partial"
opts=(-o "Dir::Etc::sourcelist=$main_list" -o "Dir::Etc::sourceparts=$parts"
  -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30
  -o "Dir::Cache::archives=$archives/")
stall=${APT_STALL_SECS:-60}
total=${APT_TOTAL_SECS:-900}
lists=${APT_LISTS:-/var/lib/apt/lists}
attempts=3
# apt's partial dirs are readable by root alone, hence sudo.
fetched() {
  { ${sudo[@]+"${sudo[@]}"} du -sb "$lists/partial" "$archives" 2>/dev/null || true; } | awk '{ s += $1 } END { print s + 0 }'
}
start=$(fetched)
ceiling() { # ceiling <what>
  echo "apt-install: $what stopped at the APT_TOTAL_SECS ceiling of ${total}s after ${SECONDS}s, $(($(fetched) - start)) bytes fetched; the archive mirror is too slow" >&2
}
# watched <apt-get args>...: 0, apt-get's own failure, 3 when no byte moved for $stall
# seconds, or timeout's 124 at the overall ceiling.
watched() {
  local pid rc=0 last now quiet=$SECONDS left=$((total - SECONDS))
  [ "$left" -gt 0 ] || return 124
  ${sudo[@]+"${sudo[@]}"} timeout -k 10 "$left" "$apt" "${opts[@]}" "$@" &
  pid=$!
  last=$(fetched)
  while [ -n "$(jobs -rp)" ]; do
    sleep 1
    now=$(fetched)
    [ "$now" = "$last" ] || { last=$now; quiet=$SECONDS; }
    if [ $((SECONDS - quiet)) -ge "$stall" ]; then
      ${sudo[@]+"${sudo[@]}"} kill -TERM "$pid" 2>/dev/null || true
      wait "$pid" || true
      return 3
    fi
  done
  wait "$pid" || rc=$?
  return "$rc"
}
# Only a stall is retried: any other failure already had apt's own retries.
bounded() { # bounded <what> <apt-get args>...
  local what=$1 i rc
  shift
  for ((i = 1; i <= attempts; i++)); do
    rc=0
    watched "$@" || rc=$?
    case $rc in
      3) ;;
      124) ceiling; return 124 ;;
      *) return "$rc" ;;
    esac
    [ "$i" -lt "$attempts" ] && echo "apt-install: $what attempt $i of $attempts made no progress for ${stall}s, retrying" >&2
  done
  echo "apt-install: $what gave up after $attempts attempts that each made no progress for ${stall}s; the archive mirror stopped sending" >&2
  return 124
}
bounded update update
bounded download install -y --no-install-recommends --download-only "$@"
rc=0
what=install
${sudo[@]+"${sudo[@]}"} timeout -k 10 "$((total > SECONDS ? total - SECONDS : 1))" "$apt" "${opts[@]}" install -y --no-install-recommends --no-download "$@" || rc=$?
[ "$rc" -ne 124 ] || ceiling
exit "$rc"
