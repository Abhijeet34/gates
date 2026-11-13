#!/usr/bin/env bash
# Set up one case of corpus/seats for run/seats.py in the current directory.
#
#   seats-case.sh CASE [VARIANT SEAT TEXT]
#
# Builds `repo/` from corpus/seats/base with the case's change committed over it,
# writing its commits to `base` and `head` and an empty title and body to
# `pr.json`; copies the case's recorded replies (corpus/seats/CASE) under
# replies/seats/, installs claude-recorded.py as `.stub/claude`, and writes CASE
# to `case`. TEXT replaces the reply of SEAT (`sonnet`, `opus`) in VARIANT
# inside its real envelope, for an answer the live run did not give.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
corpus="$here/../../../corpus/seats"
mkdir -p .stub replies/seats
cp "$here/claude-recorded.py" .stub/claude
chmod +x .stub/claude
cp -R "$here/corpus/seats/$1" replies/seats/
cp -R "$corpus/base" repo
git -C repo init -q -b main
git -C repo add -A
git -C repo commit -qm base
git -C repo rev-parse HEAD >base
cp -R "$corpus/cases/$1/change/." repo/
git -C repo add -A
git -C repo commit -qm change
git -C repo rev-parse HEAD >head
printf '{"title": "", "body": ""}\n' >pr.json
printf '%s\n' "$1" >case
[[ $# -lt 4 ]] && exit 0
python3 - "replies/seats/$1/$2/$3.json" "$4" <<'PY'
import json, sys
path, text = sys.argv[1], sys.argv[2]
envelope = json.load(open(path))
envelope["result"] = text
json.dump(envelope, open(path, "w"))
PY
