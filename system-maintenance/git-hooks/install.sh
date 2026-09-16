#!/usr/bin/env bash
# Deploy the machine-wide git hooks, or report drift.
#
#   system-maintenance/git-hooks/install.sh deploy   # repo -> ~/.git-hooks
#   system-maintenance/git-hooks/install.sh check    # report drift, exit 1 if any
#
# ONE WAY ONLY, and that is the point. ~/.git-hooks holds scripts git executes on
# every commit, checkout and push, so it is a code-execution surface with the
# same standing as a directory on $PATH. Deploy pushes reviewed, committed
# content out to it; nothing ever pulls live content back in, because a
# sync-back would let anything that can write that directory launder itself into
# the repository as "the current config". When `check` reports drift it prints
# the exact `cp` to run, so adopting a change stays a deliberate act that lands
# in a diff.
#
# Deploying is separate from merging on purpose: a bug in the machine-wide
# pre-push blocks every push on this machine, this crew's included, so arming it
# is a decision and not a side effect of a pull.
#
# $HOME is honoured, so the whole thing can be exercised against a throwaway
# tree - see test-global-hooks.sh.
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P) || exit 2
SRC="$ROOT/system-maintenance/git-hooks"
DEST="${GIT_HOOKS_DIR:-$HOME/.git-hooks}"

# "<source>:<name in ~/.git-hooks>". The last five are not hooks git ever runs -
# git only invokes files whose name is exactly a hook name - they are the two
# scanners the machine-wide pre-push chains and their rule files, deployed here
# so it never has to reach into the repository it is scanning to find them.
PAYLOAD=(
    "$SRC/pre-push:pre-push"
    "$SRC/check-orphan-dirs:check-orphan-dirs"
    "$SRC/commit-msg:commit-msg"
    "$SRC/post-checkout:post-checkout"
    "$SRC/post-commit:post-commit"
    "$SRC/post-merge:post-merge"
    "$ROOT/.githooks/pre-push:gitleaks-pre-push"
    "$ROOT/.gitleaks.toml:gitleaks.toml"
    "$SRC/watermark-scan.py:watermark-scan.py"
    "$SRC/watermark-allow.conf:watermark-allow.conf"
    "$SRC/watermark-optout.conf:watermark-optout.conf"
)

# Everything but the three rule files is executed by git or by a hook.
executable() { case "$1" in gitleaks.toml | watermark-allow.conf | watermark-optout.conf) return 1 ;; *) return 0 ;; esac; }

digest() { shasum -a 256 "$1" | cut -d' ' -f1; }

cmd_deploy() {
    mkdir -p "$DEST" || return 1
    local rc=0 pair src name
    for pair in "${PAYLOAD[@]}"; do
        src="${pair%:*}" name="${pair##*:}"
        if [ ! -r "$src" ]; then
            echo "install: cannot read $src" >&2
            rc=1
            continue
        fi
        # Write beside the target and rename, rather than copying over it. `cp`
        # truncates in place, and a shell reads a script by offset as it runs, so
        # a deploy landing while another agent's push is mid-hook would feed that
        # push half a file. rename(2) within one directory is atomic, and setting
        # the mode before the rename means the file is never briefly unexecutable.
        if ! cp "$src" "$DEST/.$name.incoming"; then
            /bin/rm -f "$DEST/.$name.incoming"
            rc=1
            continue
        fi
        executable "$name" && chmod +x "$DEST/.$name.incoming"
        mv -f "$DEST/.$name.incoming" "$DEST/$name" || { rc=1; continue; }
        echo "deployed $DEST/$name"
    done
    # The hooks are inert until git is told to look here. Global config, so one
    # setting covers every repository that has not overridden it - including
    # every clone that does not exist yet, which is the whole point.
    git config --global core.hooksPath "$DEST" || rc=1
    return $rc
}

cmd_check() {
    local rc=0 pair src name dst
    for pair in "${PAYLOAD[@]}"; do
        src="${pair%:*}" name="${pair##*:}"
        dst="$DEST/$name"
        if [ ! -r "$dst" ]; then
            echo "DRIFT $dst: missing"
            rc=1
        elif [ "$(digest "$src")" != "$(digest "$dst")" ]; then
            echo "DRIFT $dst: differs from $src"
            echo "      repo wins:  cp '$src' '$dst'"
            echo "      live wins:  cp '$dst' '$src'   # then commit it"
            rc=1
        elif executable "$name" && [ ! -x "$dst" ]; then
            # git skips a hook that is not executable, silently. Identical
            # content with the wrong mode is a gate that does not run.
            echo "DRIFT $dst: not executable, so it will not run"
            rc=1
        fi
    done
    local live
    live=$(git config --global --get core.hooksPath 2>/dev/null)
    # shellcheck disable=SC2088  # matching a literal leading "~/" from git config output, not shell tilde expansion
    case "$live" in '~/'*) live="$HOME/${live#\~/}" ;; esac
    if [ "$live" != "$DEST" ]; then
        echo "DRIFT global core.hooksPath is '${live:-<unset>}', not '$DEST' - these hooks are inert"
        rc=1
    fi
    if [ "$rc" = 0 ]; then
        echo "ok $DEST matches the repository"
    else
        echo "run '$0 deploy' to make the machine match the repository"
    fi
    return $rc
}

case "${1:-}" in
    deploy) cmd_deploy ;;
    check) cmd_check ;;
    *) echo "usage: $0 {deploy|check}" >&2; exit 2 ;;
esac
