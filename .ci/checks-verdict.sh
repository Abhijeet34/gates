#!/usr/bin/env bash
# Decides `checks`, the one status the branch ruleset requires. Reads the
# `needs` context of ci.yml's aggregate job as JSON on stdin.
#
# The rule is not "success or skipped", because a skipped job satisfies a
# required status check. Measured on 2026-08-03: with every job forced to
# skip, a pull request whose `python` suite had genuinely failed went from
# mergeStateStatus BLOCKED to CLEAN. So any future `if:` that skips a suite
# would hand main a green verdict over work that never ran, which is the
# failure this file exists to make impossible.
#
# No job is conditional any more - the macOS `blurt` job left with the desktop
# app it built - so the rule is now simply that every job succeeded, and no
# skip is legitimate. Adding a job with an `if:` means adding it to the
# `conditional` allowance below in the same commit, or it reports green having
# never run. `.ci/test-checks-verdict.sh` pins every case.

set -euo pipefail

results=$(cat)

printf '%s\n' "$results" | jq -r 'to_entries[] | "\(.key): \(.value.result)"'

# An empty object would pass `all` vacuously, so it is rejected first: no
# dependencies reported means the aggregate gated nothing.
if ! printf '%s\n' "$results" | jq -e '
        # Empty today: every job `checks` gates runs on every pull request. The
        # hook stays so a future conditional job is named here rather than
        # loosening the rule back to "success or skipped".
        def conditional: false;
        (to_entries | length) > 0
        and all(to_entries[];
            .value.result == "success"
            or (.value.result == "skipped" and (.key | conditional)))
    ' >/dev/null; then
    printf '\nchecks failed: a gate did not run, or did not pass\n' >&2
    exit 1
fi

printf '\nevery gate ran and passed\n'
