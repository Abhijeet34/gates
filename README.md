# gates

The checks that stand between a commit and a public push: a gitleaks pre-push hook and its rules, a scanner that refuses machine-detectable origin marks, the machine-wide git hooks that run both, and five reusable GitHub Actions workflows that re-check the same things in CI.

Licensed under the Apache License 2.0; see `LICENSE` and `NOTICE`.

## What is here

| Path | What it is |
| --- | --- |
| `.githooks/pre-push`, `.gitleaks.toml` | The repository-local secret gate and the canonical rules. Callers also receive `system-maintenance/git-hooks/commit-msg` as `.githooks/commit-msg`. Callers carry byte copies, distributed by `.ci/gitleaks/sync.sh`, and extend the gate through a tracked, executable `.githooks/pre-push.local`, which it runs last. |
| `system-maintenance/git-hooks/` | The machine-wide hooks deployed to `~/.git-hooks` by `install.sh`, including the watermark, exposure and signature gates, which its pre-push runs concurrently. Its `README.md` owns the design and the measurements. |
| `.github/workflows/shared-*.yml` | Reusable workflows: `secret-scan`, `watermark-scan`, `dependency-advisories`, `guard-generated-files`, `no-mistakes-required`, `instruction-truth`. Each is a bootstrap; see "Pinning". |
| `run/` | What each bootstrap runs from the locked gates commit: `lock.sh`, which decides that commit, and one script per gate. |
| `run/gate.py` | The gate runner: selects a repository's gates from its `.github/gates.toml` (absent here) and the diff, keys each verdict by the gate's inputs, records verdicts and folds them with review and docs verdicts, and keeps a shadow row from blocking until it earns promotion; its docstring owns the contract. No workflow calls it yet. |
| `run/permissions-diff.py` | The permissions-diff gate over the protected surface; see "The permissions-diff gate". |
| `run/docs-truth.py`, `.github/docs.toml` | The four document rules (owner and reader, commands, byte budget, em dash) and this repository's own owner and reader list; see "The documentation rows". |
| `run/tool-gates.py` | The pinned-tool gates (zizmor, actionlint, offline OSV-Scanner); see "The tool rows". |
| `run/pr-text.py` | The pull request text scan over a title and body read as JSON on stdin; see "The text rows". |
| `run/scratch-guard.py` | The scratch guard, which runs a gate or suite and refuses a file it left behind outside its own `TMPDIR`; see "The scratch rows". |
| `run/change-gates.py` | The contract, migration and fact rows over a change, read from a caller's `.github/changes.toml`; see "The change rows". |
| `run/slop-rules.py` | The shadow slop rows (source-text assertions, symbol truth and the deterministic UI rules of STANDARDS) over a change; see "The slop rows". |
| `run/fakes.py` | The fake rows: test doubles a caller's `.github/fakes.toml` does not declare, declared fakes with no real counterpart, and fakes past their freshness; see "The fake rows". |
| `run/mutation.py` | The mutation rows: a mutant of a change's lines that no test kills, with a full run when the change deletes or weakens a test; see "The mutation rows". |
| `run/budget-monitor.py` | The gate budget monitor: prints each budgeted step's weekly p50/p95, the alarm and the fix task text; see "The budget monitor". |
| `bin/gates.py`, `bin/rollout.toml` | `gates bump` and `gates rollback`, which move callers' locks; see "Bumping". |
| `.ci/` | The contract test for each gate script, the gitleaks rule and allowlist suites, and `check.sh`, which runs all of them. |

## Calling a workflow

```yaml
jobs:
  secret-scan:
    uses: Abhijeet34/gates/.github/workflows/shared-secret-scan.yml@<40-hex commit> # v1
```

A caller pins a release commit; one still at `@main` runs what `main` holds, exactly as it did before the workflows became bootstraps.
A caller cannot move a commit pin on its own, which was measured on 2026-09-17 in a private caller.
Its `GITHUB_TOKEN` was refused on every write that touches `.github/workflows/`: `git push` and the contents API answered `refusing to allow a GitHub App to create or update workflow ... without workflows permission`, and the git data API answered `Resource not accessible by integration`.
That token also could not open a pull request, because the repository's Actions setting forbids it (`GitHub Actions is not permitted to create or approve pull requests`).
It can dispatch the caller's own workflows, and a dispatched run puts its `checks` result on the commit it ran on, so the write is the only missing piece.
A pinned `uses:` therefore needs a writer that already holds workflow write, such as Dependabot's `github-actions` updater, whose pull requests do receive the caller's checks.
A reusable workflow runs in the caller's checkout with the caller's token, so a caller grants whatever the workflow reads: `shared-dependency-advisories.yml` needs `vulnerability-alerts: read` plus the `contents: read` its pull-request dependency compare uses, and `shared-no-mistakes-required.yml` needs `pull-requests: read`.
`shared-secret-scan.yml` compares `.gitleaks.toml`, `.githooks/pre-push` and `.githooks/commit-msg` byte for byte with this repository at the locked commit, so a caller whose synced copy has drifted fails until `.ci/gitleaks/sync.sh <clone>` is re-run.
Every gate runs from a checkout of this repository, never from the caller's tree, so a caller carries no copy of a scanner and a copy it does carry is never the one run.

## Pinning

Each shared workflow is a bootstrap.
It holds what has to live in a workflow file: the trigger, the token's `permissions:`, the runner and the checkouts.
The gate itself is a script under `run/`, run from a checkout of this repository at one commit, so a lock bump can change what a gate checks but never what token it runs with.

That commit is the one the caller's `.github/gates.lock` names, or the bootstrap's own commit (`job.workflow_sha`) when the caller has no lock.
A caller at `@main` with no lock therefore runs exactly what it ran before the split, and a caller can pin the bootstrap before it gains a lock.

```text
# .github/gates.lock: one gates commit; `#` starts a comment.
<40-hex commit>
```

The pin is a commit with a `# vN` comment, because Dependabot's `github-actions` updater proposes a new commit and rewrites the comment from the `vN` tags this repository cuts.
A release is a tag on a commit of `main` whose `checks` run is green: `gh release create vN --target <main commit> --generate-notes`.
Nothing ancestry-checks the bootstrap pin itself, so a tag that moved would have Dependabot propose an arbitrary commit; tags are never moved.

`run/lock.sh` decides whether the commit may run, before anything from it does, and refuses:

| Control | Refused when | Red fixture |
| --- | --- | --- |
| Ancestry | the lock names a commit that `refs/heads/main`, fetched by name, does not contain. GitHub serves any commit of the fork network by its sha, so the lock's own sha is never fetched. | `lock-imposter` |
| Green checks | the commit has no `checks` run from GitHub Actions (app 15368), or its latest one is not `success`. | `lock-checks` |
| Bundling | a pull request changes the lock and anything but the lock and the synced copies. | `lock-bundled` |
| Author | a pull request changes the lock and its author is not a `<login> <id>` line of `run/bump-identity` on gates `main`. | `lock-author` |
| Downgrade | a pull request moves the lock to a commit that does not descend from the base's, unless it is exactly the lock the base's lock replaced on its first-parent history (a rollback). | `lock-downgrade`, `lock-rollback` |
| Drift | the caller's synced copies differ from gates at the locked commit (`run/secret-scan/verify.sh`). | `lock-drift` |

Adding or removing the lock is a lock change: a removal moves the lock to the bootstrap's own commit, and passes only as a bump would.
The bump identity is read from `main` rather than from the caller or the lock, because the pull request under judgement must not name its own author as authorised.
It is bound by numeric id as well as login, because a login is claimed by whoever registers an App of that name first; with no line there, every lock change is refused.
It names the builder App, `hw-bodhi[bot]`, which every factory branch push also uses, so the author control separates the factory from a person, Dependabot or a fork, not one factory task from another; the other controls and the merge review are what hold against a builder token used for the wrong lock.

The green-checks call uses the caller's `GITHUB_TOKEN` against this public repository.
That a caller's token granted only `contents: read` can read these check runs is not yet measured from another repository; if it cannot, the lock is refused, never trusted.

## Bumping

```bash
bin/gates.py bump v2           # open each lock bump the rollout allows today
bin/gates.py rollback quoth    # move one caller's lock back one step
```

`bump` walks the stages in `bin/rollout.toml`, which is the only place a caller list or a hold period is written.
A stage opens once every repository of the stage before it has the release merged on its default branch, with every check run on that merge commit passing, and that stage's `hold_days` have passed since the last of those merges: canaries on day 0, the fleet from day 2, products from day 7.
It is safe to re-run: a caller already at or past the release is reported, an open bump pull request is left alone, and a stage still holding prints when it opens.

