#!/usr/bin/env bash
# Push the canonical secret-scan config and pre-push hook out to a sibling
# repository, or report drift.
#
#   .ci/gitleaks/sync.sh <repo> [<repo> ...]           install or update
#   .ci/gitleaks/sync.sh --check <repo> [<repo> ...]   report drift, exit 1 if any
#   .ci/gitleaks/sync.sh --digest [config|hook]        print a canonical digest
#
# `--digest` is what `.github/workflows/shared-secret-scan.yml` pins, once per
# synced file: a caller's CI compares against those pins rather than cloning
# the canonical, so the digests travel inside the shared workflow, and a
# repository whose copy has drifted fails there. Update both in the same commit;
# .ci/test-secret-scan.sh fails if either pin goes stale.
#
# `--check` also answers what CI structurally cannot: whether core.hooksPath
# points at the hook. That is repository config rather than tracked content, so
# no commit carries it and no checkout has it, and without it a byte-perfect,
# correctly-moded hook is completely inert - which is what it was in five of six
# clones until 2026-08-14. The executable bit is asked in both places, of
# different things: CI of the committed mode, this of the clone's own.
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P) || exit 2
CONFIG="$ROOT/.gitleaks.toml"
HOOK="$ROOT/.githooks/pre-push"

digest() { shasum -a 256 "$1" | cut -d' ' -f1; }

[ -r "$CONFIG" ] || { echo "sync: cannot read $CONFIG" >&2; exit 2; }
[ -x "$HOOK" ] || { echo "sync: $HOOK is missing or not executable" >&2; exit 2; }

if [ "${1:-}" = "--digest" ]; then
    case "${2:-config}" in
        config) digest "$CONFIG" ;;
        hook)   digest "$HOOK" ;;
        *) echo "sync: --digest takes 'config' or 'hook', not '$2'" >&2; exit 2 ;;
    esac
    exit 0
fi

CHECK=0
[ "${1:-}" = "--check" ] && { CHECK=1; shift; }
[ "$#" -gt 0 ] || { echo "sync: no target repositories given" >&2; exit 2; }

rc=0
for target in "$@"; do
    # `.git` is a directory in an ordinary checkout and a POINTER FILE in a
    # linked worktree, so the `-d` test this replaces refused every worktree -
    # and every fleet task runs in one, which left the documented one-command
    # re-sync unrunnable exactly where it is needed. An empty `--show-prefix`
    # accepts a worktree and still refuses a subdirectory, where the two synced
    # files would sit unread; the plain `-e "$target/.git"` used elsewhere in
    # this repo would take both. It is empty in a BARE repository too - measured
    # on git 2.55, it prints nothing and exits 0 there - so the second question
    # is asked rather than assumed: a bare repo has no working tree to sync into.
    prefix=$(git -C "$target" rev-parse --show-prefix 2>/dev/null) || prefix=notrepo
    bare=$(git -C "$target" rev-parse --is-bare-repository 2>/dev/null) || bare=true
    if [ -n "$prefix" ] || [ "$bare" != false ]; then
        echo "sync: $target is not the top level of a git working tree" >&2
        rc=1
        continue
    fi
    # The canonical copy is this repository's own; syncing it onto itself is a no-op
    # that would only ever report a false drift.
    [ "$(cd "$target" && pwd -P)" = "$ROOT" ] && continue

    for pair in "$CONFIG:.gitleaks.toml" "$HOOK:.githooks/pre-push"; do
        src="${pair%%:*}" rel="${pair#*:}"
        dst="$target/$rel"
        if [ "$CHECK" = 1 ]; then
            if [ ! -r "$dst" ]; then
                echo "DRIFT $target/$rel: missing"
                rc=1
            elif [ "$(digest "$src")" != "$(digest "$dst")" ]; then
                echo "DRIFT $target/$rel: differs from canonical"
                rc=1
            elif [ "$rel" = ".githooks/pre-push" ] && [ ! -x "$dst" ]; then
                # git skips a hook that is not executable, silently. Identical
                # content with the wrong mode is a gate that does not run.
                echo "DRIFT $target/$rel: not executable, so git will not run it"
                rc=1
            fi
        else
            mkdir -p "$(dirname "$dst")"
            cp "$src" "$dst"
            [ "$rel" = ".githooks/pre-push" ] && chmod +x "$dst"
            echo "synced $target/$rel"
        fi
    done

    # core.hooksPath is repository config, not a tracked file, so a fresh clone
    # has the hook on disk but inert until this runs.
    if [ "$CHECK" = 1 ]; then
        if [ "$(git -C "$target" config --get core.hooksPath 2>/dev/null)" != ".githooks" ]; then
            echo "DRIFT $target: core.hooksPath is not .githooks - the pre-push gate is inert here"
            rc=1
        fi
        # Name what was actually examined. A --check that reached no repository
        # is otherwise indistinguishable from one that found no drift, which is
        # the failure shape this whole gate exists to refuse.
        echo "checked $target"
    else
        git -C "$target" config core.hooksPath .githooks
    fi
done

exit $rc
