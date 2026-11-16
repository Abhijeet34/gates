# gates-issue-17 — the fold trusts records that an unconfined row could have written

Backlog id `factory-p3a-sr3f3-fold-unconfined` (P3a-SR3F3, finding sr3-4, severity S2, latent).
Scout run against `main` at `1739f4557dad8e59583bda236eb3b380b25a97ea`. This report is the only change on the branch. No code was changed.

## What I could not do

- **I could not read the source finding.** `data/factory-p3a-sr3-security-rereview/report.md` lives in the private repository, which this session cannot reach. I rebuilt the defect from the backlog title and the code, and reproduced it (below). Before landing a fix, check this against section 6 of that report.
- **`README.md:136` no longer points at the claim.** At this commit, line 136 is the Dependabot paragraph under "Bumping", so the README has moved since the review. The sentence the title must mean is in "The verdict cache", at **`README.md:149`**: *"That costs the cache, never a verdict: every selected row runs, and the fold is unchanged."* The demonstrations below show that sentence is false. (This is an inference. The private report would confirm it.)
- **I ran nothing on a GitHub runner.** Both demonstrations ran here, on a host with neither `bwrap` nor `sandbox-exec` (`which bwrap sandbox-exec` prints nothing). That is the same state `confinement()` reaches on the stock `ubuntu-24.04` runner the shadow bootstrap uses: README.md:1738 says *"nothing installed"*, and README.md:148 names Ubuntu 24.04's AppArmor userns limit. I did not run `.ci/check.sh` or `.ci/test-gate-runner.py`, because nothing was changed.

## The defect

`run/gate.py` protects **reuse** from unconfined rows but does not protect the **fold**:

- `run/gate.py:560-563`: `run` drops every prior record when any of them says `confined: false`, so nothing is reused. This is covered by `unconfined_run_taints_the_store` (`.ci/test-gate-runner.py:379`).
- `run/gate.py:463-469` (`fold`): reads every line of `records.jsonl`, takes the **latest** record per gate for H, and never looks at `confined`. Nothing in `.ci/test-gate-runner.py` tests the fold over a store that an unconfined row wrote.

On a host where no sandbox starts, every row runs with write access to the store (`confinement()` returns `[]`, `run/gate.py:247-255`). Any row can therefore append a `pass` for a gate that has already run, or rewrite the file. The fold then reads that forgery as the verdict. In the shadow job the row finds the store through the inherited `SHADOW_STORE` environment variable (`shared-gate-shadow.yml`, step "Run the gate runner in shadow"). `run/gate-shadow.sh:24-41` takes the shadow verdict from `fold.txt` alone, and `run_status` only counts when it is greater than 1, so the forged fold becomes `verdict=clear`.

The defect is latent: the shadow job gates nothing (README.md:1727-1731). But P3a-M6 reads `verdict=` and the uploaded `records.jsonl` as its 14-day evidence (README.md:1735-1736). A forged `clear` would count as agreement on a head where a real job failed, which is the exact case M6's acceptance line forbids. Hence "land before P3a-M6 puts any of these rows in shadow".

## Evidence

### Demonstration 1: a later row appends a pass for an earlier one

Fixture caller (`GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null`, one commit), run through the real `run/gate-shadow.sh`:

```toml
[[gate]]
id = "test"
inputs = ["src/**"]
run = "exit 1"

[[gate]]
id = "lint"
inputs = ["src/**"]
run = '''printf '{"schema":1,"gate":"test","head":"%s","verdict":"pass","confined":true,"reused_from":null}\n' "$(git rev-parse HEAD)" >> "$SHADOW_STORE/records.jsonl"'''
```

```
$ GATES_DIR=/home/user/gates SHADOW_STORE=$S/store bash -e run/gate-shadow.sh
gate: no sandbox keeps the rows from the store (...), so nothing is reused
fail test
pass lint
pass test                      <- fold
pass lint
fold: 2 selected at 0d414f673b66, every blocking gate passed
run_status=1
fold_status=0
verdict=clear
```

`run` printed `fail test`, but the fold passed it. The runner's own records still carry `"confined": false`, so a fold that **refused** when any record of H is unconfined would catch this case.

### Demonstration 2: a detached process erases the marks as well

Same `test` row. This time `lint` is `setsid python3 .github/scrub.py </dev/null >/dev/null 2>&1 &`. The script waits until the runner has written `lint`'s own record, then rewrites `records.jsonl` in place: every record gets `confined: true`, and `test` gets `verdict: pass`. Because the child closes the row's stdout pipe, `communicate()` (`run/gate.py:446`) returns at once and the child outlives the row.

