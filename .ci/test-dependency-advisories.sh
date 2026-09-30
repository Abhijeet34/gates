#!/usr/bin/env bash
# Regression tests for the three step bodies inside
# .github/workflows/shared-dependency-advisories.yml.
#
#   .ci/test-dependency-advisories.sh
#
# Same reason as the other three shared-workflow suites: a reusable workflow's
# steps run in the CALLER's checkout, where no file of this repository exists,
# so the logic lives inline in YAML where no linter reaches it, and callers
# reference @main with no digest pin - so a broken body is live in every caller
# at once. This file is the only thing standing against that.
#
# The properties, in the order that matters:
#   1. an open high advisory FAILS,
#   2. the same advisory on the deferral list PASSES,
#   3. a repository with no advisory at or above the floor PASSES,
#   4. every way the API read can fail to establish an inventory REFUSES,
#   5. a malformed or mis-keyed deferral list REFUSES rather than reading empty,
#   6. on a pull request, an alert the pull request's own dependency diff fixes
#      PASSES, one it leaves or re-adds FAILS, and a diff that could not be read
#      FAILS with the API's message whenever an alert depends on it.
#
# (4) has the teeth. A visibility gate that goes quiet when it cannot see is the
# defect the workflow exists to prevent, so the read is driven against a stub
# `curl` for each status it can meet - the same technique test-secret-scan.sh
# uses for the gitleaks failures real gitleaks cannot be made to produce.
#
# The last three cases are mutation cases: each deletes one property from the
# extracted body and fails unless the body then wrongly allows an input it
# refuses today. A property with no such case is a property nothing measures
# (AGENTS.md, "a gate that certifies what it never measured").

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

WORKFLOW=.github/workflows/shared-dependency-advisories.yml
[ -r "$WORKFLOW" ] || { echo "test-dependency-advisories: cannot read $WORKFLOW" >&2; exit 2; }
for t in jq yq; do
    command -v "$t" >/dev/null 2>&1 || { echo "test-dependency-advisories: $t is not installed (brew install $t)" >&2; exit 2; }
done

workdir=$(mktemp -d) || exit 2
trap 'rm -rf "$workdir"' EXIT

failed=0

# --- extract the step bodies -------------------------------------------------

# Print the `run: |` payload of the step named $2, dedented, ending at the first
# non-blank line indented less than the block.
extract() { # workflow step-name outfile
    awk -v step="name: $2" '
        index($0, step) { found = 1; next }
        found && !inblock && /^[[:space:]]*run:[[:space:]]*\|[[:space:]]*$/ {
            match($0, /^ */); indent = RLENGTH + 2; inblock = 1; next
        }
        inblock {
            if ($0 ~ /^[[:space:]]*$/) { print ""; next }
            match($0, /^ */)
            if (RLENGTH < indent) exit
            print substr($0, indent + 1)
        }
    ' "$1" >"$3"
}

READ_STEP="$workdir/read.sh"
JUDGE_STEP="$workdir/judge.sh"
COMPARE_STEP="$workdir/compare.sh"
extract "$WORKFLOW" "Read this repository's open Dependabot alerts" "$READ_STEP"
extract "$WORKFLOW" 'Read the dependency changes this pull request makes' "$COMPARE_STEP"
extract "$WORKFLOW" 'Judge the open advisories against the deferral list' "$JUDGE_STEP"

# Extraction is the one failure that makes every case below pass vacuously, and
# the awk stops at the first under-indented line - so a continuation written at
# column 2 inside a multi-line string truncates the body silently. Asserted at
# BOTH ends of each block, which is the correction
# .ci/test-guard-generated-files.sh had to make.
assert_marker() { # file marker label
    grep -qF -- "$2" "$1" || {
        printf 'FAIL the %s body extracted from %s is missing %s\n' "$3" "$WORKFLOW" "$2"
        exit 1
    }
}
assert_marker "$READ_STEP" 'COULD NOT ESTABLISH' 'read-step'
assert_marker "$READ_STEP" 'open alerts over' 'read-step'
assert_marker "$COMPARE_STEP" 'COULD NOT BE READ' 'compare-step'
assert_marker "$COMPARE_STEP" 'dependency change(s) between' 'compare-step'
assert_marker "$JUDGE_STEP" 'severity_floor is' 'judge-step'
assert_marker "$JUDGE_STEP" 'neither fixed nor recorded' 'judge-step'

# --- fixtures ----------------------------------------------------------------

