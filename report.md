# refile-fix-sr2-9: why the fix did not land, and the fix row to re-file

## Could not do

- **Read the cancelled attempt.** Its head `5f97e43c6c38` is on no branch of this repository (`git fetch origin 5f97e43c6c38` → `couldn't find remote ref`), and no remote ref names `sr2`.
  I could not read the gate's two blocking verdicts or the private findings store, so I can't quote what the gate said.
  The cause below comes from re-running the same change against the gates on `main`, and I'm confident of it, but it is a reconstruction, not the recorded verdict.
- **Run the change on macOS.** This container is Linux with `bwrap`. `/Users/Shared` and `/Library/Caches` do not exist here, so only the `/var/tmp` part of the case ran.

## What I did

1. Read the jail: `run/review.py:111` (`SHARED_ROOTS`) and `confinement()` at `run/review.py:225-279`. The macOS path denies reads of each hidden root through SBPL. The bwrap path mounts a `--tmpfs` over each one, but only over roots that exist (`os.path.isdir`) on that host.
2. Ran the baseline: `python3 .ci/test-review.py` → `review tests passed`.
3. Prototyped the fix in the working tree. I added `"/var/tmp", "/Users/Shared", "/Library/Caches"` to `SHARED_ROOTS` and added a case, `reproduction_lists_no_shared_cache`, to `.ci/test-review.py`. It plants a file under each of the three roots that is writable on the host, lists and reads them from inside the jail, and wants the probe to be hidden and the finding downgraded. Its mutation entry swaps the widened tuple back to the old one.
4. Ran the acceptance:
   ```
   ok   reproduction_lists_no_shared_cache
   ok   without the shared cache roots hidden from a reproduction, reproduction_lists_no_shared_cache goes red: the reproducing command listed a shared cache root (/var/tmp): ...
   review tests passed
   ```
   The acceptance line `ok   reproduction_lists_no_shared_cache` is printed, so the change is technically easy and the regression test can be met.
5. Committed the prototype on a scratch commit and ran the protected-surface gate against it:
   ```
   $ python3 run/permissions-diff.py --base origin/main --head HEAD
   permissions-diff: run/review.py changes the review agent and its verifier's sandbox
   permissions-diff: refused: 1 change(s) to the protected surface; an automatic change may not land them: size the change L and open a class-2 decision
   ```
   Then I reset the branch to `main`. None of the prototype is in this delivery.

## Why the fix did not land

**`run/review.py` is on the protected surface of security design 7.2.** See `run/permissions-diff.py:486`, `("run/review.py", "the review agent and its verifier's sandbox", None)`. That gate refuses every change to a listed path, narrowings included (`run/permissions-diff.py:23-25`; README.md, "The permissions-diff gate": "an automatic change may not land it").

The fix cannot avoid touching that file, because `SHARED_ROOTS` lives there. So no number of worker rounds could clear the gate. A fix worker would be refused on every round, and returning it after 2 rounds is the expected outcome, not a quality failure of the worker.

The registry rows that prove this file (`review-verifier`, `review-budget`, … in `.ci/gate-registry.toml:1807` onward) carry `surface = "Code review"`, which is in `SECURITY_SURFACES` (`run/permissions-diff.py:337`). That confirms the whole review sandbox is a class-2 area.

The task was routed wrongly: an automatic fix task was given a change that the rules size L and send to a class-2 decision.

## Other problems the original task text would have hit

These are worth fixing in the re-filed row, whoever lands it.

- **Two of the three roots exist only on macOS.** "Lists each from inside the jail" cannot hold on the Linux CI job, where `/Users/Shared` and `/Library/Caches` are absent and `confinement()` skips roots that do not exist. The case has to plant in the roots that exist and are writable on the host, and name them in its failure. Otherwise it either fails on Linux or passes without testing anything.
- **`/Library/Caches` is root:admin-owned on macOS.** A non-admin runner cannot plant there, so the case needs the same "writable here" filter. On such a runner that root goes unproven, and the report of the run should say so.
- **The probe should check each root separately.** My prototype joined the probes with `&&`, so one hidden root makes the whole probe read "hidden" even if another root is open. A proper case runs one probe per root, or requires `<root>=hidden` for each one.
- **The mutation entry must anchor on the `SHARED_ROOTS` literal.** The existing anchor `shared = set()` would also turn red the old `reproduction_lists_no_sibling_temp` case, so it can't show that the new roots specifically are proven. The prototype anchor `'"/var/run", "/var/tmp", "/Users/Shared", "/Library/Caches")'` matched exactly once and went red as required.
- **On macOS, check that hiding `/Library/Caches` and `/Users/Shared` breaks no ordinary reproduction** (`python3`, `git`, a test runner). A self-hosted runner whose work directory sits under `/Users/Shared` is still fine, because the writable `allow` rules come later and the last matching rule wins (`run/review.py:258-259`). That is worth one live check on the Mac runner before landing.

## Recommendation

Re-file as a fix row that is **sized L and gated on a class-2 decision by the captain**, not as an automatic fix:

- **Row:** Add `/var/tmp`, `/Users/Shared` and `/Library/Caches` to `SHARED_ROOTS` in `run/review.py`. Add `.ci/test-review.py::reproduction_lists_no_shared_cache`, which:
  - plants a file under each of those roots that exists and is writable on the host;
  - lists and reads each one separately from inside the jail;
  - wants each one hidden and the finding downgraded;
  - adds a `CASES` mutation that restores the old `SHARED_ROOTS` tuple.

  `run/review.py` is on the permissions-diff surface (`run/permissions-diff.py:486`), so the change needs a class-2 decision before it can land. Check it once on the Mac runner. Finding sr2-9.
- **Acceptance:** `out=$(python3 .ci/test-review.py) && printf '%s\n' "$out" | grep -qx 'ok   reproduction_lists_no_shared_cache'`

I'd recommend against accepting the residual. The fix is three strings plus one test case, and the exposure it closes is real: a reproduction command written by a model can read other tasks' scratch under `/var/tmp`, on Linux as well as macOS. The only barrier is the decision class, not cost or feasibility.
