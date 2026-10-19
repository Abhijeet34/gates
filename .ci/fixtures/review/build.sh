#!/usr/bin/env bash
# Build a fixture repository for run/review.py in the current directory.
#
#   build.sh [--replies NAME] STEP
#
# Commits base/ with a review row in .github/gates.toml, tags it `base`, then
# commits the STEP directory over it as the change under review. --replies
# installs claude-recorded.py as `.stub/claude`, untracked, answering from
# replies/NAME, so `PATH="$PWD/.stub:$PATH"` puts the recorded seats in place of
# the real CLI.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
replies=
if [[ "$1" == --replies ]]; then
    replies="$2"
    shift 2
fi

git init -q --template= -b main
mkdir .github
cat > .github/gates.toml <<'EOF'
[[gate]]
id = "code-review"
kind = "review"
inputs = ["**"]
lens = "code"
reviewers = ["claude-sonnet-5-5", "claude-opus-5-5"]
shadow_since = 2026-10-19
EOF
cp -R "$here/base/." .
git add -A
git commit -qm base
git tag base
cp -R "$here/$1/." .
git add -A
git commit -qm "$1"

[[ -n "$replies" ]] || exit 0
mkdir -p .git/info .stub
echo .stub >> .git/info/exclude
cp "$here/claude-recorded.py" .stub/claude
chmod +x .stub/claude
cp -R "$here/replies/$replies" .stub/replies