alert() { # severity scope ghsa package [manifest]
    jq -n --arg s "$1" --arg sc "$2" --arg g "$3" --arg p "$4" --arg m "${5:-package.json}" '{
        security_advisory: {severity: $s, ghsa_id: $g},
        dependency: {scope: $sc, package: {name: $p}, manifest_path: $m},
        html_url: "https://example.invalid/\($g)"
    }'
}
inventory() { jq -s '.' ; }   # stdin: alert objects

HIGH_DEV="$workdir/high-dev.json"
alert high development GHSA-2v37-7h3g-55p8 nanoid | inventory >"$HIGH_DEV"

MEDIUM_ONLY="$workdir/medium.json"
{ alert medium development GHSA-2v8p-3f2j-5mp7 mermaid
  alert low development GHSA-c4c3-pg64-4m4v mermaid; } | inventory >"$MEDIUM_ONLY"

EMPTY="$workdir/empty.json"
printf '[]\n' >"$EMPTY"

NULL_SCOPE="$workdir/null-scope.json"
jq -n '[{security_advisory: {severity: "high", ghsa_id: "GHSA-aaaa-bbbb-cccc"},
         dependency: {package: {name: "mystery"}}}]' >"$NULL_SCOPE"

deferral_file() { # outfile ghsa package [until]
    cat >"$1" <<YML
deferrals:
  - ghsa: $2
    package: $3
    reason: >-
      Pinned at an exact version the Excalidraw converter depends on.
    until: >-
      ${4:-The converter is ported off that version, or the advisory gains a fix in a release the pin can take.}
YML
}

# --- the judge step ----------------------------------------------------------

# EVENT and COMPARE, when set by the caller, make the run a pull request's;
# unset, it is a push, which never reads a compare record.
judge() { # label expected-exit alerts deferrals-path [floor] [faildev]
    local label="$1" want="$2" rc
    (
        cd "$workdir" || exit 127
        EVENT_NAME="${EVENT:-push}" COMPARE_JSON="${COMPARE:-/nonexistent.json}" \
        ALERTS_JSON="$3" DEFERRALS_PATH="$4" \
        SEVERITY_FLOOR="${5:-high}" FAIL_ON_DEVELOPMENT="${6:-true}" \
        GITHUB_STEP_SUMMARY=/dev/null \
            bash -e "$JUDGE_STEP"
    ) >"$workdir/out" 2>"$workdir/err"
    rc=$?
    if { [ "$want" = nonzero ] && [ "$rc" -ne 0 ]; } || [ "$rc" = "$want" ]; then
        printf 'ok   %s\n' "$label"
    else
        printf 'FAIL %s: expected exit %s, got %d\n' "$label" "$want" "$rc"
        sed 's/^/       /' "$workdir/err" | head -20
        failed=1
    fi
}

# Asserts against the run immediately above. A refusal that names nothing is a
# puzzle; only the exit code is otherwise pinned.
said() { # label substring [stream]
    local f="$workdir/${3:-err}"
    if grep -qF -- "$2" "$f"; then
        printf 'ok   %s\n' "$1"
    else
        printf 'FAIL %s: nothing said %s\n' "$1" "$2"
        sed 's/^/       /' "$f" | head -20
        failed=1
    fi
}

echo '--- the verdict ---'

judge 'an open high advisory fails' nonzero "$HIGH_DEV" /nonexistent.yml
said  '  and names the package and the GHSA' 'nanoid'
said  '  and says what to do' 'neither fixed nor recorded'

DEF_OK="$workdir/deferrals-ok.yml"
deferral_file "$DEF_OK" GHSA-2v37-7h3g-55p8 nanoid
judge 'the same advisory on the deferral list passes' 0 "$HIGH_DEV" "$DEF_OK"
said  '  and prints the reason it stands' 'reason:' out
said  '  and prints the condition that ends it' 'until:' out

judge 'no advisory at or above the floor passes' 0 "$MEDIUM_ONLY" /nonexistent.yml
said  '  and still inventories the medium and low ones' '| medium |' out
judge 'a repository with no open alerts at all passes' 0 "$EMPTY" /nonexistent.yml

# Whether development-scoped alerts can fail, answered strict and pinned both ways.
judge 'development scope fails by default' nonzero "$HIGH_DEV" /nonexistent.yml high true
judge 'fail_on_development=false exempts it' 0 "$HIGH_DEV" /nonexistent.yml high false