For each caller it opens, it mints the builder App's token for that one repository (`app-token builder --repo <r>`), scoped to contents and pull requests write, checks read, never workflows, clones the default branch, and builds the bump commit locally in two steps.
First the lock alone, judged by `run/lock.sh` from the caller's own bootstrap pin exactly as the pull request's CI will judge it, so nothing from the release runs until it is proved on gates `main` with a green `checks` run and the token's identity is the bump identity.
Then the release's `.ci/gitleaks/sync.sh`, run without any token, and `run/lock.sh` and `run/secret-scan/verify.sh` again, which refuse a release that writes more than the lock and the copies.
Only then does it write to GitHub: a branch `gates-lock/<commit>` at the base, one `createCommitOnBranch` commit carrying exactly those files, and the pull request.
The commit is GitHub's own, so it is signed by `web-flow` and verified, and its author is the builder App; it is fetched back and refused, with its branch deleted, unless it is verified, by that author, and has the tree that was judged.

`rollback` moves a caller's lock to the one its last lock change replaced, with that commit's copies, through the same path; it refuses a second rollback, which would re-apply the release the first one removed.
Rolling forward again is the next `bump`.

Three properties of `createCommitOnBranch` shape this, measured on 2026-10-16 on a throwaway gates branch as the builder App:

- A modified file keeps its mode, but an added file is always `100644`, and the mutation has no mode field. A caller that lacks a synced hook therefore cannot gain it through a bump, because `verify.sh` refuses a hook that is not executable; `bump` refuses that caller and names the file, and the remedy is one `sync.sh` push.
- The commit is verified (`signature.state` `VALID`, `wasSignedByGitHub` true), which the signature-requiring rulesets accept.
- A file under `.github/workflows/` is refused to the builder's default token (`Resource not accessible by integration`), so a bump cannot change the workflow that judges it.

The release's `sync.sh` runs on the machine that bumps, as `install.sh deploy` does, and the same proof gates both: it is on `main`, its `checks` run is green, and it was reviewed there.

Dependabot was the alternative for the lock and does not fit it: its `github-actions` updater reads only workflow files, so it cannot move `.github/gates.lock`, and it cannot run `sync.sh`, so even a bump of the workflow pin would arrive with drifted copies.
It stays the writer for the rare bootstrap pin bump, which needs `workflows` write.

## The permissions-diff gate

`run/permissions-diff.py --base B` reads `git diff B...HEAD` and refuses every change to the files that grant an agent authority, naming each path, in 0.12 s on this repository (measured 2026-10-19).
Its refusal is the signal that sizes the change L and opens a class-2 decision; an automatic change may not land it.
It refuses a narrowing too: telling a narrowing from a widening is left to that decision.

The surface is security design 7.2's list, mapped onto the paths that exist today (read 2026-10-19 across the fleet's repositories):

| 7.2 entry | Paths | What counts as a change |
| --- | --- | --- |
| `settings*.json` and worker profiles | `**/.claude/settings*.json`, `**/managed-settings.json`, `**/managed-settings.d/**`, `**/claude-code/config/settings*.json`, `internal/sandbox/profile.go` | any |
| the `fac permit` rule table | `internal/permit/permit.go` | any |
| `route.toml` `[tiers]` and `[vendors]` | `route.toml` | `[tiers]`, `[repos]` or `[vendors]` differs, or the file cannot be parsed |
| App manifests and the minter's permission subsets | `**/github-apps/roles.json`, `**/github-apps/app-token.sh`, `**/github-apps/register-app.py` | any |
| workflow `permissions:`, rulesets, Actions settings | `.github/workflows/*.yml` and `*.yaml`; `.github/ruleset*.json`, `.github/rulesets/**` | a workflow's `permissions:` blocks differ, comments aside; a ruleset, any |
| gate registry rows with `phase = "security"` | `.github/gates.toml`, every row; `.ci/gate-registry.toml`, rows with `surface = "Security"` | an existing row deleted or any of its fields changed; a new row passes |
| the outbound pattern set and channel allowlists | `outbound/outbound.go` | any |
| memory guards and `captain.md` | `**/memory-write-guard.sh`, `**/captain.md` | any |
| (added) the gate's own surface | `run/permissions-diff.py` | any |

Where 7.2 no longer matches what exists:

- No permit rule table exists: the permit host is a deny overlay compiled into `internal/permit/permit.go`, so that file is protected whole.
- No worker profile file exists: the per-task sandbox profile is code in `internal/sandbox/profile.go`, and the orchestrator's worker overlay is built inside its launcher script, which no path rule can protect without refusing every launcher edit.
- Neither `phase` nor `flake` is a registry key, and `run/gate.py` refuses an unknown key, so no row in `.github/gates.toml` can be marked as security; every existing row is protected instead.
- No channel allowlist, Actions permission setting as code or App manifest file exists; the App's permissions are written by `register-app.py` and subset by `roles.json` and `app-token.sh`.
- `route.toml` marks `[repos]` captain-owned beside `[tiers]`, and moving a repository to a wider tier widens which vendors receive its source, so `[repos]` is added.
- `settings*.json` misses `managed-settings.json`, which outranks every other scope, and catches an editor's `.vscode/settings.json`, so the globs name Claude Code's files.
- Not protected, and candidates for the list: `.github/CODEOWNERS`, `.github/secret_scanning.yml`, `.gitleaks.toml`'s allowlists, `.githooks/**`, and a new workflow with no `permissions:` key, which takes the repository's default token.

A caller runs it as a shadow row in its `.github/gates.toml`, so `run/gate.py` reports its red as `shadow` until the promotion rule in its docstring lets it block:

```toml
[[gate]]
id = "permissions-diff"
inputs = ["**"]
# The verdict depends on the base, which the runner neither keys nor passes to
# `run`, so the merge base enters the key through a probe.
tools = { base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/permissions-diff.py" --base "$BASE_SHA"'
shadow_since = 2026-10-19
```

`BASE_SHA` and `GATES_DIR` come from the calling workflow, which does not exist yet.
`.ci/test-permissions-diff.py` runs 7.2's five red fixtures and runs this row through `run/gate.py`, which folds it to `shadow` with a `shadow_since` and refuses without one.

## The documentation rows

`run/docs-truth.py owner|commands|budget|emdash [root]` answers four rules from the tree alone; its docstring owns the exact scope.
Path truth (`run/instruction-truth/path-truth.py`) and the instruction size caps (`run/instruction-truth/size-limit.py`) already exist and are not repeated: this script judges what they skip.

- **`owner`**: every `AGENTS.md` and `CLAUDE.md` is listed in `.github/docs.toml` with a non-empty `owner` and a `reader` of `agents`, `compliance` or `clients` (report 7.3's three readers). Any other Markdown file is judged only when it is listed.
- **`commands`**: a command in a shell-tagged fence of any Markdown file must parse under `bash -n` and its command word must exist. Path truth skips fenced blocks, which is where a renamed script goes stale unnoticed.
  "Exists" means a bash builtin or keyword, a file of the tree (executable when run directly, present when handed to `bash`, `sh` or `python3`), or a program the manifest lists under `tools`.
  A `PATH` lookup was rejected because it would judge the machine running the check, so a runner without `gitleaks` would refuse every caller's docs; running `--help` was rejected because it executes what the document says.
  A command word holding `$`, a glob or `~`, a heredoc body and inline backtick spans are not judged: a span such as `git push` is as often a noun as an instruction.
- **`budget`**: the root `AGENTS.md` is at most 8,000 bytes. The plan asks for a word budget, but the fleet's instruction budgets are all bytes or lines: a home file under 5,000 bytes (factory plan r5, "Budgets"; enforced by `size-limit.py`), a repository file under 8,000 bytes (the same section and report 7.2, "a root `AGENTS.md` of at most 8,000 bytes") and 200 lines (report 7.2, quoth's target; `size-limit.py`).
  No word figure exists to take, and converting bytes to words would invent one, so the 8,000 is the only new number and it keeps its unit. A nested `AGENTS.md` has no fleet budget and is not judged.

- **`emdash`**: no Markdown file carries an em dash, except a forked file.
  The rule is "Never use the em dash" in the maintainer's global agent instructions and in the draft skill's hard rule 1.
  The exemption is recorded in factory plan r5, "Docs and writing, no AI slop" ("em dashes (forked files exempt)").
  A fork is a file with the `<!-- LOCAL FORK` header that the fork policy (FORKS.md, "What is forked") puts at the top of a forked `SKILL.md`, found in its first 30 lines so that a quotation of the header lower down exempts nothing.
  The exemption is for the dash only: this rule judges nothing else.
  Measured on this tree, 2026-10-19: 0 findings.

Read tracking is left out. Report 7.3 counts reads from the event log (`record.read`) and retires a document unread for a quarter, and a gate cannot decide that deterministically:

