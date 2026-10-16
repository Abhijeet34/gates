#!/usr/bin/env bash
# Scan every commit of the caller's history, refusing a scan that read nothing.
set -uo pipefail

# Measured on gitleaks 8.30.1: a `gitleaks git` that cannot read the
# repository logs an ERR, reports "0 commits scanned." and STILL EXITS
# 0. Trusting that exit code reports a repository clean that was never
# read - a scan that fails open is worse than no scan. So count what
# git says exists and refuse unless the scan says it read it. Same
# rule, same reason, as .githooks/pre-push.
fail() {
  echo "::error::$1 COULD NOT ESTABLISH that this history is clean." >&2
  exit 1
}

# A shallow checkout scans the tip and pronounces the whole repository
# clean, which is exactly the false verdict `fetch-depth: 0` above is
# there to prevent. Assert it took effect rather than assume it.
[ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = false ] \
  || fail "the checkout is shallow, so the full history was never fetched."
expected=$(git rev-list --count --all) || fail "cannot enumerate this repository's commits."

# --log-level info is load-bearing: the commit count this parses is an
# INF line, so `warn` would hide the only evidence the scan ran.
# `rc=$?` on its own line would never run. The runner's shell is
# `bash -e {0}`, which aborts on the failing ASSIGNMENT, so a genuine
# leak - gitleaks exit 1 - took the step down before this body printed
# the findings or reached the `secrets found` message below. Red for
# the right reason and with no output at all, which reads exactly like
# a broken step. Measured 2026-09-02 by running the extracted body
# under -e, the way the runner runs it; .ci/test-secret-scan.sh does
# that now, and four of its cases were failing until this line changed.
rc=0
out=$(gitleaks git . --config .gitleaks.toml \
  --no-banner --no-color --redact --log-level info 2>&1) || rc=$?
printf '%s\n' "$out"

printf '%s\n' "$out" | grep -q ' ERR ' \
  && fail "gitleaks reported an error while reading the repository."
scanned=$(printf '%s\n' "$out" | sed -n 's/.*[^0-9]\([0-9][0-9]*\) commits scanned\..*/\1/p' | tail -1)
case "${scanned:-}" in
  '') fail "gitleaks did not report how many commits it scanned (unexpected output format)." ;;
esac
# Only that it read SOMETHING, never that it read all $expected.
# Measured on 8.30.1: gitleaks counts commits that contributed added
# content, so an empty commit and every merge commit are legitimately
# uncounted - an 8-commit repository reports 6. A `-ge $expected`
# comparison would therefore red-light every repository with a merge.
# Full coverage is established by the shallow check above instead.
if [ "$expected" -gt 0 ] && [ "$scanned" -eq 0 ]; then
  fail "gitleaks scanned 0 of the $expected commits in this repository."
fi

case $rc in
  0) echo "scanned $scanned commits, no leaks found" ;;
  1) echo "::error::secrets found in this repository's history - rotate the credential first, rewriting history does not un-leak it." >&2
     exit 1 ;;
  *) fail "the scan did not complete (gitleaks exit $rc)." ;;
esac