# A null scope is unknown, not development: GitHub omits the label when it
# cannot tell, and exempting unknown is the scope label being trusted again.
judge 'an unknown scope is not exempt even in lenient mode' nonzero "$NULL_SCOPE" /nonexistent.yml high false

judge 'the floor is honoured: medium floor fails on medium' nonzero "$MEDIUM_ONLY" /nonexistent.yml medium
judge 'the floor is honoured: critical floor passes a high' 0 "$HIGH_DEV" /nonexistent.yml critical

echo '--- the deferral list ---'

STALE="$workdir/deferrals-stale.yml"
deferral_file "$STALE" GHSA-dead-beef-cafe lodash
judge 'a deferral matching no open alert is reported, not fatal' 0 "$MEDIUM_ONLY" "$STALE"
said  '  and says the entry can go' 'outlived its reason'

MISKEY="$workdir/deferrals-miskeyed.yml"
deferral_file "$MISKEY" GHSA-2v37-7h3g-55p8 lodash
judge 'a deferral naming the wrong package refuses' nonzero "$HIGH_DEV" "$MISKEY"
said  '  and names both packages' 'but the open alert for it is `nanoid`'

malformed() { # label yaml-body substring
    local f="$workdir/bad.yml"
    printf '%s\n' "$2" >"$f"
    judge "$1" nonzero "$HIGH_DEV" "$f"
    said "  and names it" "$3"
}
malformed 'a missing required key refuses' \
    'deferrals:
  - ghsa: GHSA-2v37-7h3g-55p8
    package: nanoid
    reason: because' 'missing required key `until`'
malformed 'an unknown key refuses' \
    'deferrals:
  - ghsa: GHSA-2v37-7h3g-55p8
    package: nanoid
    reason: because
    until: later
    expires: 2027-01-01' 'unknown key `expires`'
malformed 'an empty reason refuses' \
    'deferrals:
  - ghsa: GHSA-2v37-7h3g-55p8
    package: nanoid
    reason: "  "
    until: later' '`reason` is empty'
malformed 'a value that is not a GHSA id refuses' \
    'deferrals:
  - ghsa: CVE-2024-1234
    package: nanoid
    reason: because
    until: later' 'not a GHSA id'
malformed 'the same GHSA listed twice refuses' \
    'deferrals:
  - ghsa: GHSA-2v37-7h3g-55p8
    package: nanoid
    reason: because
    until: later
  - ghsa: GHSA-2v37-7h3g-55p8
    package: nanoid
    reason: again
    until: later' 'is listed 2 times'
malformed 'an unknown top-level key refuses' \
    'deferrals: []
allow_all: true' 'unknown top-level key `allow_all`'
malformed 'a file that is not valid YAML refuses' \
    'deferrals: [
  - ghsa' 'is not valid YAML'
malformed 'a list at the top level refuses' \
    '- ghsa: GHSA-2v37-7h3g-55p8
  package: nanoid
  reason: because
  until: later' 'must be a mapping with a `deferrals:` key'
# An empty file is the most likely thing for someone to create first, and it
# must refuse rather than read as an empty list: `yq` gives back `null`, which
# is not "no deferrals", it is "nothing was read".
: >"$workdir/bad.yml"
judge 'an empty deferral file refuses rather than reading as no deferrals' nonzero "$HIGH_DEV" "$workdir/bad.yml"
said  '  and names what it wanted' 'must be a mapping with a `deferrals:` key'

judge 'a bad severity_floor refuses rather than defaulting' nonzero "$EMPTY" /nonexistent.yml sevre
said  '  and names the value' 'severity_floor is "sevre"'
judge 'a bad fail_on_development refuses' nonzero "$EMPTY" /nonexistent.yml high yes
judge 'a missing inventory refuses rather than reporting clean' nonzero "$workdir/no-such.json" /nonexistent.yml
said  '  and says nothing was judged' 'nothing was judged'

# yq absent with a deferral list present: refuse, never read it as empty.
stubdir=$(mktemp -d "$workdir/nopath.XXXXXX")
for t in bash jq mktemp grep sed tr printf cat head env; do
    p=$(command -v "$t") && ln -sf "$p" "$stubdir/$t"
done
(
    cd "$workdir" || exit 127
    PATH="$stubdir" EVENT_NAME=push ALERTS_JSON="$HIGH_DEV" DEFERRALS_PATH="$DEF_OK" \
    SEVERITY_FLOOR=high FAIL_ON_DEVELOPMENT=true GITHUB_STEP_SUMMARY=/dev/null \
        "$BASH" -e "$JUDGE_STEP"
) >"$workdir/out" 2>"$workdir/err"
if [ $? -ne 0 ] && grep -qF 'yq is not installed' "$workdir/err"; then
    printf 'ok   %s\n' 'an unreadable deferral list refuses instead of reading empty'
