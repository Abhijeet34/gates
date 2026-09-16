#!/usr/bin/env bash
# Regression tests for the step body inside
# .github/workflows/shared-watermark-scan.yml.
#
#   .ci/test-watermark-scan-workflow.sh
#
# Same reason as the other four shared-workflow suites: the logic lives inline
# in YAML where no linter reaches it, and callers reference @main with no digest
# pin - a broken body is live in every caller at once.
#
# Run under `bash -e`, the way the runner runs it (`bash -e {0}`). That is not a
# detail: under -e a failing command SUBSTITUTION aborts on the assignment, so
# an `rc=$?` on the following line never runs, and a `grep` that legitimately
# matches nothing takes the step down. Four of test-secret-scan.sh's cases were
# passing under plain bash and failing under -e.
#
# The properties, in the order that matters:
#   1. a tree carrying detective provenance FAILS, naming the remedy,
#   2. a clean tree PASSES,
#   3. every way the scan can fail to establish a verdict REFUSES - a missing
#      scanner, a missing allowlist, an empty file list, a scanner that returns
#      no per-path verdict,
#   4. each of those guards, deleted, then wrongly allows an input it refuses
#      today - a property with no such case is a property nothing measures,
#   5. the scanner is the one checked out from this workflow's own repository,
#      never one the caller's tree carries - the tree under judgement cannot
#      also supply the judge.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

WORKFLOW=.github/workflows/shared-watermark-scan.yml
SCANNER_SRC=system-maintenance/git-hooks/watermark-scan.py
ALLOW_SRC=system-maintenance/git-hooks/watermark-allow.conf
[ -r "$WORKFLOW" ] || { echo "test-watermark-scan-workflow: cannot read $WORKFLOW" >&2; exit 2; }
command -v yq >/dev/null 2>&1 || {
    echo "test-watermark-scan-workflow: yq is not installed (brew install yq)" >&2
    exit 2
}
command -v exiftool >/dev/null 2>&1 || {
    echo "test-watermark-scan-workflow: exiftool is not installed (brew install exiftool)" >&2
    exit 2
}

workdir=$(mktemp -d) || exit 2
trap 'rm -rf "$workdir"' EXIT
failed=0

# A fixture repository must not inherit this machine's git config: core.hooksPath
# there runs the global hooks inside the fixture.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; failed=1; }

# --- extract the step body ----------------------------------------------------

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

STEP="$workdir/scan.sh"
extract "$WORKFLOW" 'Scan the working tree' "$STEP"

# The paths the step reads, taken from the workflow rather than restated here,
# so every case below runs the scanner location the runner would.
JOB=.jobs.watermark-scan
SCANNER_PATH=$(yq -r "$JOB.env.SCANNER" "$WORKFLOW")
ALLOW_PATH=$(yq -r "$JOB.env.ALLOWLIST" "$WORKFLOW")
CHECKOUT_DIR=$(yq -r "$JOB.steps[] | select(.with.repository == \"\${{ job.workflow_repository }}\") | .with.path" "$WORKFLOW")
CHECKOUT_REF=$(yq -r "$JOB.steps[] | select(.with.repository == \"\${{ job.workflow_repository }}\") | .with.ref" "$WORKFLOW")
if [ -n "$CHECKOUT_DIR" ] && [ "$CHECKOUT_DIR" != null ] \
    && [ "$CHECKOUT_REF" = '${{ job.workflow_sha }}' ] \
    && [ "${SCANNER_PATH#"$CHECKOUT_DIR"/}" != "$SCANNER_PATH" ] \
    && [ "${ALLOW_PATH#"$CHECKOUT_DIR"/}" != "$ALLOW_PATH" ]; then
    pass "scanner and allowlist resolve inside the checkout of this workflow's own repository at its own commit"
else
    fail "scanner ($SCANNER_PATH) or allowlist ($ALLOW_PATH) is not read from the job.workflow_repository checkout at job.workflow_sha (path '$CHECKOUT_DIR', ref '$CHECKOUT_REF')"
fi
if [ "$(yq -r '.on.workflow_call.inputs // {} | keys | length' "$WORKFLOW")" = 0 ]; then
    pass "no input lets a caller name its own scanner"
else
    fail "the workflow takes inputs, so a caller can point the scan at a scanner of its choosing"
