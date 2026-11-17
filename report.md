# P3a-SR3F3: `gate.py fold` reads records an unconfined row could have forged

Task `gates-issue-17-2`, backlog id `factory-p3a-sr3f3-fold-unconfined`, scouted at `cc7e3c44cd125c2335b835a24791b4dab63db650` (`main`).
This is a scout report: no change to `run/gate.py` or `README.md` is pushed; the prototype patch below was tested in the working tree and reverted.

## Not done, and caveats first

- **I did not read the source report** (`data/factory-p3a-sr3-security-rereview/report.md`, section 6). It lives in the private repository and is not in this checkout, so the finding was rebuilt from the code and reproduced directly.
- **`README.md:136` on this commit is not the false claim.** Line 136 here is the Dependabot paragraph of "Bumping". The sentence the finding is about is **`README.md:149`** in "The verdict cache": *"That costs the cache, never a verdict: every selected row runs, and the fold is unchanged."* The line number most likely comes from an earlier commit. Check that the source report's line 136 means this sentence before the fix lands.
- **I did not run the shadow job on a real `ubuntu-24.04` runner.** The claim that it runs unconfined there comes from the workflow and README (see F3). It was not measured.
- **I did not land a fix.** The prototype passes `.ci/test-gate-runner.py` and `.ci/test-gate-shadow.py` locally. It has no red/clean case of its own, and the `.ci/gate-registry.toml` work it would need was not done.

## What the defect is

`run` treats a store that any unconfined row wrote as tainted and reuses nothing from it (`run/gate.py:560-563`). It keeps that rule because such a row "could have written any other line" (`README.md:145`). `fold` has no matching rule. It reads every record whose `head` equals H, the last one per gate wins, and it never looks at `confined` (`run/gate.py:465-471`):

```python
for rec in recs:
    if rec["head"] == head:
        latest[rec["gate"]] = rec
```

An unconfined row can append `{"gate": X, "head": H, "verdict": "pass"}` for any gate X. That includes:

- a check row that ran before it and failed,
- a `review`/`docs` row, whose verdict only ever comes from the store,
- the forging row itself, if it then kills the runner so its genuine `confined: false` record is never written.

The fold then passes H. `README.md:149` says the opposite.

The risk is latent today (S2), and two things keep it that way:

- Every row in `.github/gates.toml` is in shadow (`shadow_since = 2026-10-24`).
- `gate-shadow.sh` never gates.

It becomes live when P3a-M6 puts these rows in shadow on callers and any row is promoted, or when anything reads `fold.txt`'s verdict as authoritative. The M6 comparison reads exactly that verdict.

## Evidence

Reproduction fixtures were built in the session scratchpad (`GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null`). Unconfined runs were forced by running `gate.py run` with a `PATH` that has no `bwrap`, so `confinement()` returns `[]` (`run/gate.py:247-250`).

### F1: a later unconfined row overwrites an earlier `fail` and forges a review `pass`

The registry has three rows: `victim` (`run = "exit 1"`), then `forger`, then `sec-review` (`kind = "review"`). `forger` appends `pass` lines for `victim` and `sec-review` at HEAD, each marked `"confined": true`.

```
$ gate.py run --base $B --store $STORE        # no bwrap on PATH
gate: no sandbox keeps the rows from the store (...), so nothing is reused
fail victim
pass forger
await sec-review: its review verdict is recorded
run exit 1
$ gate.py fold --base $B --store $STORE
pass victim
pass forger
pass sec-review
fold: 3 selected at 30f063be7dac, every blocking gate passed
fold exit 0
```

`run` says `fail victim` and exits 1, but the fold passes every gate, including a review that never ran.

### F2: the forging row kills the runner, so the store holds no `confined: false` line at all

Here `forger` is first. It appends `pass` for all three gates and then runs `kill -9 $PPID`.

```
run exit 137
$ gate.py fold --base $B --store $STORE
pass forger
pass victim
pass sec-review
fold: 3 selected at 0173a7f30167, every blocking gate passed
fold exit 0
$ grep -c 'confined.:.false' records.jsonl
0
```

So **checking the head's records for `confined: false` alone is not enough.** On an unconfined host the row can erase or prevent every mark in the store. The only fact that can't be forged is outside the store: whether this host can confine the store at all. The `run` exit status is also outside the store, and the row can't forge it, only turn it non-zero.

### F3: the shadow job is exactly the unconfined case

- `.github/workflows/shared-gate-shadow.yml:23` runs on `ubuntu-24.04` and installs nothing: `grep bwrap\|apparmor` finds nothing in it.
- `ci.yml:48-52` and `ci.yml:156-160` install bubblewrap and lift `kernel.apparmor_restrict_unprivileged_userns` for their own jobs.
- `README.md:148` says that, without this, the host "runs every row unconfined".
- `run/gate-shadow.sh` keeps `run.txt` and `fold.txt` inside `$SHADOW_STORE`, which is also the store, so those files can be written too.

### F4: "runner-printed" output is not safe by itself either

An unconfined child can write into its parent's stdout through `/proc/$PPID/fd/1` (same uid; this container has no Yama):

