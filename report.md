# ci-push-run-removal: gates (gates-issue-6-2), scout report

Run `r20261117T182256-28138`, read at `main` = `cc7e3c44cd125c2335b835a24791b4dab63db650`, 2026-11-17.

## What I could not do

- **Read any fleet repository other than gates.** This session's GitHub access is scoped to gates; a contents read of another repository answered `GitHub access to this repository is not enabled for this session (HTTP 403)`.
  The fleet part below is therefore a default-branch code search only, unverified per file, and reported in aggregate because this repository is public and must not name private ones.
- **Read the owner's account type** (`gh api users/...` answered `sessions are bound to their configured repositories`), so the merge-queue availability point below rests on GitHub's documented plan limits, not a live read.
- **Change anything.** This is a scout; nothing but this file is on the branch.

## Summary

1. gates already did this: PR #76 (2026-10-26, "ci: drop push-to-main runs that re-verify the tested pull request tree") removed the push trigger from `ci.yml`, `watermark-scan.yml` and `gate-shadow.yml`, and PR #99 (2026-11-04) dropped the weekly secret scan. The push-run API agrees: `CI`, `watermark scan` and `gate shadow` have no push run after 2026-10-26.
2. **One push run is left: `dependency-advisories.yml`** (33 push runs 2026-10-26 to 2026-11-17). Its only red was a false red that raced Dependabot. Recommend dropping its `push:` trigger.
3. **The premise PR #76 rests on is not true live.** `ci.yml:19-22` says "The ruleset's strict status policy makes the tree that lands on main the tree its pull request run tested", and `.github/ruleset-main.json:29` says `"strict_required_status_checks_policy": true`, but the live `protect-default-branch` ruleset has **`false`** (ruleset last updated 2026-11-14, the day after CI cost design 5.2 / `verifier-tree-rule` was approved to run trains with strict off, README.md:345).
   No merge has landed an untested tree yet (39 of 39 merges since 2026-10-26 had a head up to date with main and the same tree as the landed commit), but that now holds by habit, not by rule.
4. The better tool is a **tree-proved check keyed on `head_commit.tree_id`**, the same equality the tree rule (`run/owner-verify.py`) and factory's `ledger` (relaxation `factory-ledger-on-push`, ratified 2026-11-15) already use, shipped once from gates as a `run/` script plus a shared workflow job. It makes push-run removal safe on any repository whether or not strict is on, and lets repositories with strict on drop the push trigger outright.

## Evidence

### gates' triggers at `cc7e3c4`

| Workflow | `push` to main | Where |
|---|---|---|
| `ci.yml` | no (pull_request, pull_request_review, workflow_dispatch) | `.github/workflows/ci.yml:11-17`, rationale `:19-25` |
| `watermark-scan.yml` | no | `.github/workflows/watermark-scan.yml:7-12` |
| `gate-shadow.yml` | no | `.github/workflows/gate-shadow.yml:8-11` |
| `dependency-advisories.yml` | **yes**, plus pull_request, weekly schedule `41 6 * * 1`, dispatch | `.github/workflows/dependency-advisories.yml:8-16` |
| `shared-*.yml` | `workflow_call` only; the caller picks the trigger | e.g. `.github/workflows/shared-secret-scan.yml:15-16` |

gates has no caller of `shared-secret-scan.yml`; its own secret scan is the `secrets` job inside `ci.yml`, so it has no push-time secret scan to remove.

### Push runs on gates, by workflow

```
$ gh api -X GET repos/Abhijeet34/gates/actions/runs -f event=push -f per_page=100 --jq '...group_by(.name)...'
total_count 189
{"n":"CI","count":27,"first":"2026-10-21","last":"2026-10-26"}
{"n":"watermark scan","count":27,"first":"2026-10-21","last":"2026-10-26"}
{"n":"gate shadow","count":13,"first":"2026-10-25","last":"2026-10-26"}
{"n":"dependency advisories","count":33,"first":"2026-10-26","last":"2026-11-17"}
```