fi

# Extraction is the one failure that makes every case below pass vacuously, and
# the awk stops at the first under-indented line - so a continuation written at
# column 2 inside a multi-line shell string truncates the body silently.
# Asserted at BOTH ends, which is the correction test-guard-generated-files.sh
# had to make.
for marker in 'COULD NOT ESTABLISH' 'scan did not complete'; do
    if grep -qF "$marker" "$STEP"; then
        pass "the extracted body contains $marker"
    else
        fail "the extracted body is missing $marker - extraction truncated"
    fi
done

# --- a repository to run it against -------------------------------------------

make_repo() { # <dir> ; echoes the path
    local repo="$workdir/$1"
    git -c init.defaultBranch=main init --quiet "$repo"
    printf 'plain prose\n' >"$repo/README.md"
    git -C "$repo" add -A
    # The scanner checkout sits in the caller's working tree untracked, as
    # actions/checkout leaves it, so it is added after the seed commit.
    mkdir -p "$(dirname "$repo/$SCANNER_PATH")" "$(dirname "$repo/$ALLOW_PATH")"
    cp "$SCANNER_SRC" "$repo/$SCANNER_PATH"
    cp "$ALLOW_SRC" "$repo/$ALLOW_PATH"
    git -C "$repo" -c user.name=t -c user.email=t@invalid commit --quiet -m seed
    printf '%s' "$repo"
}

run_step() { # <repo> [env assignments...]
    local repo="$1"; shift
    ( cd "$repo" && env SCANNER="$SCANNER_PATH" \
        ALLOWLIST="$ALLOW_PATH" "$@" bash -e "$STEP" ) 2>&1
}

# The runner's xargs is GNU, which maps any invocation exit in 1-125 to its own
# exit 123 rather than passing it through - so the scanner's findings exit (1)
# never reaches the step's `case $rc in ... 1)` as a literal 1 there. This
# machine's xargs is BSD and passes 1 straight through, so the case above would
# pass here whether or not the step actually handles the GNU remap. This stub
# reproduces the GNU exit-status mapping so that gap is measured rather than
# assumed.
make_gnu_xargs_stub() { # echoes a directory holding an xargs on PATH
    local dir="$workdir/gnu-xargs-stub"
    mkdir -p "$dir"
    cat >"$dir/xargs" <<'STUB'
#!/usr/bin/env bash
set -uo pipefail
n=1
cmd=()
while [ $# -gt 0 ]; do
    case "$1" in
        -0) shift ;;
        -n) n="$2"; shift 2 ;;
        *) cmd=("$@"); break ;;
    esac
done
args=()
while IFS= read -r -d '' a; do args+=("$a"); done
rc=0
batch=()
run_batch() {
    "${cmd[@]}" "$@"
    local r=$?
    if [ "$r" -ge 1 ] && [ "$r" -le 125 ]; then rc=123
    elif [ "$r" -gt 0 ]; then rc="$r"
    fi
}
for a in "${args[@]}"; do
    batch+=("$a")
    if [ "${#batch[@]}" -ge "$n" ]; then run_batch "${batch[@]}"; batch=(); fi
done
[ "${#batch[@]}" -gt 0 ] && run_batch "${batch[@]}"
exit "$rc"
STUB
    chmod +x "$dir/xargs"
    printf '%s' "$dir"
}

# 1. a clean tree passes.
repo=$(make_repo clean)
out=$(run_step "$repo"); rc=$?
if [ $rc -eq 0 ] && printf '%s' "$out" | grep -q 'no detective provenance found'; then
    pass "a clean tree passes and says how many files it read"
else
    fail "a clean tree did not pass (rc=$rc): $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
fi

# 2. a marked file fails, and the message names the bypass and the remedy.
repo=$(make_repo marked)
python3 - "$repo" <<'PY'
import sys, os
open(os.path.join(sys.argv[1], "notes.md"), "w", encoding="utf-8").write(
    "ships" + chr(0x200B) + "friday\n"
)
PY
git -C "$repo" add -A
git -C "$repo" -c user.name=t -c user.email=t@invalid commit --quiet -m marked
out=$(run_step "$repo"); rc=$?
if [ $rc -eq 1 ] && printf '%s' "$out" | grep -q 'DETECTIVE PROVENANCE'; then
    pass "a tree carrying detective provenance fails"
