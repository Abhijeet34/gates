#!/usr/bin/env bash
# Refuse a pull request that was not raised through the no-mistakes pipeline.
set -eu
# These are not our strings to choose: each is the literal a
# no-mistakes build writes into a PR body, and the URL in it is the
# repository that build came from. Our mirror build writes the first
# (measured 2026-09-17); an upstream build writes the second. Both are
# signatures, so both match - dropping either reds every pull request
# raised by that build in every caller.
marker_mirror='Updates from [git push no-mistakes](https://github.com/Abhijeet34/no-mistakes)'
marker_upstream='Updates from [git push no-mistakes](https://github.com/kunchenguid/no-mistakes)'

# no-mistakes creates the pull request and only then writes the
# `## Pipeline` section, so the event payload's body can describe a
# pull request that no longer exists - measured on a caller's pull
# request, where the `edited` payload was stale too, not just
# `opened`. Read the body live, and retry so a write landing seconds
# after creation is still seen; a write that takes longer than this
# arrives with its own `edited` event, which this same live read then
# passes.
attempts=6
delay=10
read_ok=0

for attempt in $(seq 1 "$attempts"); do
  if body=$(gh api "repos/${PR_REPO}/pulls/${PR_NUMBER}" --jq '.body // ""'); then
    read_ok=1
    if printf '%s' "$body" | grep -qF -e "$marker_mirror" -e "$marker_upstream"; then
      echo "Found no-mistakes signature in PR #${PR_NUMBER} body (attempt ${attempt})."
      exit 0
    fi
    echo "No signature in PR #${PR_NUMBER} body yet (attempt ${attempt}/${attempts})."
  else
    echo "Could not read PR #${PR_NUMBER} body (attempt ${attempt}/${attempts})." >&2
  fi
  if [ "$attempt" -lt "$attempts" ]; then
    sleep "$delay"
  fi
done

# Never read the body at all: report that, and do not pass. "Could
# not determine" is not "compliant".
if [ "$read_ok" -eq 0 ]; then
  {
    echo "::error::Could not establish whether this PR was raised through no-mistakes."
    echo
    echo "All ${attempts} attempts to read the body of PR #${PR_NUMBER} in ${PR_REPO} failed."
    echo "This is not a verdict on the PR - the check could not reach the API."
    echo "Usual causes: the caller granted no 'pull-requests: read' permission,"
    echo "a rate limit, or a GitHub outage. Re-run this check once it is resolved."
    echo
    echo "This check fails closed: an unreadable body never passes."
  } >&2
  exit 1
fi

{
  echo "::error::This PR was not raised through no-mistakes."
  echo
  echo "Contributions to this repository must be submitted via 'git push no-mistakes'."
  echo "That pipeline runs the required ${PIPELINE_STEPS} steps and writes a"
  echo "deterministic '## Pipeline' section into the PR body containing:"
  echo
  echo "    $marker_mirror"
  echo
  echo "See CONTRIBUTING.md for setup and the full workflow."
  echo
  echo "PR author: ${PR_AUTHOR}"
} >&2
exit 1