- The log is not in the tree, so it cannot enter the runner's cache key, and the same commit would fold differently from one day to the next.
- A quarter without a read is a clock, not a property of the commit under review.
- A session loads an instruction file whole, so every `AGENTS.md` counts as read on every run and the measure says nothing about it.

It fits a scheduled report over the log, as the budget monitor does for step times, which is the maintainer's to add.

Each rule is a row of a caller's `.github/gates.toml`, in shadow until the promotion rule in `run/gate.py` lets it block. They are `kind = "check"` because a `docs` row has no `run` and takes a recorded review verdict.
The commands row lists `**` as inputs because a script that is deleted or loses its executable bit changes the verdict without touching a document:

```toml
[[gate]]
id = "docs-owner-reader"
inputs = ["**/*.md", ".github/docs.toml"]
run = 'python3 "$GATES_DIR/run/docs-truth.py" owner .'
shadow_since = 2026-10-19

[[gate]]
id = "docs-commands-run"
inputs = ["**"]
run = 'python3 "$GATES_DIR/run/docs-truth.py" commands .'
shadow_since = 2026-10-19

[[gate]]
id = "docs-agents-budget"
inputs = ["AGENTS.md"]
run = 'python3 "$GATES_DIR/run/docs-truth.py" budget .'
shadow_since = 2026-10-19

[[gate]]
id = "docs-no-em-dash"
inputs = ["**/*.md"]
run = 'python3 "$GATES_DIR/run/docs-truth.py" emdash .'
shadow_since = 2026-10-19
```

`.ci/test-docs-truth.py` runs the cases of all four rules and runs these rows through `run/gate.py`, which folds each to `shadow` with a `shadow_since` and refuses without one. No workflow calls the script: a step in `shared-instruction-truth.yml` is the maintainer's to add in the weekly batch.
`.ci/check.sh` runs `owner`, `commands` and `emdash` on this tree and not `budget`, because this tree's own `AGENTS.md` is over it.

## The budget monitor

`run/budget-monitor.py --budgets FILE` prints, per budgeted gate step, the last three weeks' sample count, p50 and p95, computed from the Actions API with `gh api` alone and stored nowhere.
It stands in for the runner's timings until its records carry them fleet-wide; the docstring owns the file format and the exit status.
A series is a repository, workflow, job and optional step with a `budget_s`, for example pointback's `ci.yml`, job `check`, step `check`, 360.

- **ALARM** when the latest week's p95 is at or over 70% of the budget, which is how pointback's 12 s to 338 s climb would have been named on 2026-10-13, before the step hit its 6-minute timeout on 2026-10-15 (report 2.5; `.ci/test-budget-monitor.py` replays that table).
- **FIX** when the trailing-7-day p95 was at or over the budget itself on each of the last 14 days. The report then prints the title and body of the task to file; it files nothing.
- A series with no sample in the latest week prints `NO DATA` and exits 2, because silence is not a pass.
- A cancelled step is not a sample: a cancel ends a step early, so it says nothing about the budget.

The design says "fix task after 14 days over" without saying over what; this reads it as the budget itself and keeps the 70% line as the early alarm.
No workflow runs it: a scheduled run is the maintainer's to add in the weekly batch.

## The tool rows

`run/tool-gates.py zizmor|actionlint|osv [root]` runs a pinned tool as a gate; its docstring owns the exit statuses.
It stands in for custom workflow rules and for an online advisory lookup (factory plan r5, "P3a-ROWS"); it never fetches anything.
A refusal that is not a finding exits 2 and prints `tool-gates: refused (TYPE)`, with TYPE one of `missing-tool`, `wrong-version`, `missing-database`, `stale-database` or `tool-error`, so a runner without the tool, or with an old database, cannot fold its row to a pass.

| Tool | Pin | Where the pin lives | Checked by |
| --- | --- | --- | --- |
| zizmor | 1.29.0 | `PINS` in `run/tool-gates.py` | `zizmor --version` before every run |
| actionlint | 1.7.12 (the latest release, 2026-03-30) | the same | `actionlint --version` |
| OSV-Scanner | 2.6.0 (35 days old on 2026-10-19; 2.7.0 is 7) | the same | `osv-scanner --version` |

A runner row also lists the version probe under `tools`, so the runner's cache key moves with the pin.

**Install.**
Nothing here installs them, and the CI jobs do not either (see "Not in CI" below).

```bash
uv tool install zizmor==1.29.0
curl -fsSLO https://github.com/google/osv-scanner/releases/download/v2.6.0/osv-scanner_linux_amd64
```

- `osv-scanner_linux_amd64` has sha256 `ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108` and `osv-scanner_darwin_arm64` has `98c460dcd37de25819babd757d04542045b6243113e209edcd4d89fedb0256b4`, from the release's `osv-scanner_SHA256SUMS` (read 2026-10-19). Verify before `chmod +x`.
- actionlint 1.7.12 is the release tarball `actionlint_1.7.12_linux_amd64.tar.gz` (sha256 `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`) or `brew install actionlint` on the Mac.

**The offline database** is OSV's per-ecosystem `all.zip` under the directory `OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY` names.
The gate requires that variable, requires at least one `all.zip` in it, and refuses `stale-database` when the oldest is over 7 days old (`MAX_DB_AGE_DAYS`), which is the refresh cadence this assumes.
Refreshing is its own step, run where the network is allowed and never from the gate:

```bash
OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY=$HOME/.cache/osv-db osv-scanner scan source --offline --download-offline-databases -r .
```

This was not run for this change: the sandbox it was built in answers 403 for `osv-vulnerabilities.storage.googleapis.com` (measured 2026-10-19), and `osv-scanner` exits 127 there rather than reporting a clean tree.
The cases here used a copy of an existing 2026-10-13 database (npm 218 MB, crates.io 3.6 MB) with the same layout.
A tree that uses an ecosystem the directory lacks is refused `missing-database`; `.ci/test-tool-gates.py --real` shows it with a `Cargo.lock` and an npm-only database.

**Measured on this machine** (arm64 macOS, 2026-10-19; the load average read 10 to 12 during the loaded runs, which is why they differ):

| Row | Wall time |
| --- | --- |
| `zizmor` over this repository's 8 workflows | 0.13 to 0.27 s (3 runs) |
| `actionlint` over the same | 0.08 to 0.09 s (3 runs) |
| `osv`, one lockfile, npm database | 6.5 to 6.8 s (3 runs, load not recorded, user CPU 6.6 s); 8.4 to 11.9 s (13 runs under load); 7.4 to 12.5 s with a `Cargo.lock` beside it (5 runs under load) |

The advisory row does not hold the 10-second pre-push budget by itself: it parses the whole 218 MB npm archive on every call, so it is CPU-bound and loses time to any other work running beside it.
The pre-push hook starts four gates together, so the row is only affordable when the push touches a lockfile.
`inputs` alone did not do that: the runner selects every row when a changed path is named by no row, so a change to a source file that no row names still ran the advisory row.
The row therefore carries `scoped = true` (`run/gate.py`, docstring), which keeps it out of an unnamed path and out of another file under `.github/`, and selects it for a changed lockfile or a change to the registry.
Measured 2026-10-19 on this machine, with the real osv-scanner 2.6.0 and the 218 MB npm database, three runs each of `gate.py run` over a registry holding this row and a `docs` row (load average 5 to 6.6):

| The change touches | Before (inputs only) | After (`scoped = true`) |
| --- | --- | --- |
| no lockfile (`src/app.py`, named by no row) | 6.82 to 7.14 s; the row runs | 0.12 s; the row is not selected |
| a lockfile | 6.87 to 7.17 s | 6.74 to 7.61 s |

A push that touches a lockfile still pays the whole row, which was 8.4 to 12.5 s under the heavier load above, so scoping removes the row from every other push and does not make a lockfile push fit.
Whether the hook should call it at all, or whether a smaller database (one ecosystem, or the advisories for packages in the lockfile) should be built at refresh time, is the maintainer's to decide; the figures are the input.
No base and no change still select a scoped row, because nothing is proved unaffected then, so a hook that calls the runner must pass a real base.

**Against the custom workflow rules.**
`.ci/test-workflow-policy.sh` bans eight shapes by `grep`.
zizmor 1.29.0, offline, on its default persona, flags 3 of them (measured 2026-10-19, one fixture workflow per shape):

| Planted shape | zizmor, default persona | zizmor, `--persona auditor` |
| --- | --- | --- |
| `pull_request_target` trigger | `dangerous-triggers` | same |
| `workflow_run` trigger, list form | `dangerous-triggers` | same |
| `secrets: inherit` | `secrets-inherit` | same |
| `runs-on: self-hosted`, and the label list | not flagged | `self-hosted-runner` |
| a named secret, and one beside `GITHUB_TOKEN` | not flagged | `secrets-outside-env` for both |
| `issue_comment` trigger | not flagged | not flagged |

