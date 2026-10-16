#!/usr/bin/env bash
# Resolve the gates commit a caller runs, prove it may run it, and check the
# gates checkout out at that commit.
#
#   GATES_DIR=<gates clone> GATES_REPOSITORY=o/r BOOTSTRAP_SHA=<sha> run/lock.sh
#
# Runs from the gates checkout at the bootstrap's own commit (job.workflow_sha),
# with the caller's checkout as the working directory, before any locked content
# runs. README.md, "Pinning", owns the design; each refusal below is one of its
# controls and has a red fixture in .ci/gate-registry.toml.
set -uo pipefail

LOCK=.github/gates.lock
# The one app whose `checks` run counts: GitHub Actions itself.
ACTIONS_APP_ID=15368
# What a lock bump may touch: the lock and the copies .ci/gitleaks/sync.sh writes.
BUMP_PATHS=".github/gates.lock .gitleaks.toml .githooks/pre-push .githooks/commit-msg"
MAIN=refs/gates-lock/main

fail() {
    echo "::error::$1 Refusing to run gates." >&2
    exit 1
}

: "${GATES_DIR:?GATES_DIR is unset}"
[ -n "${GATES_REPOSITORY:-}" ] && [ -n "${BOOTSTRAP_SHA:-}" ] \
    || fail "GATES_REPOSITORY or BOOTSTRAP_SHA is empty, so the gates source cannot be named."
g() { git -C "$GATES_DIR" "$@"; }

# A lock is one 40-hex commit; `#` starts a comment. Prints it, or nothing when
# there is no lock, and refuses anything else rather than guessing.
parse_lock() { # <label> < content
    local body
    body=$(sed 's/#.*//' | tr -d ' \t\r' | grep -v '^$' || true)
    [ -z "$body" ] && return 0
    [[ $body =~ ^[0-9a-f]{40}$ ]] \
        || fail "$1 is not a single 40-hex gates commit: $(printf '%s' "$body" | head -c 120)"
    printf '%s\n' "$body"
}

head_lock=""
if [ -e "$LOCK" ]; then head_lock=$(parse_lock "$LOCK" <"$LOCK") || exit 1; fi
target=${head_lock:-$BOOTSTRAP_SHA}

fetched=0
fetch_main() {
    [ "$fetched" = 1 ] && return
    # A shallow history answers is-ancestor "no" for a commit past its edge.
    [ "$(g rev-parse --is-shallow-repository)" = false ] \
        || fail "the gates checkout is shallow, so ancestry on main cannot be decided."
    # main by name only. Fetching the lock's sha itself would be served from
    # the whole fork network, which is how an imposter commit gets in.
    g fetch --quiet --no-tags origin "+refs/heads/main:$MAIN" \
        || fail "could not fetch gates main from $GATES_REPOSITORY."
    fetched=1
}

on_main() { # <sha>
    g cat-file -e "$1^{commit}" 2>/dev/null && g merge-base --is-ancestor "$1" "$MAIN"
}

# Every latest `checks` run GitHub Actions posted on the commit, and at least one.
green() { # <sha>
    local runs
    runs=$(gh api "repos/$GATES_REPOSITORY/commits/$1/check-runs?check_name=checks&app_id=$ACTIONS_APP_ID&filter=latest") \
        || fail "could not read the check runs of gates $1."
    printf '%s' "$runs" | jq -e '[.check_runs[] | select(.app.id == '"$ACTIONS_APP_ID"' and .name == "checks")]
        | length > 0 and all(.status == "completed" and .conclusion == "success")' >/dev/null
}

if [ -n "$head_lock" ]; then
    fetch_main
    on_main "$head_lock" \
        || fail "$LOCK names $head_lock, which is not on gates main (a fork-only or unmerged commit)."
    green "$head_lock" \
        || fail "$LOCK names $head_lock, which has no green checks run from GitHub Actions (app $ACTIONS_APP_ID)."
fi

# A pull request may change the lock only as a bump: forward on main, nothing
# but the lock and the synced copies, opened by the bump identity.
if [ "${EVENT_NAME:-}" = pull_request ]; then
    : "${BASE_SHA:?BASE_SHA is unset on a pull_request event}"
    git cat-file -e "$BASE_SHA^{commit}" 2>/dev/null \
        || git fetch --quiet --no-tags --depth=1 origin "$BASE_SHA" \
        || fail "could not fetch the pull request's base $BASE_SHA."
    base_raw=$(git show "$BASE_SHA:$LOCK" 2>/dev/null || true)
    head_raw=$(cat "$LOCK" 2>/dev/null || true)
    if [ "$base_raw" != "$head_raw" ]; then
        fetch_main
        base_lock=$(parse_lock "the base's $LOCK" <<<"$base_raw") || exit 1
        from=${base_lock:-$BOOTSTRAP_SHA}

        extra=$(git diff --no-renames --name-only "$BASE_SHA" HEAD | grep -vxF -f <(tr ' ' '\n' <<<"$BUMP_PATHS") || true)
        [ -z "$extra" ] \
            || fail "this pull request changes $LOCK together with other content: $(paste -sd" " - <<<"$extra")."

        # Who may bump is gates main's policy, never the pull request's own.
        identity=$(g show "$MAIN:run/bump-identity" 2>/dev/null | sed 's/#.*//' | grep -v '^[[:space:]]*$' || true)
        [ -n "$identity" ] \
            || fail "this pull request changes $LOCK, and gates main configures no bump identity."
        grep -qxF "${PR_AUTHOR:-} ${PR_AUTHOR_ID:-}" <<<"$identity" \
            || fail "this pull request changes $LOCK, and its author ${PR_AUTHOR:-<unset>} (${PR_AUTHOR_ID:-<unset>}) is not the bump identity."

        g merge-base --is-ancestor "$from" "$target" 2>/dev/null \
            || fail "this pull request moves $LOCK from $from to $target, which does not descend from it (a downgrade)."
    fi
fi

echo "gates ${head_lock:+locked }at $target"
# Last, and the file git replaces is a new inode, so bash keeps reading this one.
g checkout --quiet --detach "$target" || fail "could not check gates out at $target."
exit 0
