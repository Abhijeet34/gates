# gates-issue-6: push-to-main run removal (`ci-push-run-removal-r1`, gates slice)

Scout report, 2026-11-16, against gates `main` at `1739f45`.

## What I could not do

- **Read the fleet's workflow files or rulesets directly.** This session's GitHub access reads and writes gates only.
  The fleet inventory below comes from code search (`gh api search/code`, default branch only), which returns matching fragments, not whole files.
  Where a fragment shows only a comment that mentions "push", I mark the trigger as unconfirmed.
- **Check any caller's live ruleset** (strict policy, bypass actors). The premise "landed tree = tested tree" needs both, per repository. The only one I could read is gates' own `.github/ruleset-main.json`.
- **Re-measure the dollar figure.** The 34 USD/month in the contract comes from the cited reports, and I could not read the Actions usage of private callers.
  gates is public, so its own runs never cost money.
- **Verify merge-queue availability from documentation this session.** The point in section 4 is from prior knowledge and is marked so.

## Summary

1. **gates' own share is already done.** `b871523` (2026-10-26, #76, "ci: drop push-to-main runs that re-verify the tested pull request tree") removed the `push` trigger from `ci.yml`, `watermark-scan.yml` and `gate-shadow.yml`.
   The run history agrees: the last push run of each of those three is 2026-10-26T10:31Z.
   The only push trigger left in gates is `dependency-advisories.yml`, and that run does not re-verify a tree (section 2.2).
2. **Two things in gates must change before the fleet drops its push runs.** Otherwise the fleet change breaks gates' own release machinery:
   - `bin/gates.py bump` decides whether a rollout stage is merged and green from the check runs on the caller's lock-change commit on `main` (`bin/gates.py:171-183`, `:356-364`).
     Only a push run puts check runs on that commit. Without them the stage reads `NOT green` and the next stage never opens.
     Worse, if a caller drops CI's push trigger but keeps another push workflow (release-please, a docs build), `green()` sees only that workflow's runs and can read **green on runs that tested nothing**.
   - `.ci/fixtures/gh-stub.py:195` answers `success` for any commit with no recorded checks, so `.ci/test-bump.py` cannot see either failure.
3. **The better tool is a shared proved-tree job, not plain deletion.** Plain deletion only works where strict is on. A shared job does the following:
   - It runs on push and looks up a green `pull_request` run of `checks` on a commit with the same tree.
   - It skips the expensive jobs when it finds one.
   - It still posts a `checks` result on the `main` commit.

   Factory already ratified this design (`policy/relaxations.toml:16-31`, 2026-11-15).
   It works whether or not strict is on, and it keeps every consumer that reads "main head is green" working.
   Moving it into `run/` lets the whole fleet adopt it through a lock bump.

## 1. Evidence

### 1.1 gates: push runs before and after removal

`mcp__github__actions_list list_workflow_runs event=push`, which returned 186 in total; I read the latest 100, 2026-10-20 to 2026-11-14:

```
('CI', '2026-10') 28                 last 2026-10-26T10:31:21Z  median wall 402 s
('watermark scan', '2026-10') 29     last 2026-10-26T10:31:21Z  median wall 20 s
('gate shadow', '2026-10') 13        last 2026-10-26T10:31:21Z  median wall 18 s
('dependency advisories', ...) 30    last 2026-11-14T21:41:31Z  median wall 10 s
```

```
$ git log --format='%h %ad %s' --date=short -S'push:' -- .github/workflows/ci.yml .github/workflows/watermark-scan.yml .github/workflows/gate-shadow.yml
b871523 2026-10-26 ci: drop push-to-main runs that re-verify the tested pull request tree (#76)
```

**What the push runs caught in their last week: nothing real.** Two CI push runs on `main` failed, on 2026-10-26 at 09:44Z and 10:31Z.
In both, the only failed job was `secret scan`, at its step "Install the watermark gate's inspector and writers", an apt install, after 4 to 6 minutes.
`checks` then failed by aggregation, and every test step that ran passed.
So both reds were infrastructure flakes on trees their pull requests had already proved, not defects. That is the cost of the push runs, in red `main` badges, on top of the minutes.

