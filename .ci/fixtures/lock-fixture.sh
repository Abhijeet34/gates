#!/usr/bin/env bash
# Build one scenario for run/lock.sh in the working directory: a gates origin,
# a fork of it, the gates checkout a bootstrap makes, a caller, a stub `gh`
# serving check runs, and env.sh, which a case sources before running the gate
# from caller/.
#
#   .ci/fixtures/lock-fixture.sh <scenario>
#
# gates main is A -> B -> C, and C is the bootstrap's own commit. B changes the
# synced copies. F is a commit on B that only the fork has, fetched into the
# gates checkout the way GitHub serves any fork-network object by sha. Every
# commit's `checks` run is green unless the scenario says otherwise.
set -euo pipefail

scenario=${1:?usage: lock-fixture.sh <scenario>}
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=fixture GIT_AUTHOR_EMAIL=fixture@invalid
export GIT_COMMITTER_NAME=fixture GIT_COMMITTER_EMAIL=fixture@invalid
BUMP_LOGIN='hw-bump[bot]' BUMP_ID=4242

commit() { git -C "$1" add -A && git -C "$1" commit -qm "$2" && git -C "$1" rev-parse HEAD; }

git init -q -b main src
mkdir -p src/.githooks src/system-maintenance/git-hooks src/run
echo 'rules A' >src/.gitleaks.toml
printf '#!/bin/sh\n# hook A\n' >src/.githooks/pre-push
printf '#!/bin/sh\n# msg A\n' >src/system-maintenance/git-hooks/commit-msg
chmod +x src/.githooks/pre-push src/system-maintenance/git-hooks/commit-msg
printf '# bump identity\n%s %s\n' "$BUMP_LOGIN" "$BUMP_ID" >src/run/bump-identity
A=$(commit src A)
echo 'rules B' >src/.gitleaks.toml
B=$(commit src B)
echo bootstrap >src/README.md
C=$(commit src C)
git clone -q --bare src gates.git
git -C src checkout -q -b fork "$B"
echo 'rules F' >src/.gitleaks.toml
F=$(commit src F)
git clone -q --bare src fork.git

git clone -q gates.git .gates
git -C .gates fetch -q ../fork.git fork
git -C .gates checkout -q --detach "$C"

mkdir bin checks
cat >bin/gh <<'GH'
#!/bin/sh
sha=$(printf '%s' "$2" | sed -n 's|.*/commits/\([0-9a-f]*\)/check-runs.*|\1|p')
conclusion=$(cat "$(dirname "$0")/../checks/$sha" 2>/dev/null || echo success)
printf '{"total_count":1,"check_runs":[{"name":"checks","status":"completed","conclusion":"%s","app":{"id":15368}}]}\n' "$conclusion"
GH
chmod +x bin/gh

# The caller's synced copies as gates carries them at <sha>.
copies() {
    mkdir -p caller/.githooks
    git -C .gates show "$1:.gitleaks.toml" >caller/.gitleaks.toml
    git -C .gates show "$1:.githooks/pre-push" >caller/.githooks/pre-push
    git -C .gates show "$1:system-maintenance/git-hooks/commit-msg" >caller/.githooks/commit-msg
    chmod +x caller/.githooks/pre-push caller/.githooks/commit-msg
}
lock() { mkdir -p caller/.github && echo "$1" >caller/.github/gates.lock; }
unlock() { rm -f caller/.github/gates.lock; }

git init -q -b main caller
echo "print('hi')" >caller/app.py
event=push author=$BUMP_LOGIN author_id=$BUMP_ID
case $scenario in
    legacy) copies "$C" ;;
    locked) copies "$B" && lock "$B" ;;
    imposter) copies "$F" && lock "$F" ;;
    unchecked) copies "$B" && lock "$B" && echo failure >"checks/$B" ;;
    drift) copies "$A" && lock "$B" ;;
    # A pull request: the base, then the head it proposes.
    bump | bundled | author | impersonated) event=pull_request && copies "$A" && lock "$A" ;;
    downgrade | removed) event=pull_request && copies "$B" && lock "$B" ;;
    added | pr-legacy) event=pull_request && copies "$C" ;;
    *) echo "lock-fixture: unknown scenario $scenario" >&2 && exit 2 ;;
esac
base=$(commit caller base)
case $scenario in
    bump | author | impersonated) copies "$B" && lock "$B" ;;
    bundled) copies "$B" && lock "$B" && echo "print('and more')" >>caller/app.py ;;
    downgrade) copies "$A" && lock "$A" ;;
    added) lock "$C" ;;
    removed) copies "$C" && unlock ;;
    pr-legacy) echo "print('a feature')" >>caller/app.py ;;
esac
[ "$scenario" = author ] && author=someone author_id=1
[ "$scenario" = impersonated ] && author_id=9999
[ "$event" = pull_request ] && commit caller head >/dev/null

cat >env.sh <<ENV
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GATES_DIR="$PWD/.gates" GATES_REPOSITORY=o/gates BOOTSTRAP_SHA=$C
export EVENT_NAME=$event BASE_SHA=$base PR_AUTHOR='$author' PR_AUTHOR_ID=$author_id
export PATH="$PWD/bin:\$PATH" GH_TOKEN=stub
export FIX_A=$A FIX_B=$B FIX_C=$C FIX_F=$F
ENV
