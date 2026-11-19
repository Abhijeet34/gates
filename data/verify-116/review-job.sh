#!/usr/bin/env bash
# review-job.sh GATES_TREE WORKDIR CASE VARIANT [MUTATE_CMD]
# Runs GATES_TREE/verifier/review.sh, the verifier's review job, as the workflow runs it:
# head and base served from a file:// remote, `gh api` answering the pull request,
# `curl` serving a CLI tarball whose package/claude is the recorded-reply stub
# (CLI_SHA512 rewritten to that tarball's digest, the only edit), and the real
# run/seats.py. Prints the decoded job outputs, then the judge's reading of the
# verdict through run/owner-verify.py's review_verdict.
# OVERRIDE_SEAT/OVERRIDE_TEXT in the environment rewrite one recorded reply.
set -uo pipefail
G=$1 W=$2 CASE=$3 VAR=$4 MUT=${5:-}
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
mkdir -p "$W" && cd "$W" || exit 9
[ -f "$G/verifier/review.sh" ] || { echo "review.sh: absent in $G, so the verifier has no review job"; exit 127; }
F=$G
mkdir -p srv/o && cp -R "$F/corpus/seats/base" srv/o/r
git -C srv/o/r init -q -b main && git -C srv/o/r config uploadpack.allowAnySHA1InWant true
git -C srv/o/r add -A && git -C srv/o/r commit -qm base; base=$(git -C srv/o/r rev-parse HEAD)
if [ -n "$MUT" ]; then (cd srv/o/r && eval "$MUT"); else cp -R "$F/corpus/seats/cases/$CASE/change/." srv/o/r/; fi
git -C srv/o/r add -A && git -C srv/o/r commit -qm change; head=$(git -C srv/o/r rev-parse HEAD)
git -C srv/o/r checkout -q "$base"
mkdir -p cli/package bin kernel/gates
cp "$F/.ci/fixtures/review/claude-recorded.py" cli/package/claude && chmod +x cli/package/claude
tar -czf cli.tgz -C cli package
sha=$(openssl dgst -sha512 -binary cli.tgz | base64 | tr -d '\n')
sed "s#^CLI_SHA512=.*#CLI_SHA512=$sha#" "$G/verifier/review.sh" > kernel/review.sh
cp -R "$G/run" kernel/gates/run
cat > bin/curl <<C
#!/usr/bin/env bash
while [ \$# -gt 0 ]; do [ "\$1" = -o ] && { cp "$W/cli.tgz" "\$2"; exit 0; }; shift; done; exit 22
C
cat > bin/gh <<C
#!/usr/bin/env bash
printf '%s' '{"number":7,"title":"Tidy permit rules","body":"No behaviour change.","head":{"sha":"$head","repo":{"full_name":"o/r"}},"base":{"sha":"$base","ref":"main"}}'
C
chmod +x bin/curl bin/gh
mkdir -p replies/seats && cp -R "$F/.ci/fixtures/review/corpus/seats/$CASE" replies/seats/ 2>/dev/null
# OVERRIDE_SEAT=sonnet|opus with OVERRIDE_TEXT replaces that seat's reply inside its real envelope.
if [ -n "${OVERRIDE_SEAT:-}" ]; then python3 -c 'import json,sys; p,t=sys.argv[1:]; e=json.load(open(p)); e["result"]=t; json.dump(e,open(p,"w"))' "replies/seats/$CASE/$VAR/$OVERRIDE_SEAT.json" "$OVERRIDE_TEXT"; fi
: > out
env -i HOME="$W" PATH="$W/bin:/usr/local/bin:/usr/bin:/bin" GITHUB_OUTPUT="$W/out" GITHUB_REF=refs/heads/verifier \
  GITHUB_REPOSITORY=o/r GITHUB_SERVER_URL="file://$W/srv" GH_TOKEN=unused PR=7 HEAD_SHA="$head" BASE_SHA="$base" \
  CLAUDE_CODE_OAUTH_TOKEN=dummy-login GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
  CLAUDE_RECORDED_REPLIES="$W/replies/seats/$CASE" GATES_CORPUS_CASE="$VAR" \
  bash -e kernel/review.sh > job.log 2>&1
echo "review.sh exit=$?"
python3 - "$W/out" "$G/run/owner-verify.py" "$head" <<'PY'
import base64, importlib.util, json, sys, tempfile
out = dict(l.split("=", 1) for l in open(sys.argv[1]).read().splitlines())
v = base64.b64decode(out.get("verdict", "")).decode()
print("job output verdict:", v.strip() or "(empty: classify passes no --review)")
print("job output summary:"); print("  " + base64.b64decode(out["summary"]).decode().replace("\n", "\n  "))
if v:
    spec = importlib.util.spec_from_file_location("ov", sys.argv[2]); ov = importlib.util.module_from_spec(spec); spec.loader.exec_module(ov)
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); f.write(v); f.close()
    print("judge review_verdict(head) ->", ov.review_verdict(f.name, sys.argv[3]), "blocking")
PY