**The premise holds in gates.** `.github/ruleset-main.json:29` has `"strict_required_status_checks_policy": true`, a `pull_request` rule, and no `bypass_actors`.
With strict on, the PR head contains the base, so a merge, squash or rebase lands exactly the head's tree.
`ci.yml:19-27` records this, and the exception: a release commit gets a dispatched `checks` run, because `run/lock.sh:66-80` requires a green `checks` on the locked gates commit itself.

### 1.2 Fleet: who still runs push-to-main

I ran `gh api -X GET search/code -f q='push path:.github/workflows user:Abhijeet34'` (69 files) and read the fragments with the `text-match` media type.
The table covers only the repositories `bin/rollout.toml` and the relaxation records already name.

| Repository | push-to-main confirmed in fragment | Notes from the fragment |
| --- | --- | --- |
| automation | ci, watermark-scan, instruction-truth, dependency-advisories | watermark-scan's comment argues the push trigger is not redundant: "only a push-triggered run sees" a web edit, API commit or `--no-verify` push |
| firstmate | ci | secret-scan fragment shows only comments (unconfirmed) |
| papertrace | none in ci (comment: "There is no `push: main` job ... the merged tree is the tree that was just proved") | already done; secret-scan unconfirmed |
| foliot | ci, gate-shadow, dependency-advisories | |
| foliot-bench-corpus | ci, secret-scan, gate-shadow, watermark-scan (likely) | |
| halderworks-design | gate-shadow; ci fragment shows comments only | maintenance.yml dropped push deliberately |
| halderworks-site | ci | |
| fleet-records | ci, gate-shadow, secret-scan, watermark-scan | |
| chrome-devtools-axi, gh-axi, quota-axi, tasks-axi | ci (also release-please on push, which is not a re-verification) | secret-scan fragments show comments only (unconfirmed) |
| ops | ci, secret-scan, watermark-scan | |
| quoth | ci, dependency-advisories | secret-scan unconfirmed |
| pointback | ci, dependency-advisories, release (release-please) | ci comment: "A superseded push run on main is the only record that the merged commit was ever green." |
| no-mistakes | release on push; ci uses `paths-ignore` | ci and secret-scan triggers unconfirmed |
| factory | ci (with the proved-tree `ledger`), gate-shadow, secret-scan (unconfirmed), watermark-scan, govulncheck | strict is false (contract, 2026-10-26) |

### 1.3 Consumers that read "main's commit is green"

Removing push runs takes away the only check runs on `main` commits. These read them:

- `bin/gates.py:178-183`: `green(sha)` over `commits/{sha}/check-runs`, with `sha` the first-parent commit that last changed `.github/gates.lock` (`:171-175`).
  An empty list is not green, and any non-CI run counts.
- firstmate `bin/fm-repo-posture.sh`: walks heads and reads `commits/$sha/check-runs` for a context (search fragment). Its purpose needs checking in firstmate before that repository drops its push runs.
- pointback ci.yml's comment, quoted above, states the same dependency in words.
- Promotion in `run/gate.py` (docstring lines 57-63) counts fresh shadow records, and push runs add some. The README says the shadow comparison "is a head-by-head read of those artifacts against the caller's check runs on the same head" (README.md, "The shadow job").
  If a caller drops CI's push trigger but keeps gate-shadow's, the `main` heads have no check runs to compare against. Drop both together.
- `run/lock.sh` and `verifier/activate.sh` read `checks` on gates commits only. Gates already solves that with a dispatched run (`ci.yml:20-22`).

## 2. Per-workflow judgement

### 2.1 CI (`checks`), secret scan, watermark scan, gate shadow: push re-verifies the same tree

- **Where strict is on and the ruleset has no bypass actor**, the push run tests a tree the PR run already proved, and it can only add flake reds.
  The arguments for keeping the push run (a web edit, an API commit, `--no-verify`) all assume a commit can reach `main` without a pull request.
  A `pull_request` ruleset rule with no bypass actor refuses all three, because a web edit to `main` must go through a pull request too.
  gates' own bootstrap says what the CI secret scan adds: "history that predates the hook, and a re-scan when gitleaks ships new rules" (`shared-secret-scan.yml:11-14`).
  The weekly schedule covers both.
- **Where strict is off (factory), or a bypass actor exists**, the push run is the only witness of the landed tree. Keep it, or make it cheap with the proved-tree job.

### 2.2 dependency advisories: not a re-verification; keep