else
    printf 'FAIL %s\n' 'yq missing did not refuse'
    sed 's/^/       /' "$workdir/err" | head -10
    failed=1
fi

# --- the verdict on a pull request ------------------------------------------

echo '--- the verdict on a pull request ---'

# The shape the live compare endpoint answered for treadling PR 120, which
# upgrades fast-uri 3.1.6 -> 3.1.8 past GHSA-58mr-gqgx-xq4g (2026-09-30).
FASTURI="$workdir/fast-uri.json"
alert high development GHSA-58mr-gqgx-xq4g fast-uri package-lock.json | inventory >"$FASTURI"

change() { # change_type name version [ghsa] [manifest]
    jq -n --arg t "$1" --arg n "$2" --arg v "$3" --arg g "${4:-}" --arg m "${5:-package-lock.json}" '{
        change_type: $t, manifest: $m, ecosystem: "npm", name: $n, version: $v,
        vulnerabilities: (if $g == "" then [] else [{severity: "high", advisory_ghsa_id: $g}] end)
    }'
}
record() { jq -s '{changes: .}'; }   # stdin: change objects

FIXES="$workdir/cmp-fixes.json"
{ change removed fast-uri 3.1.6 GHSA-58mr-gqgx-xq4g
  change added fast-uri 3.1.8; } | record >"$FIXES"
LEAVES="$workdir/cmp-leaves.json"
{ change removed nanoid 3.3.7
  change added nanoid 3.3.8; } | record >"$LEAVES"
STILL="$workdir/cmp-still.json"
{ change removed fast-uri 3.1.6 GHSA-58mr-gqgx-xq4g
  change added fast-uri 3.1.5 GHSA-58mr-gqgx-xq4g; } | record >"$STILL"
ELSEWHERE="$workdir/cmp-elsewhere.json"
{ change removed fast-uri 3.1.6 GHSA-58mr-gqgx-xq4g docs/package-lock.json
  change added fast-uri 3.1.8 "" docs/package-lock.json; } | record >"$ELSEWHERE"
CMP_ERROR="$workdir/cmp-error.json"
jq -n '{error: "HTTP 403, \"Forbidden\" (GitHub answers 403 here for a private repository without Advanced Security, and for a token without `contents: read`)"}' >"$CMP_ERROR"

EVENT=pull_request COMPARE="$FIXES" \
judge 'a pull request that removes the vulnerable version passes' 0 "$FASTURI" /nonexistent.yml
said  '  and reports it as fixed in this pull request' 'Fixed in this pull request' out
said  '  and names the advisory it fixed' '- high fast-uri GHSA-58mr-gqgx-xq4g (package-lock.json)' out

EVENT=pull_request COMPARE="$LEAVES" \
judge 'a pull request that leaves the vulnerable version fails' nonzero "$FASTURI" /nonexistent.yml
said  '  and says it is neither fixed nor recorded' 'neither fixed nor recorded'

EVENT=pull_request COMPARE="$STILL" \
judge 'a pull request that bumps to another still-vulnerable version fails' nonzero "$FASTURI" /nonexistent.yml
said  '  and names the advisory' 'GHSA-58mr-gqgx-xq4g'

EVENT=pull_request COMPARE="$ELSEWHERE" \
judge 'a removal at a different manifest does not fix the alert' nonzero "$FASTURI" /nonexistent.yml

EVENT=pull_request COMPARE="$CMP_ERROR" \
judge 'a compare API error fails when an alert depends on it' nonzero "$FASTURI" /nonexistent.yml
said  '  and says the fix could not be established' 'COULD NOT BE ESTABLISHED'
said  '  and carries the API message' 'HTTP 403, "Forbidden"'

EVENT=pull_request COMPARE="$CMP_ERROR" \
judge 'a compare API error with no alert at issue passes' 0 "$MEDIUM_ONLY" /nonexistent.yml
said  '  and still warns with the API message' 'HTTP 403, "Forbidden"' out

EVENT=pull_request COMPARE="$workdir/no-such-compare.json" \
judge 'a pull request with no compare record fails rather than reading no changes' nonzero "$FASTURI" /nonexistent.yml
said  '  and says the record is missing' 'left no readable record'