else
    fail "a marked tree did not fail (rc=$rc): $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
fi
if printf '%s' "$out" | grep -q 'no-verify' && printf '%s' "$out" | grep -q 'clean <path>'; then
    pass "and the message names both the bypass it caught and the remedy"
else
    fail "the failure message names neither the bypass nor the remedy"
fi

# 2b. the same marked tree, scanned through a stand-in for the runner's real
# (GNU) xargs, must still land in the findings branch even though its rc
# arrives as 123 rather than 1.
stubdir=$(make_gnu_xargs_stub)
out=$(run_step "$repo" "PATH=$stubdir:$PATH"); rc=$?
if [ $rc -eq 1 ] && printf '%s' "$out" | grep -q 'DETECTIVE PROVENANCE'; then
    pass "a tree carrying detective provenance fails under GNU xargs' 123 remap too"
else
    fail "under the GNU xargs stub, a marked tree did not fail (rc=$rc): $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
fi

# 2c. a scanner that dies (exit 2, e.g. exiftool missing) is a could-not-
# establish refusal, never a findings report. This is the case the naive
# `case $rc in 1|123)` on xargs' own remapped exit code got wrong: GNU xargs
# maps a die()/refuse() exit indistinguishably from the findings exit, so a
# genuine "nothing was established" would have printed "DETECTIVE PROVENANCE"
# on the runner that matters.
repo=$(make_repo dies)
printf 'import sys\nsys.exit(2)\n' >"$repo/$SCANNER_PATH"
out=$(run_step "$repo"); rc=$?
if [ $rc -eq 1 ] && printf '%s' "$out" | grep -q 'COULD NOT ESTABLISH' \
    && ! printf '%s' "$out" | grep -q 'DETECTIVE PROVENANCE'; then
    pass "a scanner that dies (exit 2) is reported as could-not-establish, not findings"
else
    fail "a dying scanner was not reported correctly (rc=$rc): $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
fi

# 2d. the same, driven with the GNU xargs stand-in on PATH - the step no
# longer routes any scanner invocation through xargs at all, so this pins
# that a stubbed xargs cannot change the verdict either way.
out=$(run_step "$repo" "PATH=$stubdir:$PATH"); rc=$?
if [ $rc -eq 1 ] && printf '%s' "$out" | grep -q 'COULD NOT ESTABLISH' \
    && ! printf '%s' "$out" | grep -q 'DETECTIVE PROVENANCE'; then
    pass "a dying scanner is still could-not-establish under the GNU xargs stub"
else
    fail "under the GNU xargs stub, a dying scanner was not reported correctly (rc=$rc): $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
fi

# 3. the ways it can fail to establish a verdict, each a refusal.
repo=$(make_repo noscanner)
rm -f "$repo/$SCANNER_PATH"
out=$(run_step "$repo"); rc=$?
if [ $rc -eq 1 ] && printf '%s' "$out" | grep -q 'nothing scanned this tree'; then
    pass "a scanner checkout that produced no scanner is refused, not passed"
else
    fail "a missing scanner did not refuse (rc=$rc)"
fi

repo=$(make_repo noallow)
rm -f "$repo/$ALLOW_PATH"
out=$(run_step "$repo"); rc=$?
if [ $rc -eq 1 ] && printf '%s' "$out" | grep -q 'no allowlist was applied'; then
    pass "a missing allowlist is refused, not silently skipped"
else
    fail "a missing allowlist did not refuse (rc=$rc)"
fi

repo="$workdir/empty"
mkdir -p "$(dirname "$repo/$SCANNER_PATH")" "$(dirname "$repo/$ALLOW_PATH")"
git -c init.defaultBranch=main init --quiet "$repo"
cp "$SCANNER_SRC" "$repo/$SCANNER_PATH"
cp "$ALLOW_SRC" "$repo/$ALLOW_PATH"
out=$(run_step "$repo"); rc=$?
if [ $rc -eq 1 ] && printf '%s' "$out" | grep -q 'listed no tracked file'; then
    pass "a checkout git lists nothing in is refused rather than reported clean"
else
    fail "an empty file list did not refuse (rc=$rc)"
fi

