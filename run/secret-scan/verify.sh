#!/usr/bin/env bash
# The caller carries gates' synced copies, unchanged and executable.
# Compared byte for byte with gates as checked out for this run, never with a
# digest in the workflow: the workflow is the bootstrap's commit, and the lock
# may name a later one whose copies differ.
set -uo pipefail
: "${GATES_DIR:?GATES_DIR is unset}"
at=$(git -C "$GATES_DIR" rev-parse --short HEAD 2>/dev/null || echo "$GATES_DIR")
rc=0
canonical() { # caller-path gates-path
  if [ ! -r "$1" ]; then
    echo "::error::$1 is missing, so this repository is not carrying the canonical gate. Re-run gates' .ci/gitleaks/sync.sh." >&2
    rc=1
  elif [ ! -r "$GATES_DIR/$2" ]; then
    echo "::error::gates has no $2 at $at, so $1 cannot be compared. COULD NOT ESTABLISH that this repository carries the canonical gate." >&2
    rc=1
  elif ! cmp -s "$1" "$GATES_DIR/$2"; then
    echo "::error::$1 has DRIFTED from gates' canonical copy ($2 at $at). Re-run gates' .ci/gitleaks/sync.sh." >&2
    rc=1
  fi
}
canonical .gitleaks.toml .gitleaks.toml
canonical .githooks/pre-push .githooks/pre-push
canonical .githooks/commit-msg system-maintenance/git-hooks/commit-msg

# Content alone is not the gate. git skips a hook that is not
# executable and says nothing about it, so a byte-perfect hook
# committed 100644 never runs, which reads exactly like a hook that
# found nothing. The mode is tracked, so CI can see this one; the other
# inertness - core.hooksPath unset in the clone - is repository config
# that no checkout carries, and only gates' `sync.sh --check`
# reaches it.
if [ -r .githooks/pre-push ] && [ ! -x .githooks/pre-push ]; then
  echo "::error::.githooks/pre-push is not executable, so git will never run it. chmod +x and commit the mode." >&2
  rc=1
fi
if [ -r .githooks/commit-msg ] && [ ! -x .githooks/commit-msg ]; then
  echo "::error::.githooks/commit-msg is not executable, so git will never run it. chmod +x and commit the mode." >&2
  rc=1
fi
exit $rc