# Push, schedule and dispatch never read the record, even one that would pass.
EVENT=push COMPARE="$FIXES" \
judge 'a push ignores a compare record and keeps the alert failing' nonzero "$FASTURI" /nonexistent.yml
EVENT=schedule COMPARE="$FIXES" \
judge 'a scheduled run ignores it too' nonzero "$FASTURI" /nonexistent.yml

PIP="$workdir/pip.json"
alert high runtime GHSA-aaaa-bbbb-cccc Foo_Bar requirements.txt | inventory >"$PIP"
PIPFIX="$workdir/cmp-pip.json"
{ change removed foo-bar 1.0 GHSA-aaaa-bbbb-cccc requirements.txt
  change added foo-bar 1.1 "" requirements.txt; } | record >"$PIPFIX"
EVENT=pull_request COMPARE="$PIPFIX" \
judge 'package names are compared PEP 503-folded' 0 "$PIP" /nonexistent.yml

# --- the API read ------------------------------------------------------------

echo '--- the API read ---'

curlbin=$(mktemp -d "$workdir/curlbin.XXXXXX")
cat >"$curlbin/curl" <<'STUB'
#!/usr/bin/env bash
# Stands in for curl in the read-step cases. The real endpoint paginates by the
# Link header's cursors and rejects `page=` with HTTP 400 (run 33563979852), so
# this hands back a `rel="next"` whenever $STUB_DIR/body-<n+1> exists and the
# step has to follow it. ?stub_page=<n> is this stub's stand-in for a cursor.
out=""; url=""; dumphdr=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -D) dumphdr="$2"; shift 2 ;;
        -w | -H) shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
[ "${STUB_CURL_RC:-0}" = 0 ] || exit "$STUB_CURL_RC"
page=$(printf '%s' "$url" | sed -n 's/.*[?&]stub_page=\([0-9]*\).*/\1/p')
[ -n "$page" ] || page=1
body="$STUB_DIR/body-$page"; [ -r "$body" ] || body="$STUB_DIR/body"
code="$STUB_DIR/code-$page"; [ -r "$code" ] || code="$STUB_DIR/code"
if [ -n "$dumphdr" ]; then
    printf 'HTTP/2 200\r\n' >"$dumphdr"
    if [ "${STUB_ALWAYS_NEXT:-0}" = 1 ] || [ -r "$STUB_DIR/body-$((page + 1))" ]; then
        printf 'link: <https://api.invalid/x?stub_page=%s>; rel="next", <https://api.invalid/x?stub_page=9>; rel="last"\r\n' \
            "$((page + 1))" >>"$dumphdr"
    fi
    [ -z "${STUB_EXTRA_HEADER:-}" ] || printf '%s\r\n' "$STUB_EXTRA_HEADER" >>"$dumphdr"
    printf '\r\n' >>"$dumphdr"
fi
if [ -r "$body" ]; then cat "$body" >"$out"; else : >"$out"; fi
cat "$code" 2>/dev/null || echo 200
STUB
chmod +x "$curlbin/curl"

read_step() { # label expected-exit stubdir [curl-rc] [always-next]
    local label="$1" want="$2" rc
    (
        cd "$workdir" || exit 127
        PATH="$curlbin:$PATH" STUB_DIR="$3" STUB_CURL_RC="${4:-0}" \
        STUB_ALWAYS_NEXT="${5:-0}" \
        GITHUB_TOKEN=stub GITHUB_REPOSITORY=Abhijeet34/stub \
        GITHUB_API_URL=https://api.invalid RUNNER_TEMP="$3/tmp" \
        ALERTS_JSON="$3/tmp/alerts.json" \
            bash -e "$READ_STEP"
    ) >"$workdir/out" 2>"$workdir/err"
    rc=$?
    if { [ "$want" = nonzero ] && [ "$rc" -ne 0 ]; } || [ "$rc" = "$want" ]; then
        printf 'ok   %s\n' "$label"
    else
        printf 'FAIL %s: expected exit %s, got %d\n' "$label" "$want" "$rc"
        sed 's/^/       /' "$workdir/err" | head -10
        failed=1
    fi
}

scenario() { # code body -> prints a stub dir
    local d
    d=$(mktemp -d "$workdir/stub.XXXXXX")
    mkdir -p "$d/tmp"
    printf '%s' "$1" >"$d/code"
    printf '%s' "$2" >"$d/body"
    printf '%s\n' "$d"
}

