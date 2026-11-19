# Re-file of `fix-p3a-gates-regression`: why it did not land, and the fix row

Run `r20261119T010801-76899`, scouted against `main` at `cc7e3c4`.

## What I could not do

- I could not read the private findings store or the P3a re-review report. So I could not check, one finding at a time, that each of the 19 findings (sr-1 to sr-3, sr-5 to sr-9, sr-13 to sr-15, sr-17, sr-19, sr-20, sr2-1, sr2-3, sr2-6, sr2-8, sr2-11) has a matching case. The suite on hand names its 37 cases by PR number and behaviour. It never names a finding id (`grep -nE "sr2?-[0-9]+" .ci/test-p3a-findings.py` finds nothing). The cancelled task asked for each case to be named by its finding id, so that mapping is still missing.
- `bash .ci/check.sh gates-proof` fails in this container on the `artifact-scan` row ("red input was not refused"). It fails the same way on plain `main` with none of my changes, so the cause is this container, not the suite. I did not check it any further.

## What I found

### 1. The regression in the captain's facts was already fixed on the old branch

PR #83 (`fac/fix-p3a-gates-regression`) has four commits. The captain's CI facts come from `ddec24b`, the third one. The fourth, `a24acb3` ("fix(ci): leave the pnpm fixtures' node_modules untracked, as installed"), is the fix the captain described. It makes the licence fixture write `node_modules/` to `.git/info/exclude` before `git add -A`, so node_modules is left untracked and #81's tracked-node_modules refusal no longer fires first:

```
@@ -525,6 +525,9 @@ def licence(files, policy=POLICY):
     run("git", "init", "-q", cwd=here)
+    # node_modules is installed, never tracked: the row refuses a tree that supplies it.
+    (here / ".git/info").mkdir(exist_ok=True)
+    (here / ".git/info/exclude").write_text("node_modules/\n")
     run("git", "add", "-A", cwd=here)
```

On PR #83's head `a24acb3`, the CI run 41366711621 (2026-11-02) passed `shell`, `python`, `gates proof`, `secret scan`, `record approval`, `checks`, `watermark` and `shadow`. The only red check was `advisories / dependency advisories`. Two critical `tinypool` advisories (GHSA-5gmw-xhrv-c9v3, GHSA-85c8-ppgw-ccpr) in `run/mutation-judge/package-lock.json` failed it. Those come from main's dependencies, not from this diff. Main has fixed them since in #90 (`709e950`, "override the judge tinypool to 2.1.2 for two advisories").

### 2. Why the fix did not land

- PR #83 was **closed without merging** on 2026-11-12, base `642c80c`.
- After that, the fix task failed twice at **launch** ("no cloud session was reported (signal: killed)"). A cloud session was never started, so the suite was never run again. Nothing in the test code caused the cancellation.

### 3. The suite still passes on current main

I copied the suite from `a24acb3` onto `main` (`cc7e3c4`) unchanged and ran the acceptance:

```
$ python3 .ci/test-p3a-findings.py
ok   pr58 pnpm-decoy-manifest-not-read: A package.json elsewhere in node_modules claiming a GPL package is MIT.
ok   pr58 pnpm-manifest-outside-tree-not-read: A pnpm store entry that is a symlink out of the tree is not read.
...
test-p3a-findings: 37 of 37 cases passed      (rc=0, 4.2 s)
```

- The two pnpm cases that had failed on the merge ref now pass against main's `run/licence.py`, which includes #81.
- `git apply --check` applies PR #83's `.ci/check.sh` hunk to main with an offset of 8. It adds `python3 .ci/test-p3a-findings.py || rc=1` to `check_shell`, after `test-critic-eval.py` (`.ci/check.sh:158` on main).
- `ruff format --check .ci/test-p3a-findings.py` reports the file as already formatted.
- `bash -e .ci/test-suite-coverage.sh` passes.
- With the suite wired in, `gates-proof`'s own checks all pass ("refuses a test that check.sh no longer runs" and the rest). Its one failure is the container-caused `artifact-scan` row described above.

## What I recommend

There is no residual risk to accept. The work exists and passes on main; it was never merged. File this fix row:

- **Text:** Land `.ci/test-p3a-findings.py` as it stands at gates `a24acb3` (PR #83's head, closed unmerged), plus its `.ci/check.sh` line in `check_shell`, on a branch from current `main`. Then add each case's P3a finding id (sr-1 … sr2-11) to its case name, from the private findings store. Keep the behaviour-only descriptions, and make sure every one of the 19 findings has at least one case.
- **Acceptance:** `python3 .ci/test-p3a-findings.py` (37 of 37 pass on `cc7e3c4` today).

Also consider: the `a24acb3` commit reached a closed PR. If the dispatcher had compared the branch head with the commit in the captain's facts, it would have found the fix already there instead of re-filing. That is a fac process note, outside this contract.
