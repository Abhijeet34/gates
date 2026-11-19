#!/usr/bin/env bash
# scenario.sh GATES_TREE FIXTURES_TREE WORKDIR CASE VARIANT [MUTATE_CMD]
# Builds a caller repo from FIXTURES_TREE's corpus/seats/base with CASE's change
# (or, with MUTATE_CMD, runs that command in the repo instead of applying the
# change), then runs GATES_TREE/run/seats.py through the recorded claude stub,
# answering from FIXTURES_TREE's recorded replies for CASE/VARIANT.
set -uo pipefail
G=$1 F=$2 W=$3 CASE=$4 VAR=$5 MUT=${6:-}
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
mkdir -p "$W" && cd "$W" || exit 9
mkdir -p .stub
cp "$F/.ci/fixtures/review/claude-recorded.py" .stub/claude && chmod +x .stub/claude
mkdir -p .stub/replies/seats && cp -R "$F/.ci/fixtures/review/corpus/seats/$CASE" .stub/replies/seats/ 2>/dev/null
cp -R "$F/corpus/seats/base" repo
git -C repo init -q -b main && git -C repo add -A && git -C repo commit -qm base
base=$(git -C repo rev-parse HEAD)
if [ -n "$MUT" ]; then (cd repo && eval "$MUT"); else cp -R "$F/corpus/seats/cases/$CASE/change/." repo/; fi
git -C repo add -A && git -C repo commit -qm change
head=$(git -C repo rev-parse HEAD)
printf '{"title": "t", "body": "b"}\n' > pr.json
if [ ! -f "$G/run/seats.py" ]; then echo "seats.py: absent in $G (exit 127)"; exit 127; fi
CLAUDE_RECORDED_REPLIES="$W/.stub/replies/seats/$CASE" GATES_CORPUS_CASE="$VAR" PATH="$W/.stub:$PATH" \
  python3 "$G/run/seats.py" --tree repo/.git --base "$base" --head "$head" --pr-text pr.json --out verdict.json --store store
rc=$?
echo "exit=$rc verdict=$(cat verdict.json 2>/dev/null || echo none) head=$head"
echo "diff names: $(git -C repo diff --name-status --no-renames $base $head | tr '\n\t' '; ')"
exit $rc