s=$(scenario 200 "$(cat "$HIGH_DEV")")
read_step 'a complete 200 read succeeds' 0 "$s"
said '  and reports what it read' 'read 1 open alerts' out

s=$(scenario 200 '[]')
read_step 'an empty inventory is a legitimate read' 0 "$s"

s=$(scenario 403 '{"message":"Resource not accessible by integration"}')
read_step 'HTTP 403 refuses' nonzero "$s"
said '  and names the permission the caller must grant' 'vulnerability-alerts: read'
said '  and never claims a verdict' 'COULD NOT ESTABLISH'

s=$(scenario 403 '{"message":"Dependabot alerts are disabled for this repository."}')
read_step 'a repository with alerts disabled refuses' nonzero "$s"
said '  and says they are disabled rather than blaming the token' 'DISABLED'

for c in 404 401 422 500 502; do
    s=$(scenario "$c" '{"message":"nope"}')
    read_step "HTTP $c refuses" nonzero "$s"
done

s=$(scenario 200 '{"message":"Not Found"}')
read_step 'a 200 carrying something other than an array refuses' nonzero "$s"
said '  and says so' 'not a JSON array'

s=$(scenario 200 '<!doctype html><html>')
read_step 'a 200 carrying HTML refuses' nonzero "$s"

s=$(scenario 200 "$(jq -n '[{security_advisory: {ghsa_id: "GHSA-aaaa-bbbb-cccc"}, dependency: {package: {name: "x"}}}]')")
read_step 'an alert with no severity refuses rather than being judged clean' nonzero "$s"
said '  and says how many' 'missing a severity'

s=$(scenario 200 '[]')
read_step 'curl failing outright refuses' nonzero "$s" 7
said '  and names the exit status' 'curl exit 7'

# Pagination: a `rel="next"` must be followed, and the cap must refuse rather
# than truncate. A silent ceiling reads exactly like a clean tail.
s=$(mktemp -d "$workdir/stub.XXXXXX"); mkdir -p "$s/tmp"
printf '200' >"$s/code"
jq -nc '[{security_advisory: {severity: "low", ghsa_id: "GHSA-aaaa-bbbb-cccc"},
          dependency: {scope: "runtime", package: {name: "p"}}}]' >"$s/body-1"
jq -nc '[{security_advisory: {severity: "high", ghsa_id: "GHSA-2v37-7h3g-55p8"},
          dependency: {scope: "runtime", package: {name: "nanoid"}}}]' >"$s/body-2"
read_step 'a rel="next" link is followed to the next page' 0 "$s"
said '  and merges both pages' 'read 2 open alerts' out

s=$(scenario 200 '[]')
read_step 'an endpoint that always offers a next page refuses rather than truncating' nonzero "$s" 0 1
said '  and says it will not truncate' 'will not silently truncate'

# --- the compare read --------------------------------------------------------
#
# The step never exits non-zero; what it must get right is the record it
# leaves, because the verdict trusts a `changes` record as the whole diff.

echo '--- the compare read ---'

compare_step() { # label stubdir [curl-rc] [always-next] [extra-header] -> record in $workdir/record
    local label="$1" rc
    rm -f "$2/tmp/compare-record.json"
    (
        cd "$workdir" || exit 127
        PATH="$curlbin:$PATH" STUB_DIR="$2" STUB_CURL_RC="${3:-0}" \
        STUB_ALWAYS_NEXT="${4:-0}" STUB_EXTRA_HEADER="${5:-}" \
        GITHUB_TOKEN=stub GITHUB_REPOSITORY=Abhijeet34/stub \
        GITHUB_API_URL=https://api.invalid RUNNER_TEMP="$2/tmp" \
        BASE_SHA=aaaa HEAD_SHA=bbbb COMPARE_JSON="$2/tmp/compare-record.json" \
            bash -e "$COMPARE_STEP"
    ) >"$workdir/out" 2>"$workdir/err"
    rc=$?
    cp "$2/tmp/compare-record.json" "$workdir/record" 2>/dev/null || : >"$workdir/record"
    if [ "$rc" = 0 ] && [ -s "$workdir/record" ]; then
        printf 'ok   %s\n' "$label"
    else
        printf 'FAIL %s: exit %d, record %s\n' "$label" "$rc" "$([ -s "$workdir/record" ] && echo written || echo missing)"
        sed 's/^/       /' "$workdir/err" | head -10
        failed=1
    fi
}

