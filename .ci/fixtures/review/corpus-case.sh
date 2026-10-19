#!/usr/bin/env bash
# Set up one seeded-corpus case for run/corpus.py in the current directory.
#
#   corpus-case.sh LENS CASE [ANSWER]
#
# Copies the case's recorded replies (corpus/LENS/CASE) under replies/, installs
# claude-recorded.py as `.stub/claude`, and writes LENS and CASE to `lens` and
# `case`, so
#   PATH="$PWD/.stub:$PATH" CLAUDE_RECORDED_REPLIES="$PWD/replies" \
#     run/corpus.py --corpus corpus/"$(cat lens)" --case "$(cat case)" --store store
# replays it. ANSWER, a JSON object, replaces the finder's answer inside its real
# envelope, for an outcome the live run did not produce.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p .stub "replies/$1"
cp "$here/claude-recorded.py" .stub/claude
chmod +x .stub/claude
cp -R "$here/corpus/$1/$2" "replies/$1/"
printf '%s\n' "$1" > lens
printf '%s\n' "$2" > case
[[ $# -lt 3 ]] && exit 0
python3 - "replies/$1/$2/finder.json" "$3" <<'EOF'
import json, sys
path, answer = sys.argv[1], sys.argv[2]
envelope = json.load(open(path))
envelope["result"] = json.dumps(json.loads(answer))
json.dump(envelope, open(path, "w"))
EOF
