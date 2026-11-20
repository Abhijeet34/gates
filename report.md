# gates-issue-22: replace the no-mistakes PR-body marker with a required check run

## Outcome first

Nothing to change in this repository. The marker gate the contract names, `run/no-mistakes-required.sh`, is not on `main`: commit `a817e45` (PR #109, "chore(gates): drop the no-mistakes-required gate", merged 2026-11-12) deleted it, its bootstrap workflow, its contract test and its registry row. That merge came two days after the maintainer's 2026-11-10 decision. With the grep gone, gates has no check left to turn into a check run, and nothing in gates matches the marker any more.

What I could not do: GitHub access for this session covers only `Abhijeet34/gates`. So I could not look at the `no-mistakes` repository (which writes the marker) or the private `automation` repository (whose docs still describe the gate). Both follow-ups below sit outside this contract for that reason.

## What I did

1. Checked out `main` at `a8185d610ec940fcdbc898b231fed286f08c92e3`, the recon commit.
2. Looked for the gate and the marker in the tracked tree:

   ```
   $ ls run/no-mistakes-required.sh .github/workflows/shared-no-mistakes-required.yml
   ls: cannot access 'run/no-mistakes-required.sh': No such file or directory
   ls: cannot access '.github/workflows/shared-no-mistakes-required.yml': No such file or directory
   $ git grep -n -i -e "no-mistakes-required" -e "Updates from \[git push" \
         -e "raised through no-mistakes" -e "pipeline-attestation"
   (no output; exit 1)
   ```

   The other `no-mistakes` hits on `main` concern the pipeline's local staging bare (`~/.no-mistakes/repos/`) in the push hooks (`system-maintenance/git-hooks/pre-push:96`, `.githooks/pre-push:91`, `pushrange.py:63`). One more is the caller list in `bin/rollout.toml:33`. None of them reads a PR body.
3. Found where the gate was removed:

   ```
   $ git log --all -S"no-mistakes-required" --oneline
   a817e45 chore(gates): drop the no-mistakes-required gate (#109)
   $ git merge-base --is-ancestor a817e45 origin/main && echo on-main
   on-main
   ```

   `git show --stat a817e45` lists the removed files: `run/no-mistakes-required.sh` (70 lines), `.github/workflows/shared-no-mistakes-required.yml` (98), `.ci/test-no-mistakes-required.sh` (189) and the `no-mistakes-required` row in `.ci/gate-registry.toml` (23). It also removed the `check.sh` invocation, the two marker `literal` declarations in `.ci/test-upstream-owner-refs.sh`, and the README mentions.
4. Read the PR #109 body. Its reason for removal: no caller. `grep -rIn "no-mistakes-required"` over every local clone's `.github`, `.ci` and `run` directories found no `uses:` line. `gh search code "no-mistakes-required" --owner Abhijeet34` returned only gates files. The PR also notes that it left `automation`'s `.ci/shared-workflows.md` describing the gate.

## What the removed gate did (for whoever builds the replacement elsewhere)

From `git show a817e45 -- run/no-mistakes-required.sh`:
- It read the PR body live: `gh api repos/$PR_REPO/pulls/$PR_NUMBER --jq '.body // ""'`. It tried up to 6 times, 10 s apart, because no-mistakes writes the `## Pipeline` section after it creates the pull request, and the event payload's body is stale.
- It passed when the body contained either fixed string `Updates from [git push no-mistakes](https://github.com/<owner>/no-mistakes)`, one for the mirror build and one for the upstream build. It failed closed when the body could not be read.
- That string is what the decision objects to. It lives in the PR body only so a machine can match it.

Recent PR bodies still carry the section the no-mistakes binary writes, for example PR #22: `Updates from [git push no-mistakes](...)` plus an HTML comment `<!-- no-mistakes-pipeline-attestation:v1 {"head_sha": ..., "steps": [...]} -->`. Gates no longer reads either one.

## Recommendation

- **In gates:** close the task as no change. If a gate comes back, don't rebuild it as a body grep. Build it as a check run keyed to the head SHA. One constraint applies: posting a check run needs a GitHub App token with `checks: write` (a workflow's `GITHUB_TOKEN` posts as app 15368, which anyone with `checks: write` can imitate; see README.md:314). The `verifier` branch's `post.sh` (README.md:391) is the existing pattern for an App-posted check run. A required status check pinned to that App's id would let a ruleset require "raised through no-mistakes" without anything in the body.
- **In no-mistakes (outside this contract):** if the marker should leave PR bodies, the change belongs in the binary that writes `Updates from [git push no-mistakes]`. If it still needs to prove a PR came through the pipeline, it should post an App check run on the head commit.
- **In automation (outside this contract):** `.ci/shared-workflows.md` still describes the deleted gate, as PR #109 noted.