```
$ out=$(python3 -c 'import subprocess; subprocess.run(["bash","-c","echo FORGED > /proc/$PPID/fd/1"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, start_new_session=True); print("runner line")'); echo "$out"
FORGED
runner line
```

A fold that reads the runner's printed lines is sound only under three conditions:

1. it requires exactly one verdict line per selected gate and refuses a duplicate,
2. it treats a `run` exit status above 1 as undecided,
3. it keeps that output outside the store.

A row can add lines but can't remove the runner's own line. Killing the runner shows up in the exit status.

### Baseline

`python3 .ci/test-gate-runner.py` reports `gate runner tests passed` on `main`. None of its cases folds a store that an unconfined row wrote. The nearest cases are `unconfined_record_is_never_reused` and `unconfined_run_taints_the_store` (`.ci/test-gate-runner.py:370-386`), and they cover `run`'s reuse only.

## Recommendation

**1. `fold` refuses (exit 2, undecided) a store it can't trust.** Two controls, both needed (F1 and F2):

- (a) any record of H marked `confined: false` → `Undecided`;
- (b) when the store exists and `confinement(store)` returns `[]` on the folding host → `Undecided`.

I tested this prototype:

```diff
@@ def fold(rows, chosen, head, store):
     for rec in recs:
         if rec["head"] == head:
+            # A row that ran unconfined could have written any line for H, a
+            # `confined: true` one included, so no record of H can be read.
+            if rec.get("confined") is False:
+                raise Undecided(
+                    f"a record of {rec['gate']} at {head[:12]} ran unconfined, so no record of this head is trusted"
+                )
             latest[rec["gate"]] = rec
@@ def main():
+        # The store carries no mark a row on this host could not have erased.
+        # An absent store holds no record to forge, and folds to refusals alone.
+        if args.store.exists() and not confinement(args.store):
+            raise Undecided(
+                "no sandbox keeps the rows from the store on this host, so its records carry no provenance"
+            )
         ok, lines = fold(rows, chosen, head, args.store)
```

Results:

- F1's store: `gate: a record of victim at 30f063be7dac ran unconfined, so no record of this head is trusted`, exit 2.
- F1's and F2's stores folded without `bwrap`: `gate: no sandbox keeps the rows from the store on this host, ...`, exit 2.
- `.ci/test-gate-runner.py`: passed with the patch.
- `.ci/test-gate-shadow.py`: its cases `ok` with the patch, on a host with `bwrap`.

The `exists()` guard matters. Without it, `migration_change_selects_migration_gate` failed, because `bwrap --ro-bind` of a missing path does not start.

Limits of (b):

- It is a property of the host that folds. A store forged on an unconfined host and folded on a confined one passes (b) and is caught by (a) only if a genuine `confined: false` line survived. That is another reason to keep the store per host and per job, as the shadow job already does.
- An unconfined row with passwordless `sudo` (a GitHub runner) owns the host. No control in `gate.py` covers that, and the remedy is item 2.

Consider widening (a) from "of H" to "any record in the store", to match `run`'s rule at `run/gate.py:562`. A forged review record for another head H2 survives in a persisted store, because `run` never writes a review row's record. The backlog title asks only for the head scope, so this is a judgement call.

**2. Run the shadow job confined.** Give `shared-gate-shadow.yml` the same `apt-get install bubblewrap` and `sysctl kernel.apparmor_restrict_unprivileged_userns=0` step that `ci.yml` has. Otherwise, with item 1, every shadow head reads `verdict=undecided`, because `gate-shadow.sh` maps fold exit 2 to `undecided`, and the M6 comparison has no data.

Changing the YAML is a workflow-file change, so note two things before doing it:

- AGENTS.md says logic belongs in `run/`.
- The `sudo` step must stay within `.ci/test-workflow-policy.sh`.

If the step is not wanted, the alternative "reads only runner-printed records" route is to have `gate-shadow.sh` fold from `run`'s stdout under the three conditions in F4, with `run.txt` written outside `$SHADOW_STORE`. Review rows must then fold as undecided, because their verdicts exist only in the store.

**3. Correct `README.md:149`** (the "README.md:136" of the backlog). Suggested replacement:

> That costs the cache, and the fold too: a row that runs unconfined could have written any record in the store, so `fold` reads no record of a head that an unconfined row ran on, and none at all on a host whose sandbox will not start; it exits 2 (undecided) instead.

Add to "The shadow job" that the job installs bubblewrap for this reason. Also update the module docstring at `run/gate.py:53-55`, which says the fold "exits 0 only when every selected blocking gate's latest record for H is `pass`", to name the new refusal.

**4. Tests and registry.** Add two cases to `.ci/test-gate-runner.py` and to the mutation table near `.ci/test-gate-runner.py:714-722`, each red without its control:

- `unconfined_record_of_head_refuses_fold`, which is F1 with its anchor `if rec.get("confined") is False:` in `fold`;
- `unconfinable_host_refuses_fold`, which is F2 run with no `bwrap` on `PATH`, so it runs everywhere, macOS included, if `sandbox-exec` is also hidden.

Per AGENTS.md, moving a mutation's anchor text fails the suite until the anchor follows. The `gate-runner` row in `.ci/gate-registry.toml` already covers `run/gate.py`, so I don't expect a new row, but `gates-proof` should be re-run.