`dependency advisories` push runs: 33, 32 success, 1 failure, mean wall time 11.2 s, 370 s in all.
gates is public, so these minutes are not billed; the saving here is noise and runner time, not money. The 34 USD a month in the goal is in the private repositories.

### The one red push run was a false red

The failed run is the push of `709e950`, the squash of PR #90 "deps(mutation): override the judge tinypool to 2.1.2 for two advisories", merged 2026-11-02T07:54:17Z; the run started 3 s later and its `Judge` step said:

```
##[error]critical advisory in tinypool (development scope, run/mutation-judge/package-lock.json): GHSA-5gmw-xhrv-c9v3 .../security/dependabot/13
##[error]critical advisory in tinypool (development scope, run/mutation-judge/package-lock.json): GHSA-85c8-ppgw-ccpr .../security/dependabot/12
##[error]2 open advisory(ies) at or above `high` are neither fixed nor recorded in .github/dependency-deferrals.yml.
```

That commit is the fix for exactly those two advisories. On a push the gate judges the default branch's open alerts alone (`run/dependency-advisories/judge.sh:21-24`: "every other event is judged on the default branch's alerts alone"; `run/dependency-advisories.sh:8` runs the compare only on `pull_request`), and Dependabot closes alerts asynchronously after the push lands, so a push run reads the alert set from before the merge.
A push run of this gate therefore cannot add a verdict the pull request run did not already give (the PR run judges the same default-branch alerts plus the PR's own compare), and right after a dependency fix it gives a wrong one.
Alerts that open without a commit are what the weekly schedule is for (`dependency-advisories.yml:13-15`), and every later pull request run sees them too.

### The live ruleset is not the committed one

```
$ gh api repos/Abhijeet34/gates/rulesets/23545843 --jq '{bypass_actors, rules:[...]}'
"bypass_actors": []
pull_request: allowed_merge_methods ["merge","squash","rebase"], required_approving_review_count 0
required_status_checks: contexts "checks", "watermark / watermark scan", "strict_required_status_checks_policy": false
$ gh api repos/Abhijeet34/gates/rulesets/23545843 --jq '.updated_at'
2026-11-14T10:56:34.109Z
```

versus `.github/ruleset-main.json:29`: `"strict_required_status_checks_policy": true`. Nothing in the repository compares the two (`grep -rn strict .ci run` finds only a fixture in `.ci/test-permissions-diff.py:936`).
With no bypass actors, `non_fast_forward` on, and a pull request required, every commit on main is a PR merge, so the only gap strict closed is a PR tested against an older base. With strict off that gap is open: a PR whose head is behind main can merge, the landed tree is `head ⊕ newer main`, no PR run tested it, and no push run tests it either.

### Has that gap been hit? Not yet

For every first-parent commit on main since 2026-10-26 I compared the PR head with the commit's parent and the landed tree with the head's tree:

```
for each squash merge c (parent p) of PR n:
  behind_by = compare(p...head(n)).behind_by
  tree(c) == tree(head(n)) ?
```

Result: 39 of 39 merges `behind_by=0` and tree-equal (one transient connection reset on #89, re-run by hand: head tree `0558e7d9...` = landed tree `0558e7d9...`). Six of those merged after strict went off on 2026-11-14 (#114, #115, #123, #125, #127, #126). All 39 merges were squash merges.
`behind_by=0` means the head contained main's tip at merge, so the PR run's merge ref tree was the head's tree (main only moves forward), which is the landed tree: each landed tree was tested.

### Fleet, from code search only

`gh api -X GET search/code -f q='user:<owner> "branches: [main]" push path:.github/workflows'` returns 54 workflow files in 19 repositories, 12 of them `secret-scan.yml` in private repositories. The query matches the words anywhere, so it over-reports: two of gates' own hits are a `pull_request: branches: [main]` with "push" in a comment (`watermark-scan.yml`) and the one real trigger. Each hit needs reading before it counts; this session could not read them.

## Options weighed

| Option | Removes the push run | Safe with strict off | Cost per push | Verdict |
|---|---|---|---|---|
| A. Delete the `push:` trigger, rely on strict | yes | **no** | 0 | Right only where strict is on live, and nothing checks that it stays on. |
| B. Turn strict back on fleet-wide | enables A | yes | 0, but every PR behind main re-runs CI after an update | Contradicts the approved train design (README.md:345); fine for repositories that run no train. |
| C. Tree-proved job on push (factory's `ledger`, generalised) | runs one short job, skips the expensive ones | yes | one job, billed as a whole minute on a private repository | The fallback where strict is off; strictly cheaper than today. |
| D. GitHub merge queue (`merge_group`) | yes, the queue tests the exact landing tree | yes | 0 extra | Not offered for private repositories of a personal account by GitHub's plans (not verified live); the fleet's own train and the tree rule already fill this role. |
| E. Skip by `paths`/`paths-ignore` | partly | no | 0 | Does not address the redundancy, and `run/weakening.py` refuses `paths-ignore` on a required job (`workflow-condition`, README.md:226). |

The tree-proved check needs no new state. A pull request run's `head_commit.tree_id` is the PR head's tree (owner-verify already reads it, README.md:357), so a push of commit `c` with first parent `p` is proved when there is a run of the CI workflow with event `pull_request` or `pull_request_review`, conclusion `success` with a successful `checks` job in its latest attempt, whose `head_commit.tree_id` equals `tree(c)` and whose head descends from `p`.
The descent condition is what makes the merge ref tree equal the head tree: the base at run time is an ancestor of `p`, so `merge(head, base) = head`. Without it, tree equality alone could match a head tested against an older base.
That is the single-member case of the tree rule (`run/owner-verify.py`, "With --train FILE ... A single-member train is its own head") and the check factory's `ledger` makes, so gates can ship one implementation instead of each repository growing its own.

## Recommendations

1. **gates, now (small, class-1):** remove `push: branches: [main]` from `.github/workflows/dependency-advisories.yml:9-10` and say why in its header, as `watermark-scan.yml:7-8` does: the push run judges main's alerts before Dependabot has closed them (PR #90's push, 2026-11-02), and the pull request runs and the weekly schedule cover everything else. The workflow is listed in `.ci/gate-registry.toml:46`; check whether that row's inputs assume a push trigger.
2. **gates, decide which premise holds (class-2, the maintainer's):** either
   - set `strict_required_status_checks_policy` back to `true` live, matching `.github/ruleset-main.json`, if trains are not going to run on gates; or
   - change `.github/ruleset-main.json:29` to `false` and rewrite `ci.yml:19-22` to rest on the tree-proved check (recommendation 3) rather than on strict.
   Either way the file and the live ruleset must agree; today `ci.yml`'s stated reason is false. `run/permissions-diff.py` treats the ruleset file as protected surface, so this goes through that decision path.
3. **gates, the tool for the fleet:** ship `run/tree-proved.sh` (API only, `bash -e`, contract test under `bash -e` per AGENTS.md) and a `shared-tree-proved.yml` bootstrap job exposing `outputs.proved`, built on the condition above and sharing code with `run/owner-verify.py`'s tree rule. Fail closed: a lookup that errors, finds no run, or is skipped reports `proved=false` and the expensive jobs run. Register it in `.ci/gate-registry.toml` with red (tree differs, head behind `p`, run not `success`) and clean inputs, and add a generic `relaxation` record replacing factory's two repository-specific ones once a second caller adopts it.
4. **Fleet rollout, per repository (outside this session's access):** for each workflow the search found, read the trigger. Where strict is on live, delete the `push:` trigger (option A). Where it is off, call the tree-proved job and gate the heavy jobs on it (option C). Keep a non-PR run only where it buys something a PR cannot: a weekly schedule for new advisories or new gitleaks rules, and a dispatched run on release commits for `run/lock.sh` (`ci.yml:20-22`, PR #96).
5. **Drift alarm:** add a check, run on the weekly schedule rather than on push, that reads the live ruleset and fails when it differs from `.github/ruleset-main.json`. The drift found here would have been caught on 2026-11-16.

## Side finding

README.md has the section `### The tree rule` twice, verbatim (`README.md:343` and `README.md:361`).
