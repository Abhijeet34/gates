#!/usr/bin/env bash
# Move the verifier's kernel to one gates commit, as one new commit on `verifier`.
#
#   activate.sh --init <gates commit>   in an empty directory: the branch's first commit
#   activate.sh <gates commit>          in a clean checkout of `verifier`: the next one
#
# The maintainer runs the branch's own copy; the first time, the copy at the
# reviewed gates commit. It refuses a commit that gates main, fetched by name,
# does not contain, or whose latest `checks` run from GitHub Actions is not
# green, as run/lock.sh does; then writes exactly the files kernel.txt at that
# commit names, plus gates/COMMIT. A rollback is the same command with the
# earlier commit, so the branch only ever moves forward. It pushes nothing.

set -euo pipefail

GATES_URL=${GATES_URL:-https://github.com/Abhijeet34/gates.git}
GATES_REPOSITORY=${GATES_REPOSITORY:-Abhijeet34/gates}
ACTIONS_APP_ID=15368
fail() { printf 'activate: refused: %s\n' "$1" >&2; exit 1; }

init=0
if [ "${1:-}" = --init ]; then
    init=1
    shift
fi
commit=${1:-}
[[ $commit =~ ^[0-9a-f]{40}$ ]] || fail "usage: activate.sh [--init] <40-hex gates commit>"

if ((init)); then
    [ -z "$(ls -A)" ] || fail "--init writes the branch's first commit, so it runs in an empty directory"
else
    [ "$(git symbolic-ref --short HEAD 2>/dev/null)" = verifier ] || fail "run it in a checkout of the verifier branch"
    [ -z "$(git status --porcelain)" ] || fail "the checkout has uncommitted changes"
fi

gates=$(mktemp -d)
# Best effort: the directory is a fetched copy of public gates.
trap 'rm -rf "$gates"' EXIT
git init -q --bare "$gates"
# main by name only: GitHub serves any commit of the fork network by its sha.
git -C "$gates" fetch -q --no-tags "$GATES_URL" "+refs/heads/main:refs/heads/main" \
    || fail "could not fetch gates main from $GATES_URL"
{ git -C "$gates" cat-file -e "$commit^{commit}" 2>/dev/null \
    && git -C "$gates" merge-base --is-ancestor "$commit" refs/heads/main; } \
    || fail "$commit is not on gates main"
runs=$(gh api "repos/$GATES_REPOSITORY/commits/$commit/check-runs?check_name=checks&app_id=$ACTIONS_APP_ID&filter=latest") \
    || fail "could not read the check runs of gates $commit"
printf '%s' "$runs" | jq -e '[.check_runs[] | select(.app.id == '"$ACTIONS_APP_ID"' and .name == "checks")]
    | length > 0 and all(.status == "completed" and .conclusion == "success")' >/dev/null \
    || fail "$commit has no green checks run from GitHub Actions; for a merge commit, dispatch one: gh workflow run ci.yml --ref main -R $GATES_REPOSITORY"
manifest=$(git -C "$gates" show "$commit:verifier/kernel.txt") || fail "$commit has no verifier/kernel.txt"

if ((init)); then
    git init -q -b verifier
else
    git rm -rq --ignore-unmatch .
fi
while read -r src dest rest; do
    [ -n "$src" ] && [ "${src:0:1}" != "#" ] || continue
    [[ -z $rest && $dest =~ ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$ && $dest != *..* ]] \
        || fail "kernel.txt at $commit names a destination that is not a plain relative path: $dest"
    mode=$(git -C "$gates" ls-tree "$commit" -- "$src" | cut -d' ' -f1)
    [ -n "$mode" ] || fail "kernel.txt at $commit names $src, which is not in that commit"
    mkdir -p "$(dirname "$dest")"
    git -C "$gates" show "$commit:$src" >"$dest"
    if [ "$mode" = 100755 ]; then chmod 755 "$dest"; else chmod 644 "$dest"; fi
done <<<"$manifest"
mkdir -p gates
printf '%s\n' "$commit" >gates/COMMIT
git add -A
git -c core.pager=cat diff --cached --stat
git commit -qm "verifier: the kernel at gates ${commit:0:12}"
cat <<EOF

Committed $(git rev-parse --short=12 HEAD); read it whole with: git show HEAD
Then publish it to each repository that runs the verifier, which only a repository admin can do:
  git push https://github.com/OWNER/REPOSITORY.git verifier:refs/heads/verifier
EOF