# A scanner that exits 0 having read nothing is the empty green this whole gate
# is about, so the per-path verdict count is what catches it.
repo=$(make_repo silent)
printf 'import sys\nsys.exit(0)\n' >"$repo/$SCANNER_PATH"
out=$(run_step "$repo"); rc=$?
if [ $rc -eq 1 ] && printf '%s' "$out" | grep -q 'no per-path verdict'; then
    pass "a scanner that exits 0 having printed nothing is refused"
else
    fail "a silent scanner was reported clean (rc=$rc): $(printf '%s' "$out" | tail -1)"
fi

# 5. the caller's own tree cannot supply the judge. The caller here carries a
# scanner at the path callers used to be told to carry it, rigged to report
# every file clean; the tree also holds a real mark. The step, run with the
# paths the workflow declares, must still find it - and the same step pointed
# at the caller's copy must NOT, which is what shows this case can fail.
repo=$(make_repo caller_scanner)
mkdir -p "$repo/system-maintenance/git-hooks"
cat >"$repo/system-maintenance/git-hooks/watermark-scan.py" <<'PY'
import sys
for p in sys.argv[2:]:
    print(f"watermark-scan: clean {p}")
PY
python3 - "$repo" <<'PY'
import sys, os
open(os.path.join(sys.argv[1], "notes.md"), "w", encoding="utf-8").write(
    "ships" + chr(0x200B) + "friday\n"
)
PY
git -C "$repo" add -A
git -C "$repo" -c user.name=t -c user.email=t@invalid commit --quiet -m rigged
out=$(run_step "$repo"); rc=$?
if [ $rc -eq 1 ] && printf '%s' "$out" | grep -q 'DETECTIVE PROVENANCE'; then
    pass "a scanner carried in the caller's tree is not the one run"
else
    fail "the caller's rigged scanner decided the verdict (rc=$rc): $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
fi
if ( cd "$repo" && env SCANNER=system-maintenance/git-hooks/watermark-scan.py \
    ALLOWLIST="$ALLOW_PATH" bash -e "$STEP" ) >/dev/null 2>&1; then
    pass "and reading the scanner from the caller's tree would have passed that tree"
else
    fail "the caller-tree control did not pass, so case 5 proves nothing"
fi

# --- 4. mutation cases --------------------------------------------------------
#
# Each deletes one guard and re-runs the input that guard exists for. Exactly
# one of them flips the verdict, and that asymmetry is the finding: the
# per-path-verdict count is the ONLY thing standing between a scanner that
# exits 0 having read nothing and a green tick. The other guards are defended a
# second time by the scanner's own exit status, so what their deletion costs is
# the actionable message, not the refusal - and a mutation case that claimed
# otherwise would be asserting something untrue.
mutate() { # label sed-program repo expected-outcome{allows|still-refuses}
    local label="$1" program="$2" repo="$3" want="$4"
    local mut="$workdir/mut.sh"
    sed "$program" "$STEP" >"$mut"
    if cmp -s "$mut" "$STEP"; then
        fail "mutation '$label' changed nothing - the guard it targets has moved"
        return
    fi
    local got=still-refuses
    if ( cd "$repo" && env SCANNER="$SCANNER_PATH" \
        ALLOWLIST="$ALLOW_PATH" bash -e "$mut" ) >/dev/null 2>&1; then
        got=allows
    fi
    if [ "$got" = "$want" ]; then
        pass "removing the $label guard $want, as recorded"
    else
        fail "removing the $label guard $got, expected $want"
    fi
}

repo=$(make_repo mut_noscanner)
rm -f "$repo/$SCANNER_PATH"
mutate "missing-scanner" '/nothing scanned this tree/d' "$repo" still-refuses

repo=$(make_repo mut_empty)
rm -rf "$repo/.git" "$repo/README.md"
git -c init.defaultBranch=main init --quiet "$repo"
mutate "empty-file-list" '/listed no tracked file/d' "$repo" still-refuses

repo=$(make_repo mut_silent)
printf 'import sys\nsys.exit(0)\n' >"$repo/$SCANNER_PATH"
mutate "per-path-verdict" '/no per-path verdict/d' "$repo" allows

if [ "$failed" -ne 0 ]; then
    echo "test-watermark-scan-workflow: FAILED" >&2
    exit 1
fi
echo "ok watermark-scan CI backstop refuses what it cannot establish"