s=$(scenario 200 "$(jq '.changes' "$FIXES")")
compare_step 'a complete 200 compare is recorded as changes' "$s"
said '  and reports what it read' 'read 2 dependency change(s) between aaaa and bbbb' out
said '  and the record holds both changes' '"change_type": "added"' record

s=$(scenario 403 '{"message":"Forbidden","documentation_url":"https://docs.github.com/rest/dependency-graph/dependency-review"}')
compare_step 'HTTP 403 is recorded as an error' "$s"
said '  and keeps the API message' 'HTTP 403, \"Forbidden\"' record
said '  and names the likely causes' 'Advanced Security' record

# The whole chain, as the runner runs it: the 403 record into the verdict.
cp "$workdir/record" "$workdir/record-403.json"
EVENT=pull_request COMPARE="$workdir/record-403.json" \
judge 'a 403 from the compare API fails the pull request that needed it' nonzero "$FASTURI" /nonexistent.yml
said '  and the failure quotes the API' 'the dependency compare failed with HTTP 403, "Forbidden"'

s=$(scenario 404 '{"message":"Not Found"}')
compare_step 'HTTP 404 is recorded as an error' "$s"
said '  and names the dependency graph' 'no dependency graph' record

for c in 400 500 503; do
    s=$(scenario "$c" '{"message":"nope"}')
    compare_step "HTTP $c is recorded as an error" "$s"
    said "  and never as changes" '"error"' record
done

s=$(scenario 200 '[]')
compare_step 'curl failing outright is recorded as an error' "$s" 7
said '  and names the exit status' 'curl exit 7' record

s=$(scenario 200 '{"message":"Not Found"}')
compare_step 'a 200 that is not an array is recorded as an error' "$s"
said '  and says so' 'not an array of changes' record

s=$(scenario 200 '[{"change_type":"added","manifest":"package-lock.json","name":"fast-uri","version":"3.1.5"}]')
compare_step 'an added version with no vulnerabilities list is an error, not a clean add' "$s"
said '  and says what is missing' 'vulnerabilities list' record

s=$(scenario 200 "$(jq '.changes' "$FIXES")")
compare_step 'a paginated answer is recorded as an error, not read partially' "$s" 0 1
said '  and says so' 'paginated' record

s=$(scenario 200 "$(jq '.changes' "$FIXES")")
compare_step 'a snapshot warning is recorded as an error' "$s" 0 0 \
    "x-github-dependency-graph-snapshot-warnings: $(printf 'manifest go.sum was not processed' | base64)"
said '  and decodes the warning' 'manifest go.sum was not processed' record

s=$(scenario 200 "$(jq '.changes' "$FIXES")")
compare_step 'an empty snapshot-warnings header, as GitHub sends it, is no warning' "$s" 0 0 \
    'x-github-dependency-graph-snapshot-warnings: '
said '  and the changes are recorded' '"changes"' record

# --- mutations: each property, shown having teeth ----------------------------
#
# A registry cannot be its own oracle. Each case deletes one property from the
# extracted body and fails unless the body then wrongly ALLOWS an input it
# refuses today.

echo '--- mutations ---'

mutate() { # label file sed-expr  -> prints the mutated body path
    local label="$1" src="$2" expr="$3"
    local mutated="$workdir/mutated.sh"
    sed "$expr" "$src" >"$mutated"
    if cmp -s "$src" "$mutated"; then
        # To stdout would be captured as the path by the caller's $(...).
        printf 'FAIL %s: the mutation changed nothing, so it proves nothing\n' "$label" >&2
        return 1
    fi
    printf '%s\n' "$mutated"
}

# Runs a mutated body and passes only if it now ALLOWS what the live body refuses.
mutation_allows() { # label body env-assignments...
    local label="$1"; shift
    if ( cd "$workdir" && env "$@" bash -e "$mut" ) >/dev/null 2>&1; then
        printf 'ok   %s\n' "$label"
    else
        printf 'FAIL %s: the mutated body still refuses, so the property it removes has no teeth\n' "$label"
        failed=1
    fi
}

# 1. `.dependency.scope // "unknown"` is what makes a null scope non-exempt.
if mut=$(mutate 'null-scope default' "$JUDGE_STEP" \
    's/scope: (\.dependency\.scope \/\/ "unknown")/scope: (.dependency.scope \/\/ "development")/'); then
    mutation_allows 'defaulting an unknown scope to development wrongly exempts it' \
        EVENT_NAME=push ALERTS_JSON="$NULL_SCOPE" DEFERRALS_PATH=/nonexistent.yml \
        SEVERITY_FLOOR=high FAIL_ON_DEVELOPMENT=false GITHUB_STEP_SUMMARY=/dev/null
