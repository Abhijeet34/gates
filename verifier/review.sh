#!/usr/bin/env bash
# The verifier's review job: seat the review over the head's safeguard paths with
# this branch's copy of run/seats.py, and hand its verdict to `classify` as job
# outputs, never as a file.
#
# Reads PR, HEAD_SHA and BASE_SHA from the dispatch through env:, GH_TOKEN (the
# run's own token, which reads the pull request and the commits) and
# CLAUDE_CODE_OAUTH_TOKEN, the `review` environment's secret, which only the
# seats' `claude` processes receive. The head and base are fetched as objects
# into a bare repository; nothing of the head runs. The CLI is the pinned
# platform tarball, checked against its sha512 before it is unpacked, so no
# install script runs. Always exits 0 once it has written its outputs:
# `verdict` holds the base64 of {"head", "blocking"} only on a decided review.

set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CLI_VERSION=2.1.323
CLI_URL=https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-$CLI_VERSION.tgz
CLI_SHA512=cUAH9W7KrcfM5v7hWjvv1rurvAAgvOe+BVfOlk17sXH3Vqh9V+tVj0WZin6RiJ8MiZ7RpEtVfaVKPcgrY3x6Vw==
LIMIT=30000

emit() { # <verdict file or empty> <text>
    local text=$2 verdict=""
    [ "${#text}" -le "$LIMIT" ] || text="${text:0:$LIMIT}
(cut at $LIMIT characters)"
    [ -z "$1" ] || verdict=$(base64 <"$1" | tr -d '\n')
    # One base64 line each, so no text from the pull request can shape the outputs file.
    printf 'verdict=%s\nsummary=%s\n' "$verdict" "$(printf '%s' "$text" | base64 | tr -d '\n')" >>"$GITHUB_OUTPUT"
    printf '%s\n' "$text"
    exit 0
}

[ "${GITHUB_REF:-}" = refs/heads/verifier ] \
    || emit "" "seats: refused: this run is at ${GITHUB_REF:-no ref}, not refs/heads/verifier"
[[ ${PR:-} =~ ^[0-9]+$ && ${HEAD_SHA:-} =~ ^[0-9a-f]{40}$ && ${BASE_SHA:-} =~ ^[0-9a-f]{40}$ ]] \
    || emit "" "seats: could not decide: the dispatch's pr must be digits and its head and base 40 hex"

work=$(mktemp -d)
tree=$work/tree
git init -q --bare "$tree"
auth=$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')
if ! GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.extraheader GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $auth" \
    git -C "$tree" fetch -q --no-tags "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY" "$BASE_SHA" "$HEAD_SHA" 2>"$work/fetch.err"; then
    emit "" "seats: could not decide: fetching the head and base failed: $(head -c 2000 "$work/fetch.err")"
fi

seats=(python3 "$here/gates/run/seats.py" --repo "$GITHUB_REPOSITORY" --pr "$PR"
    --head "$HEAD_SHA" --base "$BASE_SHA" --tree "$tree")
# A head with no safeguard path, a fork's head, or an unenrolled base sits no seat
# and installs nothing.
rc=0
out=$("${seats[@]}" --plan 2>&1) || rc=$?
[ "$rc" = 0 ] || emit "" "$out"

if ! curl -fsSL --retry 3 -o "$work/cli.tgz" "$CLI_URL" 2>"$work/curl.err"; then
    emit "" "seats: could not decide: downloading the CLI failed: $(head -c 2000 "$work/curl.err")"
fi
got=$(openssl dgst -sha512 -binary "$work/cli.tgz" | base64 | tr -d '\n')
[ "$got" = "$CLI_SHA512" ] \
    || emit "" "seats: could not decide: the CLI tarball's sha512 is $got, not the pinned $CLI_SHA512"
mkdir "$work/bin"
tar -xzf "$work/cli.tgz" -C "$work/bin" --strip-components=1 package/claude \
    || emit "" "seats: could not decide: the CLI tarball holds no package/claude"
[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] \
    || emit "" "seats: could not decide: the review environment's CLAUDE_CODE_OAUTH_TOKEN is empty"

rc=0
out=$(PATH="$work/bin:$PATH" "${seats[@]}" --out "$work/verdict.json" --store "$work/store" 2>&1) || rc=$?
case $rc in
    0 | 1) emit "$work/verdict.json" "$out" ;;
    *) emit "" "$out" ;;
esac
