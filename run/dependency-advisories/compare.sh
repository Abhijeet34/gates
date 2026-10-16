#!/usr/bin/env bash
# Read the dependency changes a pull request makes into $COMPARE_JSON.
# Never exits non-zero: it records either the changes or the reason it could
# not read them, and judge.sh decides whether that reason matters (the
# workflow's header says when). A missing record is itself a refusal.
set -uo pipefail

record_error() {
  jq -n --arg e "$1" '{error: $e}' >"$COMPARE_JSON"
  echo "the dependency compare COULD NOT BE READ: $1"
  exit 0
}

api="${GITHUB_API_URL:-https://api.github.com}"
tmp="${RUNNER_TEMP:-.}"
body="$tmp/compare.json"
hdr="$tmp/compare.head"
curl_rc=0
code=$(curl -sS -D "$hdr" -o "$body" -w '%{http_code}' \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$api/repos/$GITHUB_REPOSITORY/dependency-graph/compare/$BASE_SHA...$HEAD_SHA") || curl_rc=$?
[ "$curl_rc" -eq 0 ] || record_error "the request did not complete (curl exit $curl_rc)"

message=$(jq -r '.message? // empty' "$body" 2>/dev/null) || message=""
case "$code" in
  200) ;;
  403) record_error "HTTP 403, \"${message:-no message}\" (GitHub answers 403 here for a private repository without Advanced Security, and for a token without \`contents: read\`)" ;;
  404) record_error "HTTP 404, \"${message:-no message}\" (no dependency graph for this repository, or a commit it does not know)" ;;
  *)   record_error "HTTP $code, \"${message:-no message}\"" ;;
esac

# The endpoint documents no pagination; a next page would mean the
# first one is not the whole diff, and a partial diff is not read.
if grep -qiE '^link:.*rel="next"' "$hdr"; then
  record_error "the answer is paginated, which this step does not follow, so the diff it holds is partial"
fi
# An incomplete head snapshot can list a package as removed that is
# still there, and that reads as a fix.
warnings=$(grep -i -m1 '^x-github-dependency-graph-snapshot-warnings:' "$hdr" \
  | sed 's/^[^:]*: *//' | tr -d '\r') || warnings=""
if [ -n "$warnings" ]; then
  decoded=$(printf '%s' "$warnings" | base64 -d 2>/dev/null) || decoded="$warnings"
  record_error "GitHub reported the dependency snapshot incomplete: $(printf '%s' "$decoded" | tr '\n' ' ')"
fi

# An added version whose `vulnerabilities` is absent would read as
# carrying none, so every field the match reads is asserted present.
jq -e 'type == "array" and all(.[];
    (.change_type | type) == "string" and (.manifest | type) == "string"
    and (.name | type) == "string" and (.vulnerabilities | type) == "array"
    and all(.vulnerabilities[]; (.advisory_ghsa_id | type) == "string"))' "$body" >/dev/null 2>&1 \
  || record_error "HTTP 200, but the answer is not an array of changes that each carry a manifest, a name and a vulnerabilities list"

jq '{changes: .}' "$body" >"$COMPARE_JSON" || record_error "the answer could not be recorded"
echo "read $(jq '.changes | length' "$COMPARE_JSON") dependency change(s) between $BASE_SHA and $HEAD_SHA"