else
    failed=1
fi

# 2. The unjudgeable-fields check in the read step.
if mut=$(mutate 'unjudgeable guard' "$READ_STEP" \
    's/\[ "\${unjudgeable:-1}" = 0 \]/true/'); then
    s=$(scenario 200 "$(jq -n '[{security_advisory: {ghsa_id: "GHSA-aaaa-bbbb-cccc"}, dependency: {package: {name: "x"}}}]')")
    mutation_allows 'dropping the field check lets an unjudgeable alert through' \
        PATH="$curlbin:$PATH" STUB_DIR="$s" STUB_CURL_RC=0 \
        GITHUB_TOKEN=stub GITHUB_REPOSITORY=Abhijeet34/stub GITHUB_API_URL=https://api.invalid \
        RUNNER_TEMP="$s/tmp" ALERTS_JSON="$s/tmp/alerts.json"
else
    failed=1
fi

# 3. The mis-keyed refusal.
if mut=$(mutate 'miskeyed refusal' "$JUDGE_STEP" \
    's/\[ "\$(get '"'"'\.miskeyed | length'"'"')" = 0 \]/true/'); then
    mutation_allows 'dropping the mis-key refusal lets a deferral for the wrong package stand' \
        EVENT_NAME=push ALERTS_JSON="$HIGH_DEV" DEFERRALS_PATH="$MISKEY" \
        SEVERITY_FLOOR=high FAIL_ON_DEVELOPMENT=true GITHUB_STEP_SUMMARY=/dev/null
else
    failed=1
fi

# 4. The re-add check: a bump to another still-vulnerable version is no fix.
if mut=$(mutate 'still-vulnerable re-add' "$JUDGE_STEP" \
    's/touches(\$chg; \.; "removed") and (touches(\$chg; \.; "added") | not)/touches($chg; .; "removed")/'); then
    mutation_allows 'dropping the re-add check lets a still-vulnerable bump pass' \
        EVENT_NAME=pull_request COMPARE_JSON="$STILL" ALERTS_JSON="$FASTURI" DEFERRALS_PATH=/nonexistent.yml \
        SEVERITY_FLOOR=high FAIL_ON_DEVELOPMENT=true GITHUB_STEP_SUMMARY=/dev/null
else
    failed=1
fi

# 5. The event gate: only a pull request reads the compare record.
if mut=$(mutate 'event gate' "$JUDGE_STEP" \
    's/if \[ "\$EVENT_NAME" != pull_request \]; then/if false; then/'); then
    mutation_allows 'dropping the event gate lets a push be judged on a compare record' \
        EVENT_NAME=push COMPARE_JSON="$FIXES" ALERTS_JSON="$FASTURI" DEFERRALS_PATH=/nonexistent.yml \
        SEVERITY_FLOOR=high FAIL_ON_DEVELOPMENT=true GITHUB_STEP_SUMMARY=/dev/null
else
    failed=1
fi

# 6. The shape check in the compare read: without it an added version with no
# vulnerabilities list is recorded as a diff the verdict would trust.
if mut=$(mutate 'compare shape check' "$COMPARE_STEP" \
    's/(\.vulnerabilities | type) == "array"/true/; s/all(\.vulnerabilities\[\];/all(.vulnerabilities[]?;/'); then
    s=$(scenario 200 '[{"change_type":"added","manifest":"package-lock.json","name":"fast-uri","version":"3.1.5"}]')
    ( cd "$workdir" && env PATH="$curlbin:$PATH" STUB_DIR="$s" GITHUB_TOKEN=stub GITHUB_REPOSITORY=Abhijeet34/stub \
        GITHUB_API_URL=https://api.invalid RUNNER_TEMP="$s/tmp" BASE_SHA=aaaa HEAD_SHA=bbbb \
        COMPARE_JSON="$s/tmp/compare-record.json" bash -e "$mut" ) >/dev/null 2>&1
    if jq -e 'has("changes")' "$s/tmp/compare-record.json" >/dev/null 2>&1; then
        printf 'ok   %s\n' 'dropping the shape check records an unlisted re-add as a trusted diff'
    else
        printf 'FAIL %s\n' 'the mutated compare read still refuses, so the shape check has no teeth'
        failed=1
    fi
else
    failed=1
fi

((failed)) || printf '\ndependency-advisory gate: every property pinned\n'
exit $failed
