#!/usr/bin/env bash
# stops.sh TREE WORKROOT
# Probes run/review.py's safety_stops read through data/verify-116/attack.sh unchanged:
# the permit rm rule deleted, both seats answering a clean approval, every seat's envelope
# carrying the given safety_stops. Prints, per value, seats.py's exit and verdict and the
# undecided reason or the last traceback line.
set -uo pipefail
T=$1 R=$2
A=$(cd "$(dirname "$0")/../verify-116" && pwd)/attack.sh
AP='{"approve": true, "findings": []}'
for v in '"yes"' '[1]' '0.5' '1.0' 'true' '"1"' '"0"' '{"n": 1}' '-1' '""' '[]' 'false' 'null' '0' '1' '2'; do
  env=$(python3 -c 'import json,sys; print("ENV:"+json.dumps({"safety_stops": json.loads(sys.argv[1]), "result": sys.argv[2]}))' "$v" "$AP")
  name=s-$(printf '%s' "$v" | tr -c 'A-Za-z0-9.-' '_')
  out=$("$A" "$T" "$T" "$R" "$name" 'sed -i 23d internal/permit/permit.go' "$env" "$env" "$env" 2>&1)
  ex=$(grep -m1 '^exit=' <<<"$out" | sed 's/"head": "[0-9a-f]*", //')
  why=$(grep -E 'could not decide|Error:|Exception' <<<"$out" | tail -1 | cut -c1-160)
  printf '%-10s %s | %s\n' "$v" "$ex" "$why"
done
