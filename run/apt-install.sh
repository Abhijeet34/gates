#!/usr/bin/env bash
# Install apt packages from the Ubuntu archive alone. A hosted runner image ships
# third-party sources (Microsoft, Azure CLI, ...) that a job installing from the
# archive never uses, and one of them answering 403 fails a plain `apt-get update`
# with exit 100 (measured 2026-10-25). An archive source being down still fails.
#
#   run/apt-install.sh PACKAGE...
#
# A slow mirror is bounded too: 2026-10-26 one runner fetched the same 67.9 MB that
# main fetched at 21 MB/s at about 150 kB/s, never idle long enough for apt's own
# Timeout, so each update and download attempt is killed at APT_ATTEMPT_SECS and
# retried; finished debs stay in apt's cache, so a retry resumes rather than restarts.
#
# APT_ETC, APT_GET and APT_SUDO exist for .ci/test-apt-install.sh.
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

opts=(-o "Dir::Etc::sourcelist=$main_list" -o "Dir::Etc::sourceparts=$parts"
  -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
secs=${APT_ATTEMPT_SECS:-75}
attempts=3
# Only a timeout (124) is retried: any other failure already had apt's own retries.
bounded() { # bounded <what> <apt-get args>...
  local what=$1 i rc
  shift
  for ((i = 1; i <= attempts; i++)); do
    rc=0
    ${sudo[@]+"${sudo[@]}"} timeout -k 10 "$secs" "$apt" "${opts[@]}" "$@" || rc=$?
    [ "$rc" -eq 124 ] || return "$rc"
    [ "$i" -lt "$attempts" ] && echo "apt-install: $what attempt $i of $attempts exceeded ${secs}s, retrying" >&2
  done
  echo "apt-install: $what gave up after $attempts attempts of ${secs}s each; the archive mirror is too slow" >&2
  return 124
}
bounded update update
bounded download install -y --no-install-recommends --download-only "$@"
${sudo[@]+"${sudo[@]}"} "$apt" "${opts[@]}" install -y --no-install-recommends --no-download "$@"