So the premise that the planted refusals are mostly zizmor rules holds for 3 of 8 on the default persona and 7 of 8 on the auditor persona, which zizmor documents as accepting false positives.
Nothing is retired here.
The proposal for the weekly batch: keep `workflow-policy` for the five shapes zizmor does not cover, and drop only its `pull_request_target`, `workflow_run` and `secrets: inherit` lines once the zizmor row has been promoted out of shadow.
Running zizmor's auditor persona in place of the custom rule is the alternative; it needs a decision on its false positives first.
The row runs `--offline`, so zizmor's audits that need the GitHub API (known-vulnerable actions, impostor commits) do not run.

**Findings on this repository** (shadow rows are allowed to be red):

- `zizmor` exits 13 with 11 `artipacked` findings: each `actions/checkout` step lacks `persist-credentials: false`.
- `actionlint` exits 1 with 26 findings, all of two kinds the workflows depend on and 1.7.12 does not know: `job.workflow_repository` and `job.workflow_sha` (24), and the `vulnerability-alerts` token scope (2). They are false positives against the tool's tables, not defects in the workflows.

**The rows**, in a caller's `.github/gates.toml`, in shadow until the promotion rule in `run/gate.py` lets them block.
The advisory row's `database` probe prints the day and each database file's size and mtime, so a verdict is reused only within a day and only against the database it was judged on.

```toml
[[gate]]
id = "workflow-zizmor"
inputs = [".github/workflows/**"]
tools = { zizmor = "zizmor --version" }
run = 'python3 "$GATES_DIR/run/tool-gates.py" zizmor .'
shadow_since = 2026-10-19

[[gate]]
id = "workflow-actionlint"
inputs = [".github/workflows/**"]
tools = { actionlint = "actionlint --version" }
run = 'python3 "$GATES_DIR/run/tool-gates.py" actionlint .'
shadow_since = 2026-10-19

[[gate]]
id = "advisories-osv"
inputs = ["**/*lock.json", "**/*lock.yaml", "**/*.lock", "**/go.mod", "**/requirements*.txt"]
scoped = true
tools = { osv = "osv-scanner --version", database = 'python3 "$GATES_DIR/run/tool-gates.py" osv-stamp' }
run = 'python3 "$GATES_DIR/run/tool-gates.py" osv .'
shadow_since = 2026-10-19
```

`.ci/test-tool-gates.py` runs these rows through `run/gate.py` against stub tools and folds each to `shadow`.
Five registry rows (`workflow-zizmor`, `workflow-actionlint`, `advisories-osv`, `advisories-osv-stale`, `tool-missing`) prove the script by execution with stub tools, because the `gates-proof` job installs none of the real ones.

**Not in CI.**
`ci.yml` installs none of the three tools, so `.ci/check.sh` runs the stub cases only, and `.ci/test-tool-gates.py --real` (the pinned binaries on `PATH` and a database directory) is run by hand:

```bash
OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY=$HOME/.cache/osv-db python3 .ci/test-tool-gates.py --real
```

Installing the pinned tools in the `shell` job and adding `--real` to `check_shell` is a workflow edit, so it waits for the maintainer's weekly batch.
No workflow calls `run/tool-gates.py` either.

## The text rows

`run/pr-text.py` scans a pull request's title and body: `gh pr view N --json title,body | python3 run/pr-text.py`. Its docstring owns the rules and the exit statuses (0 clean, 1 a finding, 2 could not decide).
Each finding is one line, `pr-text: body:3: em-dash: "..."`, with the line of the title or body.

| Rule | What it refuses | Where the rule is written |
| --- | --- | --- |
| `em-dash` | U+2014 | "Never use the em dash" in the maintainer's global agent instructions; the draft skill's hard rule 1; VOICE.md, "What to avoid" |
| `slop-phrase` | 14 phrase patterns, among them "delve", "seamless", "game-changing", "at the end of the day", "in conclusion", "best-in-class" | the draft skill's hard rule 4; VOICE.md, "What to avoid" |
| `attribution` | a `Generated with` or `Co-authored-by` line naming an agent | the commit-msg hook in `system-maintenance/git-hooks`, whose vendor list it copies; a pull request body can become the squash commit's message |
| `home-path` | `/Users/<name>` or `/home/<name>`, except `runner`, `Shared` and the placeholders `user`, `username`, `name`, `you`, `me` and `example` | the exposure gate, which refuses this machine's own home path on a push but reads no pull request text |
| `security-marker` | a count of alerts, advisories, vulnerabilities, findings or CVEs with a security source (the shape `exposure-scan.py` refuses), a Dependabot, code-scanning or secret-scanning alert by number, a GHSA id, a CVE id | `exposure-scan.py`, `content.vulnerability-count`; the pull request line stays neutral about what a finding was |

The factory plan counts 14 slop classes ("Docs and writing, no AI slop") and none of the records read for this change lists them, so the slop rule is the phrases the two written voice rules name, and not a list invented here.
The plan's own bar for a deterministic rule excludes soft phrases, so "leverage" as a verb, "robust" as filler and empty intensifiers are left to review: `.ci/test-pr-text.py` pins a body that uses the first two as clean.
Hidden characters are left out because `watermark-scan.py` already owns them for files, and a pull request body is the one place it does not read; adding that is a separate row.

The text is not in the tree, so the runner's cache key would reuse a pass after the body changed.
The row names the text as a tool probe, whose output enters the key, and `.ci/test-pr-text.py` shows a pass followed by a fresh fail on one tree when only the text changed:

```toml
[[gate]]
id = "pr-text"
inputs = ["**"]
tools = { pull-request = 'gh pr view "$PR_NUMBER" --json title,body' }
run = 'gh pr view "$PR_NUMBER" --json title,body | python3 "$GATES_DIR/run/pr-text.py"'
shadow_since = 2026-10-19
```

Five registry rows (`pr-text-slop`, `pr-text-em-dash`, `pr-text-attribution`, `pr-text-home-path`, `pr-text-security-marker`) prove the script by execution, one per rule class, under the surface "Pull request text" and not "Security", so that adding them does not widen what `run/permissions-diff.py` protects.
No workflow runs the script: fetching the text needs a step with `pull-requests: read`, which is the maintainer's weekly batch.

## The scratch rows

`run/scratch-guard.py [--tree-only] [--root DIR] -- COMMAND` runs a gate or a suite with `TMPDIR` set to a directory of its own, removes that directory afterwards, and refuses (exit 1) when a path is new under the tree or directly under the system temporary directory.
A run that cleans up writes only to `TMPDIR`, so that is the one place it may leave files; `.ci/test-scratch-guard.py` pins that a file in `TMPDIR` passes and a stray one beside it does not.
Build output and caches (`.git`, `node_modules`, `target`, `.venv`, `__pycache__`) are not walked, and only a new path counts, not a changed or removed one.
A shared `/tmp` has other writers, so a machine that is not a job's own passes `--tree-only`; the guard's cost on this tree is 0.05 s.

```toml
[[gate]]
id = "scratch-clean"
inputs = ["**"]
run = 'python3 "$GATES_DIR/run/scratch-guard.py" --tree-only -- bash .ci/check.sh shell'
shadow_since = 2026-10-19
```

Two registry rows (`scratch-guard-tree`, `scratch-guard-temp`) prove it.
It also found a defect while being built: five of the seven Python suites run under `.ci/check.sh shell` made a fixture directory with `tempfile.mkdtemp()` and never removed it, so each run left one entry in `TMPDIR` (measured 2026-10-19 with a private `TMPDIR`: `test-docs-truth`, `test-tool-gates`, `test-permissions-diff`, and the two suites of this change).
They now remove it, and the same measurement reads 0 for all seven.
The shell suites were not measured: `.ci/check.sh shell` takes about four minutes.
Under the guard such a leftover lands in the guard's own directory and is removed with it, so the guard names a write outside `TMPDIR` and does not name a suite that forgot to clean up inside it.

## The change rows

`run/change-gates.py contracts|migrations|facts [--base B] [root]` judges one change against what a repository declares in `.github/changes.toml`.
Its docstring owns the exit statuses (0 nothing to name, 1 a finding, 2 could not decide, with a typed `refused (TYPE)` line) and the declaration keys.
Nothing is guessed from the tree: a repository that declares nothing gets `refused (undeclared)`, so a row cannot pass over a file it was never told about.

