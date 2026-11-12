#!/usr/bin/env bash
# Print a ruleset file against the live ruleset it would replace, before the
# maintainer applies it; it reads and never writes.
#
#   ruleset-read.sh OWNER/NAME RULESET_ID FILE
#
# Both sides are reduced to what an apply sets, with rules and bypass actors in
# a fixed order. Exit 0 when they are the same, 1 with the difference printed,
# 2 when either cannot be read.

set -euo pipefail

if [ $# -ne 3 ] || ! [[ $1 =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ && $2 =~ ^[0-9]+$ ]] || [ ! -f "$3" ]; then
    echo "usage: ruleset-read.sh OWNER/NAME RULESET_ID FILE" >&2
    exit 2
fi
shape='{name, target, enforcement, conditions,
  bypass_actors: ((.bypass_actors // []) | map({actor_id, actor_type, bypass_mode}) | sort_by(.actor_type, .actor_id)),
  rules: ((.rules // []) | map({type, parameters: (.parameters // null)}) | sort_by(.type))}'

live=$(gh api "repos/$1/rulesets/$2") || { echo "ruleset-read: could not read ruleset $2 of $1" >&2; exit 2; }
live=$(printf '%s' "$live" | jq -S "$shape") || { echo "ruleset-read: the live ruleset is not JSON" >&2; exit 2; }
file=$(jq -S "$shape" "$3") || { echo "ruleset-read: $3 is not JSON" >&2; exit 2; }
if [ "$live" = "$file" ]; then
    echo "ruleset-read: $3 is the live ruleset $2 of $1"
    exit 0
fi
diff -u --label "live ruleset $2 of $1" --label "$3" <(printf '%s\n' "$live") <(printf '%s\n' "$file") || true
exit 1
