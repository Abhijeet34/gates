#!/usr/bin/env bash
# Scan the caller's tracked tree for detective provenance, with the scanner and
# allowlist from the gates checkout ($SCANNER, $ALLOWLIST), never the caller's.
set -uo pipefail

fail() {
  echo "::error::$1 COULD NOT ESTABLISH that this tree is clean." >&2
  exit 1
}

# A scanner checkout that produced no scanner is refused: a missing
# scanner reported as a pass is the empty green this file exists to
# prevent.
[ -r "$SCANNER" ] || fail "$SCANNER is missing, so nothing scanned this tree."
[ -r "$ALLOWLIST" ] || fail "$ALLOWLIST is missing, so no allowlist was applied."

# Every tracked path, NUL-delimited, because a filename is bytes and
# core.quotePath would hand the scanner an escaped name that matches
# nothing. Counted first: a scan that read no file must not report a
# clean tree, which is the same witness the secret scan keeps on its
# commit count.
expected=$(git -c core.quotePath=false ls-files -z | tr -cd '\0' | wc -c | tr -d ' ')
[ "${expected:-0}" -gt 0 ] || fail "git listed no tracked file."

# Batches are driven from here rather than from xargs, because GNU
# xargs (this runner's /usr/bin/xargs) does not pass a batch's own
# exit status through: it remaps ANY invocation exit in 1-125 to its
# own 123, so the scanner's findings exit (1) and its die()/refuse()
# exits (also non-zero, e.g. 2 for a missing exiftool) would arrive
# indistinguishable. A gate whose whole posture is to refuse what it
# cannot establish must not let that remap turn a genuine "could not
# establish" into a false "findings" report - so each batch is
# invoked directly and its own exit status is read straight from the
# python3 process, with no xargs in between.
out=""
overall=0
batch=()
run_batch() {
  local batch_out batch_rc=0
  batch_out=$(WATERMARK_ALLOW="$ALLOWLIST" python3 "$SCANNER" inspect "$@" 2>&1) || batch_rc=$?
  out="$out$batch_out"$'\n'
  if [ "$batch_rc" -ne 0 ] && [ "$batch_rc" -ne 1 ]; then
    fail "the scan did not complete (scanner exit $batch_rc)."
  fi
  [ "$batch_rc" -eq 1 ] && overall=1
  return 0
}
while IFS= read -r -d '' path; do
  batch+=("$path")
  if [ "${#batch[@]}" -ge 200 ]; then
    run_batch "${batch[@]}"
    batch=()
  fi
done < <(git -c core.quotePath=false ls-files -z)
[ "${#batch[@]}" -gt 0 ] && run_batch "${batch[@]}"

printf '%s\n' "$out"

# One summary line per path, whatever the verdict, so this counts what
# was actually read rather than trusting the exit status.
#
# `|| true` is load-bearing and not defensive noise: the runner's shell
# is `bash -e {0}`, and a `grep -c` that matches nothing exits 1, which
# aborts on the ASSIGNMENT. Without it a scanner that printed nothing -
# exactly the case this line exists to catch - killed the step before
# this guard was reached, so it refused with no message at all. Found
# by running the extracted body under -e, which is the whole reason
# .ci/test-watermark-scan-workflow.sh does that.
scanned=$(printf '%s\n' "$out" | grep -cE '^watermark-scan: (clean|VERIFIED|findings in|[0-9]+ (allowed|noted))' || true)
[ "$scanned" -gt 0 ] || fail "the scanner produced no per-path verdict for $expected tracked file(s)."

if [ "$overall" -eq 0 ]; then
  echo "scanned $expected tracked file(s), no detective provenance found"
else
  echo "::error::DETECTIVE PROVENANCE is in this repository's tree. The local pre-push gate did not see this push - --no-verify, a machine without the hook deployed, or a commit made on GitHub itself. Remedy: watermark-scan.py clean <path>, or an allowlist entry naming its reason." >&2
  exit 1
fi