```
fail test
pass lint
pass test
pass lint
fold: 2 selected at 78f7f99d7d90, every blocking gate passed
run_status=1
fold_status=0
verdict=clear
--- records.jsonl afterwards (the uploaded artifact)
{... "confined": true, "detail": "exit 1", ... "gate": "test", ... "verdict": "pass" ...}
{... "confined": true, "detail": "exit 0", ... "gate": "lint", ...}
```

Here **no** record says `confined: false`, so a fold that only checks the mark would also pass. The artifact M6 reads no longer shows any trace of the unconfined run. The same reach covers `run.txt`, `fold.txt` and `shadow-verdict.txt`, which `gate-shadow.sh` writes inside the store, and the `gates` checkout at `$RUNNER_TEMP/gates`. README.md:153 already concedes the general point: *"a store shared with unconfined tools carries no provenance"*.

## What follows for the fix

The goal offers two fixes: the fold "refuses" or "reads only runner-printed records". Demonstration 2 decides between them. Once an unconfined row has run, nothing on disk can be trusted, including the `confined` marks. The only trustworthy channel is the `run` process itself: its memory and its own stdout. Rows do not hold that stdout, because their output goes into a pipe that `run` reads.

## Recommendation

1. **`run` folds in-process and prints the fold.**
   After the loop at `run/gate.py:565-595`, fold over the records this `run` produced (held in a list, not re-read from the store), plus the store's records only when the run was confined. Print the same `pass`/`FAIL`/`shadow` lines and the `fold:` line to stdout, and exit with the fold's verdict. On an unconfined run, a `review`/`docs` row that `run` cannot vouch for (`await ...`) folds as missing, so the fold refuses instead of trusting the store.
2. **`gate.py fold` refuses an unconfined store.**
   When any record for H says `confined: false`, refuse with exit 2 (undecided) and name the row. This catches demonstration 1 and every case where the marks survive. It is not sufficient alone (demonstration 2), so it is defence in depth for a standalone `fold` invocation.
3. **`run/gate-shadow.sh` reads the verdict from `run`'s stream, not from files in the store.**
   Capture `run`'s stdout in a shell variable or pipe and derive `verdict=` from it. Echo the full lines to the job log first, which the rows cannot rewrite, before writing any copy into the store. Drop the separate `fold` call, or make it the check of point 2. README "The shadow job" should then say that M6 reads the verdict from the step log. For an unconfined run, the uploaded `records.jsonl` is not evidence.
4. **Correct `README.md:149`** (the sentence the backlog cites as `:136`), and the matching claim in the docstring at `run/gate.py:47-55` ("only the runner writes a record").
   Proposed text: *"An unconfined run costs the cache and the store's word: any row could have written any line in it, or left a process that rewrites it after the row exits, so the fold on that host reads only what `run` itself recorded in this run and printed, and `gate.py fold` refuses a store holding an unconfined record."*
5. **Tests in `.ci/test-gate-runner.py`, run with confinement forced off** (for example a `GATE_NO_SANDBOX` test hook, or a `PATH` without `bwrap`), each with a mutation anchor as the suite requires (AGENTS.md):
   - `unconfined_forged_pass_never_folds`: demonstration 1. The fold and `run` both refuse.
   - `unconfined_scrubbed_store_never_folds`: demonstration 2. `run`'s printed fold refuses, and the shadow verdict is not `clear`.
   - A `gate-shadow` case in `.ci/test-gate-shadow.py`: a forging row leaves `verdict` other than `clear`.

   These cases need the opposite of `REUSE_CASES`, which runs only *with* a sandbox. They must run where no sandbox starts, or force that state, never be skipped there.
6. **Also, separately: give the shadow job a sandbox.**
   The shadow bootstrap could install bubblewrap and lift the AppArmor limit, as `ci.yml:48` does for the `shell` job, so that callers' rows run confined during M6. That is a workflow-file change, so it reaches pinned callers only through a bootstrap pin bump (AGENTS.md).

### Next to this finding, not verified, worth a look in the same change

Even a **confined** row runs under `bwrap --dev-bind / / --ro-bind STORE STORE` (`run/gate.py:247-248`), so only the store is read-only. A row could therefore rewrite `$GATES_DIR/run/gate.py` (in `$RUNNER_TEMP`, outside the store) before `gate-shadow.sh` starts the separate `fold` process from that file. Folding inside `run` (point 1) closes this too, because `run` has already loaded its code. I did not demonstrate it, because there is no `bwrap` here.

### Process notes

- `run/gate.py` is on `run/permissions-diff.py`'s protected surface (`run/permissions-diff.py:487`). The fix is a class-2 change once a caller promotes that gate out of shadow; today it is not promoted.
- `.ci/gate-registry.toml` already has rows for `run/gate.py` (around lines 436 and 1131) and for `gate-shadow` (around line 2132). A new control needs its red/clean inputs kept passing under `.ci/check.sh gates-proof`.