This workflow does not re-check the tree. It reads `main`'s live Dependabot alerts, which change without a commit.
On a pull request it adjusts them by the dependency compare.
Its own header records the case only the run on `main` catches (`shared-dependency-advisories.yml:31-34`): "the first run on the default branch after the merge is red".
A gates push run of it takes about 10 s and is free, because gates is public. It is out of scope for this backlog item.

## 3. Recommendation

In order. Each step is a gates change that the fleet receives through a lock bump, except the per-caller trigger edits.

1. **Fix `bin/gates.py` `green()` first** (gates).
   Judge the pull request that produced the commit, not the commit:
   - resolve `commits/{sha}/pulls`;
   - take the merged pull request whose `merge_commit_sha == sha`;
   - require `sha^{tree}` to equal the PR head's tree, which strict guarantees;
   - require the head's `checks` from app 15368 to be `success`.

   If any step fails, fall back to the commit's own `checks` run. Match `checks` by name and app, as `run/lock.sh:67-72` does, never by "all runs pass".
   In `.ci/fixtures/gh-stub.py`, answer an empty `check_runs` for a commit with no fixture, and add `commits/{sha}/pulls`.
   Add `.ci/test-bump.py` cases for:
   - no push run;
   - only a release-please run on the commit;
   - a tree mismatch.
2. **Ship the proved-tree check as `run/proved-tree.sh` plus `shared-proved-tree.yml`** (gates), generalising factory's `ledger`.
   - Input: `HEAD^{tree}`.
   - Lookup: the `checks` runs from app 15368 on `pull_request` events whose head commit has that tree, found through the merged pull request of the push commit (`commits/{sha}/pulls`), with no search over all runs.
   - Output: `proved=true|false`. A failed or partial lookup gives `false`, so the full suite runs (fail-closed, as factory's record states).

   A caller keeps `push:` and gates its heavy jobs on `needs.proved.outputs.proved != 'true'`, and `checks` still posts on the `main` commit.
   That keeps `bin/gates.py`, firstmate's posture read and pointback's "only record" working without step 1. Do step 1 anyway, because it is the right reading.
   Cost per merge is one job of about 10 s, in place of minutes. The job also works where strict is off, which is factory's case.
   Two pieces of policy follow, and both are class-2:
   - `.ci/checks-verdict.sh` must accept "skipped because proved" only when `proved == 'true'`.
   - `run/weakening.py` will flag the `if:` as `workflow-condition` (README.md, "The weakening guard"), so each adopting repository needs a record like `factory-shards-skip-proved-tree`.

   A single record keyed on the shared workflow's output would scale better than one per repository; that is a maintainer decision.
   New `run/` and workflow files need their `.ci/gate-registry.toml` rows in the same commit (AGENTS.md).
3. **Per caller, drop `push:` outright where both hold:** strict is on and the `main` ruleset has no bypass actor. Check with `gh api repos/OWNER/REPO/rulesets` and then each ruleset id, reading `strict_required_status_checks_policy` and `bypass_actors`.
   - Drop CI, gate-shadow, secret-scan and watermark-scan together, and keep the weekly secret-scan schedule.
   - Keep push on dependency-advisories and release-please.
   - Everywhere else, use step 2.
   - Do not drop anything in a caller before step 1 is merged and released, or its next bump stalls.

   automation and fleet-records carry explicit comments defending the push trigger on watermark-scan. Each needs its ruleset checked before that comment is overturned.
4. **Merge queue (`merge_group`) is not the tool here.** From prior knowledge, not verified this session: GitHub offers merge queues only on organization-owned repositories, and the fleet is under a personal account.
   On an organization, it would replace both strict and the push run.

## 4. Commands to reproduce

```
git log --format='%h %ad %s' --date=short -S'push:' -- .github/workflows/ci.yml .github/workflows/watermark-scan.yml .github/workflows/gate-shadow.yml
grep -n strict .github/ruleset-main.json
sed -n 171,183p bin/gates.py; sed -n 356,364p bin/gates.py
gh api -X GET search/code -H 'Accept: application/vnd.github.text-match+json' -f q='push path:.github/workflows user:Abhijeet34' -f per_page=100
gh api -X GET search/code -H 'Accept: application/vnd.github.text-match+json' -f q='check-runs user:Abhijeet34'
# gates push runs: GitHub MCP actions_list list_workflow_runs event=push; jobs of the two failed CI push runs of 2026-10-26
```
