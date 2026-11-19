#!/usr/bin/env bash
# attack.sh GATES_TREE FIXTURES_TREE WORKDIR NAME MUTATE_CMD SONNET_REPLY OPUS_REPLY [OPUS_AGAIN_REPLY] [SEAT_SECONDS]
# Caller repo from corpus/seats/base, MUTATE_CMD applied as the pull request's change,
# then GATES_TREE/run/seats.py (a copy with SEAT_SECONDS patched when given) through the
# recorded claude stub, each seat answering with the given text inside a real envelope.
# A reply of "-" means no recording (the stub exits 1); "SLEEP:n:text" sleeps n seconds;
# "ENV:json" merges json into the envelope.
set -uo pipefail
G=$1 F=$2 W=$3/$4 MUT=$5
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
mkdir -p "$W/.stub/replies" && cd "$W" || exit 9
cp "$F/.ci/fixtures/review/claude-recorded.py" .stub/claude && chmod +x .stub/claude
cp "$F/.ci/fixtures/review/corpus/seats/permit-drop-rm/plain/version.txt" .stub/replies/
tmpl="$F/.ci/fixtures/review/corpus/seats/permit-drop-rm/plain"
python3 - "$tmpl" .stub/replies "${6}" "${7}" "${8:--}" <<'PY'
import json, sys
tmpl, out, *replies = sys.argv[1:]
for seat, text in zip(("sonnet", "opus", "opus-again"), replies):
    if text == "-":
        continue
    model = "claude-sonnet-5-5" if seat == "sonnet" else "claude-opus-5-5"
    env = json.load(open(f"{tmpl}/{'sonnet' if seat == 'sonnet' else 'opus'}.json"))
    if text.startswith("SLEEP:"):
        _, n, text = text.split(":", 2)
        open(f"{out}/{seat}.sleep", "w").write(n)
    if text.startswith("ENV:"):
        env.update(json.loads(text[4:]))
    else:
        env["result"] = text
    json.dump(env, open(f"{out}/{seat}.json", "w"))
PY
rm -rf repo; cp -R "$F/corpus/seats/base" repo
git -C repo init -q -b main && git -C repo add -A && git -C repo commit -qm base
base=$(git -C repo rev-parse HEAD)
(cd repo && eval "$MUT") && git -C repo add -A && git -C repo commit -qm change
head=$(git -C repo rev-parse HEAD)
printf '{"title": "t", "body": "b"}\n' > pr.json
seats=$G/run/seats.py
if [ -n "${9:-}" ]; then
  mkdir -p g/run && cp "$G"/run/review.py "$G"/run/owner-verify.py "$G"/run/gate.py g/run/ && cp -R "$G"/run/. g/run/ 2>/dev/null
  sed -i "s/^SEAT_SECONDS = .*/SEAT_SECONDS = ${9}/" g/run/seats.py; seats=$PWD/g/run/seats.py
fi
PATH="$W/.stub:$PATH" python3 "$seats" --tree repo/.git --base "$base" --head "$head" --pr-text pr.json --out verdict.json --store store
rc=$?
echo "exit=$rc verdict=$(cat verdict.json 2>/dev/null || echo none)"
echo "changed: $(git -C repo diff --name-status --no-renames $base $head | tr '\n\t' '; ')"
[ -f .stub/calls.jsonl ] && python3 -c '
import json,sys
for l in open(sys.argv[1]):
    c=json.loads(l); p=c["prompt"]; i=p.find("## The change to the safeguard paths")
    print("  seat", c["seat"], "shown diff header:", " | ".join(x for x in p[i:].splitlines() if x.startswith(("diff --git","deleted file","new file")))[:400])
' .stub/calls.jsonl
exit $rc
