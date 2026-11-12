#!/usr/bin/env bash
# The verifier's classify job: judge one head with this branch's copy of the
# judge and hand the verdict to `post` as job outputs, never as a file.
#
# Reads PR, HEAD_SHA and BASE_SHA from the dispatch through env:, and refuses
# any that is not digits or 40 hex before it is used. The head and base are
# fetched as objects into a bare repository, so no file of the head is checked
# out, and the judge reads them with git; nothing of the head runs.
# Always exits 0 once it has written `verdict`: success, failure or undecided.

set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# The check run's summary holds at most 65535 characters.
LIMIT=60000

emit() { # <verdict> <text>
    local text=$2
    [ "${#text}" -le "$LIMIT" ] || text="${text:0:$LIMIT}
(cut at $LIMIT characters)"
    # One base64 line, so no text from the pull request can shape the outputs file.
    printf 'verdict=%s\nsummary=%s\n' "$1" "$(printf '%s' "$text" | base64 | tr -d '\n')" >>"$GITHUB_OUTPUT"
    printf '%s: %s\n' "$1" "$text"
    exit 0
}

[ "${GITHUB_REF:-}" = refs/heads/verifier ] \
    || emit undecided "owner-verify: refused: this run is at ${GITHUB_REF:-no ref}, not refs/heads/verifier"
[[ ${PR:-} =~ ^[0-9]+$ && ${HEAD_SHA:-} =~ ^[0-9a-f]{40}$ && ${BASE_SHA:-} =~ ^[0-9a-f]{40}$ ]] \
    || emit undecided "owner-verify: could not decide: the dispatch's pr must be digits and its head and base 40 hex"

tree=$(mktemp -d)
git init -q --bare "$tree"
auth=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')
if ! GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.extraheader GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $auth" \
    git -C "$tree" fetch -q --no-tags "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY" "$BASE_SHA" "$HEAD_SHA" 2>"$tree/fetch.err"; then
    emit undecided "owner-verify: could not decide: fetching the head and base failed: $(head -c 2000 "$tree/fetch.err")"
fi

rc=0
out=$(python3 "$here/gates/run/owner-verify.py" --repo "$GITHUB_REPOSITORY" --pr "$PR" \
    --head "$HEAD_SHA" --base "$BASE_SHA" --tree "$tree" 2>&1) || rc=$?
case $rc in
    0) emit success "$out" ;;
    1) emit failure "$out" ;;
    *) emit undecided "$out" ;;
esac
