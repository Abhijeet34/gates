#!/usr/bin/env bash
# Read the caller's open Dependabot alerts into $ALERTS_JSON.
# Separate from the verdict because it is the only part that touches the
# network, which is what lets .ci/test-dependency-advisories.sh drive the
# verdict against fixture inventories with no API at all, and drive this part
# against a stub curl for every way the read can fail.
set -uo pipefail

# A visibility gate that goes quiet when it cannot see is the exact
# defect this workflow exists to prevent, so every path out of this
# step that is not a complete read is a refusal. Same wording, same
# reason, as shared-secret-scan.yml's history scan.
fail() {
  echo "::error::$1 COULD NOT ESTABLISH this repository's advisory state." >&2
  exit 1
}

api="${GITHUB_API_URL:-https://api.github.com}"
tmp="${RUNNER_TEMP:-.}"
: >"$tmp/pages.list"

# Cursor pagination, not `page=`. Measured: this endpoint answers
# HTTP 400 to a `page` parameter - it is one of the few REST endpoints
# that paginates only by the Link header's before/after cursors, so
# the ordinary page-counting loop refuses everything. The loop was
# exercised live at per_page=25 across 3 pages, merging to the same
# count `gh api --paginate` reports (2026-09-02). A loop that silently
# stopped at page 1 would report the rest of a repository clean.
url="$api/repos/$GITHUB_REPOSITORY/dependabot/alerts?state=open&per_page=100"
page=0
# 100 alerts a page. A repository past 3000 open alerts is a different
# problem, but the cap refuses rather than truncating: a silent ceiling
# reads exactly like a clean tail.
max_pages=30
while [ -n "$url" ]; do
  page=$((page + 1))
  if [ "$page" -gt "$max_pages" ]; then
    fail "this repository has more than $((max_pages * 100)) open alerts, which this check will not silently truncate."
  fi
  body="$tmp/alerts-page-$page.json"
  hdr="$tmp/alerts-page-$page.head"
  # `curl_rc=$?` on the next line would never run: the runner's shell
  # is `bash -e`, which aborts on the failing assignment itself, so the
  # refusal has to be attached to the assignment.
  curl_rc=0
  code=$(curl -sS -D "$hdr" -o "$body" -w '%{http_code}' \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$url") || curl_rc=$?
  [ "$curl_rc" -eq 0 ] || fail "the request for page $page did not complete (curl exit $curl_rc)."

  case "$code" in
    200) ;;
    403)
      # Two different causes, one status. Naming which one is what
      # turns this refusal into something a reader can act on.
      if grep -qi 'disabled' "$body" 2>/dev/null; then
        fail "Dependabot alerts are DISABLED on this repository (HTTP 403). Enable them in Settings -> Advanced Security, or stop calling this workflow;"
      fi
      fail "the token was refused (HTTP 403, \"$(jq -r '.message? // "no message"' "$body" 2>/dev/null)\"). The CALLER must grant \`vulnerability-alerts: read\` - a called workflow cannot widen the caller's GITHUB_TOKEN;"
      ;;
    404) fail "the alerts endpoint answered HTTP 404, so this repository has no dependency graph or no alerts feature;" ;;
    *)   fail "the alerts endpoint answered HTTP $code;" ;;
  esac

  jq -e 'type == "array"' "$body" >/dev/null 2>&1 \
    || fail "page $page was HTTP 200 but is not a JSON array (the endpoint answered with something else);"

  printf '%s\n' "$body" >>"$tmp/pages.list"
  # No Link header, or one with no `next`, is the normal last page and
  # both make this pipeline exit non-zero under pipefail, so the empty
  # result has to be spelled rather than inherited.
  url=$(tr -d '\r' <"$hdr" | grep -i '^link:' | tr ',' '\n' \
    | grep 'rel="next"' | sed -n 's/.*<\(.*\)>.*/\1/p' | head -1) || url=""
done

# shellcheck disable=SC2046
jq -s 'add' $(tr '\n' ' ' <"$tmp/pages.list") >"$ALERTS_JSON" \
  || fail "the pages could not be merged;"

# An alert whose severity or ghsa id is absent cannot be judged, and
# judging it as clean is the failure mode this file exists to prevent.
unjudgeable=$(jq '[.[] | select(
    (.security_advisory.severity | type) != "string"
    or (.security_advisory.ghsa_id | type) != "string"
    or (.dependency.package.name | type) != "string"
  )] | length' "$ALERTS_JSON") \
  || fail "the merged inventory could not be inspected;"
[ "${unjudgeable:-1}" = 0 ] \
  || fail "$unjudgeable of the alerts returned are missing a severity, a GHSA id or a package name, so they cannot be judged;"

echo "read $(jq 'length' "$ALERTS_JSON") open alerts over $page page(s)"