**What the callers declare today** (read 2026-10-19 across the fleet's clones):

| Row | Found | So the row takes |
| --- | --- | --- |
| contract snapshots | `skills/<name>/SKILL.md` in `chrome-devtools-axi`, `gh-axi`, `quota-axi`, `tasks-axi` and `pointback`, each named as the CLI contract. Four are generated from the CLI's help text and drift-checked in the caller's own tests (`build:skill -- --check`, `test/skill.test.ts`); `pointback`'s is hand-written and its `AGENTS.md` says to change it "with any command, flag or field it names". No JSON schema file and no exported API snapshot exists. | a `snapshot` and either `sources` (the hand-written case) or a `command` that prints it (the generated case) |
| migrations | one directory of files: `halderworks-site` `migrations/NNNN_name.sql`, applied by `wrangler d1 migrations apply`. `quoth`'s `migrate.rs` moves folders between bundle identifiers and `quoth`'s and `papertrace`'s SQLite schemas are code, so they have no files to protect. | a `dir`, a `pattern` and an optional `dry_run` |
| facts | no repository declares one. | a `fact` with its superseded statements |

```toml
[[contract]]
id = "pointback-cli"
snapshot = "skills/pointback/SKILL.md"
sources = ["bin/**", "src/cli.js"]        # or a command that prints the contract

[[migrations]]
id = "d1"
dir = "migrations"
dry_run = "sqlite"                         # or a shell command; SCRATCH and NEW_MIGRATIONS are set

[[fact]]
id = "zizmor-pin"
now = "1.30.0"
was = ["1.29.0"]                           # each is swept once, in the change that adds it
decision = "maintainer decision 2026-10-19, README.md"
allow = ["CHANGELOG.md"]                   # files that may quote the old statement as history
```

**Contracts.**
With `sources`, a change to one of them is refused unless the same change touches the snapshot, so the check needs no build and no tool.
Its limit is that a source edit that leaves the contract alone still has to touch the snapshot: on `pointback`'s history 13 of the 21 commits that touched `bin/pointback.js` or `src/cli.js` did not touch `SKILL.md` (nine are `fix(...)`, one `chore`, three `feat`), so coarse `sources` raise many alarms and a caller should list the files that define commands and flags.
That is a reason for the shadow window, and for `command` where the contract can be printed: the output must equal the snapshot byte for byte, whatever changed, with no false alarm of that kind.
The four generated skills already run that comparison themselves, so no row is proposed for them.

**Migrations.**
A migration file that is edited or deleted on the change is refused when it is on the base (`git diff B...HEAD`, a rename counting as a delete and an add), because its database has applied it and a fix is a new migration.
Ordering a new file after the last landed one is a further rule this does not make.
The dry run applies the directory's files in name order to a scratch database, so a new file that breaks against the landed schema is named with SQLite's message.
`sqlite` is built in because D1 is SQLite, which also lets the row run with no tool installed; a shell command covers any other engine; `wrangler d1 migrations apply DB --local --persist-to "$SCRATCH"` would be the D1 form, and was not run here.
A migration is repository code, so the built-in dry run denies `ATTACH`, which would let one write a file elsewhere on the machine.
Measured on a clone of `halderworks-site` with the row declared: a valid `0002` passes, one that indexes a missing column is named ("no such column: nope"), and an edit of `0001_waitlist.sql` is named.

**Facts.**
A fact is declared by the change that supersedes it: the old statement moves into `was`, the new truth into `now`, and `decision` says where the decision is recorded.
A statement is a literal string on one line of a tracked text file, at word boundaries (`1.28.0` does not match `1.28.01` or `11.28.0`); a file listed in `allow` and the declaration file are not swept, and a binary file or one over 1 MB is not read.
Only a `was` entry that this change added is swept (the base's declaration is the comparison), so a decision made last month does not re-fire, and without `--base` every entry is.
Literals, not paraphrases, because a sweep that cannot be reproduced cannot be a gate: it will not find a sentence that says the same thing in other words, and it keeps `was` as short as is unambiguous.
Measured over this repository when it tracked 88 files, with `was = ["1.29.0"]`: 17 lines across 5 files in 0.07 s.

**The rows**, in a caller's `.github/gates.toml`, in shadow until the promotion rule in `run/gate.py` lets them block, and `scoped = true` so a change to a path they do not name does not select them (`inputs` is the caller's list of its own paths):

```toml
[[gate]]
id = "contracts"
inputs = ["bin/**", "src/cli.js", "skills/pointback/SKILL.md", ".github/changes.toml"]
scoped = true
tools = { base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/change-gates.py" contracts --base "$BASE_SHA"'
shadow_since = 2026-10-19

[[gate]]
id = "migrations"
inputs = ["migrations/**", ".github/changes.toml"]
scoped = true
tools = { base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/change-gates.py" migrations --base "$BASE_SHA"'
shadow_since = 2026-10-19

[[gate]]
id = "facts"
inputs = [".github/changes.toml"]
scoped = true
tools = { base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/change-gates.py" facts --base "$BASE_SHA"'
shadow_since = 2026-10-19
```

The `facts` row runs when the declaration changes, which is when a decision is recorded; a new file that states an old fact later is not caught by it.
`.ci/test-change-gates.py` runs these three rows through `run/gate.py`, folds each red one to `shadow`, and shows a change to `src/app.py` leaves them unselected.
Five registry rows (`contract-snapshot-sources`, `contract-snapshot-command`, `migration-immutable`, `migration-dry-run`, `fact-change-sweep`) prove the script by execution.
No workflow calls it: `BASE_SHA` and a `.github/changes.toml` come from a caller, and a step is the maintainer's weekly batch.

## The slop rows

`run/slop-rules.py ROW [--base B] [root]` holds one change to the slop a tree alone can answer.
With `--base` a row judges what `git diff B...HEAD` added, so a stale line already on the base is not the change's; without it, every file under `root`.
Its docstring owns the rules and the exit statuses (0 nothing to name, 1 a finding, 2 could not decide), and each finding is one line, `slop-rules: tests: path:LINE: what`.
Seven rows, each a registry entry and a row of a caller's `.github/gates.toml` in shadow and `scoped`, so that a change to a path the row does not name does not select it.

**Source-text assertions (`tests`).**
"A test asserts behaviour through the public entry point, never on source text" is a line of the maintainer's global agent instructions ("Building"), and the learning-loop report's generation-time list carries it as item 7.
`~/STANDARDS.md`, "Testing", does not hold it: the clause the fleet enforces lives in the global instructions, so that is what the row cites.
A Python test is read as a syntax tree: an `assert` or an `assert*` call over the text of a source file, whether the read is inline or reached through a variable, a derived result (`hit = re.search(p, text)`) or a file handle.
A shell test is a `grep` (or `cat` into `grep`) whose file operand is a source file, and a JavaScript, TypeScript, Rust or Go test is a read of a source file in a test file that also asserts.
A source file has a code extension and is not itself a test or a fixture, and a file the test writes is not one.
Not judged: a test in a file whose name does not say it is a test, a config file such as a workflow's YAML (a policy lint over config is a different act), and an assertion reached through a helper the syntax tree cannot see.

**Symbol truth (`symbols`).**
A backticked span in an added line of Markdown, a comment or a Python docstring that names a path, a long flag or a `name()` function the tree does not have.
The path half is `instruction-truth/path-truth.py`'s own resolution, moved from instruction files to every changed doc and comment, with its `(absent here)` waiver.
Two further limits, both measured below: a path is judged only when it starts in a directory of this tree, because one that starts elsewhere (`internal/git/git.go`) is another repository's file, and a path that a script spells out (`REGISTRY = ".github/gates.toml"`) is a path the tree names.
A flag or function resolves when some file has the word outside a backtick span, so a second doc quoting it proves nothing; Markdown contributes its fenced blocks only.
A builtin needs no definition, and a bare file name is not judged, because on this tree it names another repository's file as often as a stale one.

**The impeccable rules, mapped to `~/STANDARDS.md`.**
The sources name the linter (`npx impeccable detect --json`, "Frontend design defaults") and the seven rule families it reported over four trees (learning-loop report, 5.5); no source lists its other rules, and it is not installed here, so it was not run and no rule of it was copied.
Each family and each line of the "Frontend design defaults" is below with the row that enforces it or the reason it has none.
The bar is that a row fires on a literal or on arithmetic over literals and never on a judgement of taste: a rule that needs a render, a token set or the captain's eye is not a row.

| Rule | STANDARDS clause | Row |
| --- | --- | --- |
| bounce easing | "Motion uses exponential ease-out curves, never bounce or elastic" | `easing`: a `cubic-bezier` whose y values leave 0..1, a `linear()` stop that does, a named back, bounce or elastic ease, `animate-bounce` |
| low contrast | "Keep body text contrast at 4.5:1 or better, including placeholders" | `contrast`: one block that declares a literal color and background (hex, `rgb()`, `oklch()`, `white`, `black`); 3:1 when the block sets large text; a disabled block is exempt |
| em dash in body text | "Never use the em dash" (global agent instructions and VOICE.md; `~/STANDARDS.md` has no line for it) | `em-dash`: U+2014 in an added line of a UI file, a `LOCAL FORK` header exempting it as `docs-em-dash` does for Markdown |
| arbitrary z-index | "a semantic z-index scale, never arbitrary `999`/`9999`" | `z-index`: three or more nines, or 2147483647, in `z-index`, `zIndex` or `z-[...]` |
| reduced motion | "honor `prefers-reduced-motion` always" | `reduced-motion`: added motion when no file of the tree names `prefers-reduced-motion`, `motion-reduce:` or `motion-safe:` |

Left out, with why:

| Rule | Why it is not a row |
| --- | --- |
| flat type hierarchy (12 findings in the report) | no clause in `~/STANDARDS.md`; the report says one tree sets every role at 16px possibly by design, so it needs the captain's eye |
| side-border accent on a rounded box (8) | stylistic, no clause |
| `transition: width` (3) | plausible, but no clause: "Performance" bounds growth, not layout properties |
| cramped padding (1) | the report did not judge it; "spacing comes from the design tokens" would need the repository's token set |
| contrast across rules, through `var()`, inherited or on a translucent color | needs a render; the report counted its three contrast findings as unverified without one |
| "Use OKLCH for new color work" | whether work is new, against "Match the file you are editing", is a judgement |
| spacing from tokens | needs a token declaration each repository would have to make |
| body line length 65-75ch, `text-wrap: balance` on headings | which element is the body or a heading is a judgement |
| popovers through the popover API or a portal, never `position: absolute` in an overflow container | needs layout |

Soft prose phrases ("leverage" as a verb, "robust" as filler, empty intensifiers) stay out of every row, for the reason the text rows give: the report measured 2.8% precision for them.
The `contrast` row reads the house syntax, `oklch()`, through the published OKLab matrices; a color it cannot read as one opaque literal makes the block undecided, never a finding.
Its limits are the ones above: CSS blocks in a stylesheet, a `<style>` element or a Vue or Svelte block, and not a style object in script.

**Measured on this repository's 30 commits** (each row over `git diff C^...C` at that commit, 2026-10-19): `tests`, `easing`, `contrast`, `z-index`, `em-dash` and `reduced-motion` named nothing, which is what a tree with no UI files and no source-text assertion should print, so this history cannot show their precision.
`symbols` named 58 spans with the path rule as first written, 24 once a path had to start in a directory of the tree, and 2 once a path that a script spells out resolved: `--help` in a sentence about a tool's own flag, and a path of `pointback` that the README quotes.
Neither is stale, so on this history the row has 0 true findings and 2 false alarms, and it cannot be promoted on this tree's evidence; the shadow window of `run/gate.py` is what measures it, and an upheld false alarm restarts that window.
`symbols` over the whole tree takes 0.3 s and each UI row under 0.2 s on a megabyte of input.

```toml
[[gate]]
id = "slop-tests"
inputs = ["**/test_*.py", "**/test-*.sh", "**/*.test.*", "**/*_test.*", "tests/**"]
scoped = true
tools = { base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/slop-rules.py" tests --base "$BASE_SHA"'
shadow_since = 2026-10-19

[[gate]]
id = "slop-symbols"
inputs = ["**/*.md", "**/*.py", "**/*.sh"]
scoped = true
tools = { base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/slop-rules.py" symbols --base "$BASE_SHA"'
shadow_since = 2026-10-19

[[gate]]
id = "ui-easing"
inputs = ["**/*.css", "**/*.tsx", "**/*.jsx", "**/*.html"]
scoped = true
tools = { base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/slop-rules.py" easing --base "$BASE_SHA"'
shadow_since = 2026-10-19
```

The other four UI rows (`contrast`, `z-index`, `em-dash`, `reduced-motion`) take the same shape with their name in `run`.
`reduced-motion` reads the whole tree for a handler, so its `inputs` must also name the files that could hold one, or a change to the stylesheet that holds it would not re-run the row.
`.ci/test-slop-rules.py` runs the rows through `run/gate.py`, folds each red one to `shadow`, and shows a change to an unrelated file leaves all seven unselected.
Nine registry rows prove the script by execution (`slop-tests-source-text`, `slop-symbols-path`, `slop-symbols-function`, `slop-symbols-flag`, `ui-easing`, `ui-contrast`, `ui-z-index`, `ui-em-dash`, `ui-reduced-motion`).
No workflow calls it: `BASE_SHA` comes from a caller, and a step is the maintainer's weekly batch.

## The fake rows

`run/fakes.py undeclared|counterpart|stale [--base B] [--today D] [root]` holds a repository's test doubles to what it declares in `.github/fakes.toml`.
Its docstring owns the exit statuses (0 nothing to name, 1 a finding, 2 could not decide, with a typed `refused (TYPE)` line), and each finding is one line, `fakes: ROW: ...`.
It is the first slice of the factory plan's real-tests row (r5, "P3a-RT"): the declaration, the fake lint and the stale-fake refusal.
Mutation proof on changed code is the second slice (see "The mutation rows"), and GitHub record-and-compare with a daily real smoke a later one.

**What this replaces.**
The sandbox report's task W8 (s1, section 9) was one real-tests policy task: a `fakes.toml` registry, a weekly contract or record-and-compare check per fake, a census gate that flags a new unregistered mock, and a seeded-defect proof per test, accepted when the census gate fails on a new `jest.mock` and a drifted fake fails its weekly check.
The plan folded W8 into P3a-RT, and these rows are W8's registry and census gate.
The census is widened from mock libraries to hand fakes and test-written programs, as the learning-loop report's fake lint asks (l1, 10.3), and the drift check is a declared freshness date here, which the record-and-compare slice will move.
The plan review found "fake freshness not a merge refusal" in the earlier plan (r4, 4, R19); `stale` is that refusal.

**The declaration.**

```toml
[[fake]]
id = "gh"
paths = ["tests/**", ".ci/fixtures/gh-stub.py"]   # where the double lives or is installed
names = ["gh"]                                    # what the lint calls it, below
stands_for = "the GitHub REST API, rate-limited for content-creating requests"
real = "bin/sync.py"                              # the code that talks to the real thing
real_name = "gh"                                  # optional: the word it names it by
compare = "tests/compare-gh.sh"                   # the check against the real system
recorded = 2026-10-19                             # the last passing record-and-compare
max_age_days = 7                                  # optional, default 7
```

| Key | Why it is there |
| --- | --- |
| `id` | Names the fake in every finding and keeps two declarations apart, as in `.github/changes.toml`. |
| `paths` | "the fake's path" (l1, 10.2 rule 3) and "outside a path and symbol registered" (10.3): globs of the files that hold or install the double, so a declared name is allowed only there. |
| `names` | The "symbol" of 10.3: the names the lint gives the doubles this fake covers. |
| `stands_for` | What real thing the fake stands in for, l1's "boundary" and "reason"; prose for the reviewer, checked only to be present. |
| `real`, `real_name` | The file of this tree that calls the real thing, and the word it calls it by. An external API cannot be probed offline, so the tree's own call is what `counterpart` checks, and a declaration cannot outlive the seam it fakes. |
| `compare` | "the record-and-compare command" (10.2 rule 3, 10.4), the source of truth to compare against. Nothing here runs it; the record-and-compare slice will. |
| `recorded` | "a recording date": the day the last compare passed, which `stale` reads. |
| `max_age_days` | "its schedule"; the default is 7 because every boundary in l1's 10.4 table is compared weekly. |

Left out: a closed list of boundary classes, because rule 2's "paid or rate-limited API or the phone channel" is a judgement a reviewer makes from `stands_for`, and a field for a red compare, which the compare slice will write.
A key the table does not name is refused (`bad-declaration`), since a misspelt `max_age` would otherwise fall back to 7 days without a word, and `recorded` must be a bare TOML date.

**What a test double is, per language.**
The callers' test files (eleven clones that call these workflows, read 2026-10-19, and this tree) are shell, Python, TypeScript and JavaScript, Go and Rust, so those five are judged.
A test file is what `run/slop-rules.py` calls one, plus `conftest.py` and a Rust file from its first `#[cfg(test)]` line; a fixture directory and a vendored tree (`node_modules`, `vendor`, `target`) are not judged.
Each double gets a name, and a declaration covers a name in its `paths`:

| Kind | Recognised by | Named by |
| --- | --- | --- |
| hand fake | a type, class, struct, trait or function (and a `const`, `let` or `var` in JavaScript) whose name starts with Fake, Stub, Mock or Dummy and then a capital, a digit, `_` or the end of the word; a shell `fake_x` or `stub_x` function | its name: `FakeHost`, `fakeGh`, `stub_gitleaks` |
| mock | a mocking library's call: Python's `patch`, `Mock`, `MagicMock`, `monkeypatch`, `mocker`, `responses` and `freeze_time`; `vi` and `jest` mocks, spies and fake timers, `sinon`, `nock`, `node:test`'s `mock`, `mockIPC`, `page.route` and msw's `setupServer`; Go's gomock and testify's `mock.Mock`; Rust's mockall | its first argument when that is a string literal, the module, target or host it replaces (`node:child_process`, `pkg.fetch.get`); the call otherwise (`vi.fn`) |
| stand-in program | a file a test writes from a `#!` literal (a shell redirect or heredoc, `write_text`, `writeFileSync`, `os.WriteFile`, `fs::write`); a file a shell test makes executable in a directory it puts on `PATH` or another `*_PATH`, or behind a `*_BIN` seam; a `#!` source held in a variable named fake, stub, mock or shim | the file's basename, through the variable that holds it when that spells one |

The starting point was the learning-loop report's classifier (l1, A7), and the measurement below changed it in five places:

- A7's `Null`, `No` and `Noop` prefixes are left out: the callers' tests define none (0 across the eleven clones), so they add a pattern nothing measured.
- `chmod +x` alone is not a stand-in, because this tree's tests make the hooks they test executable (`.ci/test-secret-scan.sh`), and those are the real thing; putting the directory on a path is what makes a written file stand in for a program.
- A `>` inside a quoted string is not a redirect, so a printf format that writes `: > "%s/blocked"` into a stub does not name it `blocked`.
- A compared literal (`=== "win32"`) or one with a space is not the program's path, and a call, an object or an attribute is not the variable that holds it.
- A `#!` that a test only looks for (`strings.Contains(string(b), "#!/bin/sh")`) writes nothing.

Not judged: a program written by a command held in a string for a helper to run (`.ci/test-lock.sh` writes `bin/gh` that way); a `#!` on a later line than its write; a shell function that overrides a command (`git() {`), which A7 counted but which cannot be told from a test's own helper without a list of commands; Go's `httptest.NewServer`, which A7 counts as loopback real I/O; a double defined in a helper module outside the test files.

**Measured** over the tracked test files of the eleven clones and this tree's own, 2026-10-19, with `--base` of the empty tree: 269 doubles in 102 files, none declared, because no caller has a `.github/fakes.toml` yet.

| Language | Files | Hand fakes | Mocks | Stand-in programs |
| --- | --- | --- | --- | --- |
| TypeScript and JavaScript | 46 | 16 | 102 | 3 |
| shell | 27 | 7 | 0 | 77 |
| Python | 17 | 7 | 26 | 6 |
| Rust | 9 | 20 | 0 | 1 |
| Go | 3 | 2 | 0 | 2 |

A fixed-seed sample of 30 findings read against its source line held 30 test doubles; one sat in a vendored `node_modules` a caller tracks, so the row now skips vendored trees, which took 8 findings out of the count.
That is precision on whole trees; on a change the shadow window of `run/gate.py` measures it, and an upheld false alarm restarts that window.
Most stand-in programs are local binaries (`sleep`, `stat`, `ps`, `cat`, `xargs`, `defaults`) or the repository's own scripts, which l1's rule 2 allows no fake for, so they are conversion tasks rather than declarations; `gh`, the commonest at 6, is the allowed kind.
Each tree takes under 0.5 s.
`.ci/test-fakes.py` carries the examples it tests, so this tree's own run names 7 more; no row runs on this tree.

**The rows**, in a caller's `.github/gates.toml`, in shadow until the promotion rule in `run/gate.py` lets them block, and `scoped = true` so a change to a path they do not name does not select them:

```toml
[[gate]]
id = "fakes-undeclared"
inputs = ["**/test_*.py", "**/*_test.go", "**/*.test.*", "**/test-*.sh", "tests/**", "test/**", ".github/fakes.toml"]
scoped = true
tools = { base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/fakes.py" undeclared --base "$BASE_SHA"'
shadow_since = 2026-10-19

[[gate]]
id = "fakes-counterpart"
inputs = [".github/fakes.toml", "tests/**", "bin/sync.py"]
scoped = true
run = 'python3 "$GATES_DIR/run/fakes.py" counterpart'
shadow_since = 2026-10-19

[[gate]]
id = "fakes-stale"
inputs = [".github/fakes.toml", "tests/**", "bin/sync.py"]
scoped = true
tools = { today = 'date -u +%F' }
run = 'python3 "$GATES_DIR/run/fakes.py" stale'
shadow_since = 2026-10-19
```

`counterpart` and `stale` name the declaration, every fake's `paths` and every `real` file as their inputs, so a change to a test that uses a fake, or to the code it fakes, selects them: that is "a PR whose selected tests use a registered fake" (l1, 10.3).
`stale` keys on today's date through its `tools` probe, so yesterday's pass is not reused once a fake goes past its freshness.
With `--base`, `undeclared` judges only the lines a change added, so a repository's existing doubles do not turn its next change red; the change that adds or edits `.github/fakes.toml` sweeps every test file, so deleting a declaration cannot pass, and that first sweep is where existing doubles become declarations or conversion tasks (l1, 10.3: "its first run is a baseline").
l1 asks for a stale fake's verdict to be `error`; `run/gate.py` records any non-zero exit as `fail`, which refuses in the fold the same way, so the runner is unchanged here.
`.ci/test-fakes.py` runs the three rows through `run/gate.py`, folds each red one to `shadow`, and shows a change to an unrelated file leaves them unselected.
Five registry rows prove the script by execution (`fakes-undeclared-program`, `fakes-undeclared-mock`, `fakes-undeclared-hand`, `fakes-counterpart`, `fakes-stale`).
No workflow calls it: `BASE_SHA` and a `.github/fakes.toml` come from a caller, and a step is the maintainer's weekly batch.

## The mutation rows

`run/mutation.py rust|typescript --base B [--dir D] [--in-place] [root]` is the second slice of the factory plan's real-tests row (r5, "P3a-RT"): mutation proof on changed code through cargo-mutants `--in-diff` and Stryker `--incremental`, with a full run whenever a change deletes tests.
Its docstring owns the exit statuses (0 pass, 1 a mutant survived, 2 could not decide, with a typed `refused (TYPE)` line), and each finding is one line, `mutation: survived: FILE:LINE:COL: ...`.
GitHub record-and-compare and the daily real smoke are a later slice.

**What it judges.**
A mutant counts when it touches a line `git diff B...HEAD` adds or rewrites under D: cargo-mutants filters by `--in-diff`, and Stryker by `--mutate FILE:A-B` per changed range.
Stryker 10.0.0's `--incremental` report also carries reused mutants outside `--mutate` (measured 2026-10-19 on the fixture: a range holding one blank line tested 0 mutants and reported 7), so the row keeps only the mutants whose span touches a changed line.
A survivor is cargo-mutants' `MissedMutant`, or Stryker's `Survived` or `NoCoverage`; a timeout counts as killed, as Stryker scores it, and a mutant that does not compile is not counted.
Rust test code is never mutated by cargo-mutants, and the TypeScript row mutates `.ts`, `.tsx`, `.mts` and `.cts` files that are not tests, fixtures, declarations or `*.config.*`.

**Deleting or weakening a test.**
`--in-diff` matches only code under test, so a change that removes an assertion and touches no source mutates nothing and passes (r4, section 5).
The row counts three markers in each changed test file at the merge base and at HEAD, following renames, and runs every mutant under D when a count moves the wrong way:

| Language | Files counted | Tests | Assertions | Skips (a rise weakens) |
| --- | --- | --- | --- | --- |
| Rust | every `.rs` file, since tests sit beside the code | `#[test]`, `#[tokio::test]` and any `#[path::test]`, `#[rstest]`, `#[test_case]` | `assert!`, `assert_eq!`, `assert_ne!`, `assert_matches!`, their `debug_` forms, `#[should_panic]` | `#[ignore]` |
| TypeScript | test files by the rule `run/slop-rules.py` uses, in `.ts`, `.tsx`, `.mts`, `.cts`, `.js`, `.jsx`, `.mjs`, `.cjs` | `it(`, `test(` and their `.each`, `.concurrent`, `.sequential`, `.fails`, `.only` forms | `expect(`, `expect.soft(`, `assert(`, `assert.equal(`, `expectTypeOf(` | `.skip`, `.todo`, `.only`, `.skipIf`, `.runIf`, `xit(`, `xtest(`, `xdescribe(` |

Counts rather than removed lines, because a moved or reformatted test removes and re-adds the same line; per file, so a deleted test cannot hide behind a test added elsewhere; a deleted test file counts as zero.
What it misses: an assertion replaced by a weaker one (`assert_eq!(x, 5)` to `assert!(true)`) keeps every count, and a marker in a comment is counted like code.
A test moved between files reads as a deletion and costs a full run, never a wrong verdict.

In the full run a survivor is named only when the same mutant was killed at the merge base, which the row checks by running again on an export of the base, matched by file, function or mutator, replacement and the original source text.
Without that, a test-only change would be charged every survivor the repository already had: on gh-axi, removing one `expect` gave 4,217 survivors at HEAD, all of them alive at the base too, so the row named none.

**Pins.**

| Tool | Pin | Where the pin lives | Checked by |
| --- | --- | --- | --- |
| cargo-mutants | 27.1.0 (the latest release, 2026-06-02) | `PINS` in `run/mutation.py` | `cargo mutants --version` before every run |
| Stryker (`@stryker-mutator/core`) | 10.0.0 (the latest, 2026-08-14; 9.6.1 is the one before) | the same, and the caller's `package.json` as an exact devDependency | the version in `node_modules/@stryker-mutator/core/package.json` |
| `@stryker-mutator/vitest-runner` | 10.0.0 | the same | its `package.json` the same way |

A missing tool is `missing-tool` and any other version is `wrong-version`, never a pass.
Stryker runs from the caller's own `node_modules`, because its vitest runner loads vitest from where it is installed: a copy installed beside the gate would pull in a vitest of its own.
The vitest runner is the only one supported because every TypeScript caller that has tests runs vitest (gh-axi, tasks-axi, quota-axi and chrome-devtools-axi, read 2026-10-19); Stryker 10 needs Node 22 or later.

```bash
cargo install --locked cargo-mutants@27.1.0
npm install -D -E @stryker-mutator/core@10.0.0 @stryker-mutator/vitest-runner@10.0.0   # pnpm add -D -E for a pnpm caller
```

`cargo-mutants-x86_64-unknown-linux-gnu.tar.gz` from the v27.1.0 release has sha256 `dfe6dc37d0342c891d2829b5a695aa57c2d0edecef7e7d0399a30cc6e206411e` (read 2026-10-19), which a runner can verify instead of compiling.
Stryker's incremental file is kept at `node_modules/.cache/gates-mutation/stryker-incremental.json`, because its default `reports/` is untracked litter in a caller that does not ignore it (gh-axi does not).

**A crate that builds from outside its directory.**
cargo-mutants copies D and builds the copy, so a build script that reads a file outside D or an untracked one fails its unmutated baseline.
quoth's does both: tauri-build reads `../out`, `../../../docs/SECURITY.md` and the gitignored whisper sidecar, and the row refused `baseline-failed` after 175 s.
`--in-place` passes cargo-mutants' own flag, which mutates the checkout and restores each file; a run killed mid-mutant leaves that mutant in the tree, so it belongs on a disposable checkout such as a CI job's.
A full run in place still builds the base from an export holding only tracked files, so on quoth it would refuse `baseline-failed` too.

**Measured** on this machine (arm64 macOS, 2026-10-19), against the two budgets the row could run under: the 10-second pre-push budget (r5, the advisory row) and the 5-minute CI budget l1 sets for this check (10.3).

| Run | Mutants | Survived | Wall time |
| --- | --- | --- | --- |
| quoth 4916d48, diff, default copy | (baseline failed) | | 175 s, `baseline-failed` |
| quoth 4916d48, diff, `--in-place`, cold target | 2 tested of 5 found | 1 | 429 s |
| the same, warm target | 2 | 1 | 267 s |
| gh-axi 6e1dcf4 (32 source lines added, 30 removed), diff | 174 | 29 | 22 s, then 11 to 12 s reusing the incremental file |
| gh-axi dc52d29 (159 source lines added, 40 removed), diff | 161 | 22 | 14 s |
| gh-axi, one `expect` removed, full run at HEAD and base | 7,835 | 4,217, all also at the base | 627 s |
| quoth, full run | 3,917 found by `--list` | not run | |
| the fixtures, `.ci/test-mutation.py --real` | 2 to 10 per case | | 2.0 to 13.4 s per case |

Neither row fits pre-push: the smallest real change took 11 s on gh-axi and 267 s on quoth, whose every mutant runs the crate's whole test suite.
Both are PR-time rows.
The diff path fits the 5-minute budget on both callers measured, quoth's with 33 s to spare warm and not at all cold; the full path does not fit it on either, 627 s on gh-axi, and on quoth 3,917 mutants at about 90 s each (267 s held a baseline and 2 mutants), some 98 hours.
l1 runs the full set weekly in the scheduled tier (10.3); whether a test-deleting change waits for that or pays a full run at PR time is the maintainer's to decide, and the figures are the input.
The quoth survivor is real: 4916d48 keeps the sidecar's token out of `WhisperStatus`'s `Debug` output, and replacing that `fmt` with an empty `Ok` passes every test, since an empty string leaks nothing either.

**Go and Python.**
The plan of record names cargo-mutants and Stryker only (r5, P3a-RT and its tool table), so neither language has a row here.
The learning-loop report lists `mutmut` for Python and a seeded-defect fixture per test for shell (l1, 10.3); the plan did not carry either. No source names a tool for Go.

**The rows**, in a caller's `.github/gates.toml`, in shadow until the promotion rule in `run/gate.py` lets them block, and `scoped = true` so a change to a path they do not name does not select them:

```toml
[[gate]]
id = "mutation-rust"
inputs = ["**/*.rs", "**/Cargo.toml", "**/Cargo.lock", ".cargo/mutants.toml"]
scoped = true
tools = { cargo-mutants = "cargo mutants --version", base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/mutation.py" rust --base "$BASE_SHA"'
shadow_since = 2026-10-19

[[gate]]
id = "mutation-typescript"
inputs = ["**/*.ts", "**/*.tsx", "**/*.mts", "**/*.cts", "**/*.test.js", "**/*.test.mjs", "package.json", "vitest.config.*"]
scoped = true
tools = { stryker = "node_modules/.bin/stryker --version", base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/mutation.py" typescript --base "$BASE_SHA"'
shadow_since = 2026-10-19
```

quoth's row adds `--dir apps/desktop/src-tauri --in-place`, and needs the frontend built and the sidecar stubbed first, as its own CI does before `cargo test`.
The `base` probe keys the verdict on the merge base, since the same head judged against another base mutates other lines.
A tracked file under D that differs from HEAD is refused `dirty-tree`, because the tools mutate the working tree and the verdict is recorded for HEAD.

`.ci/test-mutation.py` drives the script against `.ci/fixtures/mutation/stub-tool.py`, which answers as the real tools did on the fixtures beside it, and runs the Rust row through `run/gate.py` in shadow, established and unselected.
Five registry rows prove the script by execution on a real crate and package with that stub (`mutation-rust`, `mutation-rust-weakened`, `mutation-typescript`, `mutation-typescript-weakened`, `mutation-tool-missing`).
The stub is a fake of a local binary, which l1's rule 2 allows for nothing; it stands in only because the CI jobs install neither tool.
`.ci/test-mutation.py --real` puts the same fixtures to cargo-mutants 27.1.0 and Stryker 10.0.0, installing the fixture package's devDependencies with npm:

```bash
python3 .ci/test-mutation.py --real
```

**Not in CI.**
Running `--real` in CI needs a workflow edit, which waits for the maintainer's weekly batch: in `ci.yml`'s `shell` job, a pinned `actions/setup-node` step for Node 22 and a step that downloads the cargo-mutants 27.1.0 Linux tarball and checks the sha256 above, then `python3 .ci/test-mutation.py --real` in `check_shell` in the same change.
No workflow calls `run/mutation.py`.

## Using the hooks on a machine

```bash
system-maintenance/git-hooks/install.sh deploy   # this checkout -> ~/.git-hooks
system-maintenance/git-hooks/install.sh check    # report drift, exit 1 if any
.ci/gitleaks/sync.sh --check <clone>...          # drift in each clone's synced copies and core.hooksPath
```

Deploying is one way on purpose.
`~/.git-hooks` runs on every commit and push, so nothing copies live content back into this repository.

## Checks

```bash
.ci/check.sh                  # shell, python, secrets and gates-proof
.ci/check.sh shell            # one suite
```

`shell` includes `.ci/test-workflow-policy.sh`, which fails on `pull_request_target`, `workflow_run`, `issue_comment` or `self-hosted` anywhere in a workflow file, and on any secret other than `GITHUB_TOKEN`, because anyone can call these workflows once the repository is public.
`gates-proof` runs every gate in `.ci/gate-registry.toml` on a red input it must refuse, naming the expected finding, and a clean input it must pass, then neuters the gate and requires the red input to get through, so a case that does not exercise its gate fails.
It also fails on a workflow, hook or `.ci` script missing from the registry, and needs `gitleaks`, `exiftool`, `yq`, `jq` and `git-lfs` because the cases run the real gates.
`secrets` needs `gitleaks`, `exiftool`, `qpdf`, `ffmpeg` and ImageMagick, and refuses rather than skipping when one is missing.
A gate that skipped what it could not run would report a pass over something it never measured.
