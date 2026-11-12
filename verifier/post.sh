#!/usr/bin/env bash
# The verifier's post job, the only step that holds the App key: post the
# classify job's verdict as a check run on the dispatched head.
#
# Env: HEAD_SHA, VERDICT, SUMMARY (base64), CHECK_NAME, APP_ID, APP_KEY, and
# GH_TOKEN, the run's own token, which only reads this branch's tip.
# It posts nothing from a re-run or from a run whose kernel is no longer the
# branch's tip, so an old judge cannot be brought back (invariant 3). A missing
# or malformed verdict is posted as a failure. Neither the key, the JWT nor the
# installation token is ever in an argument list or the log.

set -euo pipefail

API=${GITHUB_API_URL:-https://api.github.com}
die() { printf 'post: refused: %s\n' "$1" >&2; exit 1; }

[ "${GITHUB_REF:-}" = refs/heads/verifier ] || die "this run is at ${GITHUB_REF:-no ref}, not refs/heads/verifier"
[ "${GITHUB_RUN_ATTEMPT:-}" = 1 ] \
    || die "this is attempt ${GITHUB_RUN_ATTEMPT:-unknown} of the run; a re-run keeps its old kernel, so only a new dispatch posts"
tip=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/verifier" --jq .object.sha) \
    || die "could not read the tip of refs/heads/verifier"
[ "$tip" = "$GITHUB_SHA" ] || die "this run's kernel is ${GITHUB_SHA:0:12}, but refs/heads/verifier is now ${tip:0:12}"
[[ ${HEAD_SHA:-} =~ ^[0-9a-f]{40}$ ]] || die "the head is not 40 hex"
[[ ${APP_ID:-} =~ ^[0-9]+$ ]] || die "the verify environment's VERIFIER_APP_ID is not set to the App's id"
[ -n "${APP_KEY:-}" ] || die "the verify environment's VERIFIER_APP_KEY is empty"
case ${CHECK_NAME:-} in
    'owner-verify' | 'owner-verify (shadow)') ;;
    *) die "VERIFIER_CHECK_NAME must be 'owner-verify' or 'owner-verify (shadow)'" ;;
esac

case ${VERDICT:-} in
    success) conclusion=success title="passes" ;;
    failure) conclusion=failure title="refused" ;;
    undecided) conclusion=failure title="could not decide" ;;
    *) conclusion=failure title="could not decide" SUMMARY="" ;;
esac
summary=$(printf '%s' "${SUMMARY:-}" | base64 -d 2>/dev/null) || summary=""
[ -n "$summary" ] || summary="owner-verify: could not decide: the classify job left no verdict"

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }
now=$(date +%s)
header=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
payload=$(printf '{"iat":%d,"exp":%d,"iss":%d}' $((now - 60)) $((now + 540)) "$APP_ID" | b64url)
signature=$(printf '%s.%s' "$header" "$payload" | openssl dgst -sha256 -sign <(printf '%s\n' "$APP_KEY") | b64url)
jwt="$header.$payload.$signature"
unset APP_KEY

call() { # <bearer> <method> <path> [json body on stdin]
    curl -fsS -X "$2" -H @<(printf 'Authorization: Bearer %s\nAccept: application/vnd.github+json\nX-GitHub-Api-Version: 2022-11-28\n' "$1") \
        --data-binary @- "$API/$3"
}
field() { python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }

installation=$(call "$jwt" GET "repos/$GITHUB_REPOSITORY/installation" </dev/null | field id) \
    || die "the verifier App is not installed on $GITHUB_REPOSITORY, or the key does not match VERIFIER_APP_ID"
token=$(printf '{"repositories":["%s"],"permissions":{"checks":"write"}}' "${GITHUB_REPOSITORY#*/}" \
    | call "$jwt" POST "app/installations/$installation/access_tokens" | field token) \
    || die "could not mint a checks-only token for $GITHUB_REPOSITORY"
echo "::add-mask::$token"
# Best effort: the token lapses within the hour if the revoke fails.
trap 'call "$token" DELETE installation/token </dev/null >/dev/null 2>&1 || true' EXIT

NAME=$CHECK_NAME CONCLUSION=$conclusion TITLE="$title" SUMMARY_TEXT="$summary" python3 -c '
import json, os
print(json.dumps({
    "name": os.environ["NAME"],
    "head_sha": os.environ["HEAD_SHA"],
    "status": "completed",
    "conclusion": os.environ["CONCLUSION"],
    "output": {"title": os.environ["TITLE"], "summary": os.environ["SUMMARY_TEXT"]},
}))' | call "$token" POST "repos/$GITHUB_REPOSITORY/check-runs" | field html_url
printf 'post: %s %s on %s\n' "$CHECK_NAME" "$conclusion" "$HEAD_SHA"
