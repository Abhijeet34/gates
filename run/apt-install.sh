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
# A retry goes to the next of $fallbacks, not back to the mirror that just timed out:
# 2026-11-19 a runner's regional mirror (us-east-2.ec2.archive.ubuntu.com, through its
# mirror+file: list) was slow on all three attempts, four runs in six hours. Every
# fallback is an ubuntu.com host, so nothing outside ubuntu.com is ever used.
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

# A retry moves every archive URI on the /ubuntu/ path (a direct ubuntu.com one, or a
# mirror+file: list naming one) to the next host; other paths, ports.ubuntu.com's
# /ubuntu-ports/ say, keep their host. Update and download run against the same host,
# because apt keeps package lists per URI.
fallbacks=(archive.ubuntu.com azure.archive.ubuntu.com)
on_path="${host}ubuntu/?([[:space:]]|\$)"
moved() { # moved <file> <base-url>: the file with its archive URIs on <base-url>
  local f=$1 base=$2 m r text
  text=$(cat "$f")
  while read -r m; do
    r=$m
    [ -f "$r" ] || r="$etc/$(basename "$r")"
    [ -f "$r" ] && grep -Eq "^$on_path" "$r" && text=${text//"mirror+file:$m"/$base}
  done < <(grep -E '^(URIs:|deb(-src)?[[:space:]])' "$f" | grep -oE 'mirror\+file:[^[:space:]]+' | sed 's/^mirror+file://' || true)
  printf '%s\n' "$text" | sed -E "/^(URIs:|deb(-src)?[[:space:]])/ s#$on_path#$base\\2#g"
}
via() { # via <n>: point opts at attempt n's sources; 1 is the configured mirror
  local n=$1 dir list=$main_list f
  if [ "$n" -eq 1 ]; then
    dir=$parts where="the configured mirror"
  else
    where=${fallbacks[$((n - 2))]}
    dir="$parts/via/$where"
    mkdir -p "$dir"
    for f in "$parts"/*; do
      [ -L "$f" ] && moved "$f" "http://$where/ubuntu/" >"$dir/$(basename "$f")"
    done
    if [ "$main_list" != /dev/null ]; then
      list="$dir.list"
      moved "$main_list" "http://$where/ubuntu/" >"$list"
    fi
  fi
  opts=(-o "Dir::Etc::sourcelist=$list" -o "Dir::Etc::sourceparts=$dir"
    -o Acquire::Retries=3 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30)
}
secs=${APT_ATTEMPT_SECS:-75}
attempts=$((${#fallbacks[@]} + 1))
tried=
# Only a timeout (124) is retried: any other failure already had apt's own retries,
# and a mirror+file: list's own fallback.
bounded() { # bounded <what> <apt-get args>...
  local what=$1 rc=0
  shift
  ${sudo[@]+"${sudo[@]}"} timeout -k 10 "$secs" "$apt" "${opts[@]}" "$@" || rc=$?
  case $rc in
    0 | 124) ;;
    *) echo "apt-install: $what failed (exit $rc) via $where; not a timeout, so not retried" >&2 ;;
  esac
  return "$rc"
}
for ((i = 1; i <= attempts; i++)); do
  via "$i"
  tried+="${tried:+, }$where"
  rc=0
  what=update
  bounded update update || rc=$?
  if [ "$rc" -eq 0 ]; then
    what=download
    bounded download install -y --no-install-recommends --download-only "$@" || rc=$?
  fi
  [ "$rc" -eq 0 ] && break
  [ "$rc" -eq 124 ] || exit "$rc"
  if [ "$i" -lt "$attempts" ]; then
    echo "apt-install: $what attempt $i of $attempts exceeded ${secs}s, retrying via ${fallbacks[$((i - 1))]} instead of $where" >&2
  else
    echo "apt-install: $what gave up after $attempts attempts of ${secs}s each ($tried); the archive is too slow from every ubuntu.com host tried" >&2
    exit 124
  fi
done
${sudo[@]+"${sudo[@]}"} "$apt" "${opts[@]}" install -y --no-install-recommends --no-download "$@"
