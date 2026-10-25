# gates

The checks that stand between a commit and a public push: a gitleaks pre-push hook and its rules, a scanner that refuses machine-detectable origin marks, the machine-wide git hooks that run both, and five reusable GitHub Actions workflows that re-check the same things in CI.

Licensed under the Apache License 2.0; see `LICENSE` and `NOTICE`.

## What is here

| Path | What it is |
| --- | --- |
| `.githooks/pre-push`, `.gitleaks.toml` | The repository-local secret gate and the canonical rules. Callers also receive `system-maintenance/git-hooks/commit-msg` as `.githooks/commit-msg`. Callers carry byte copies, distributed by `.ci/gitleaks/sync.sh`, and extend the gate through a tracked, executable `.githooks/pre-push.local`, which it runs last. |
| `system-maintenance/git-hooks/` | The machine-wide hooks deployed to `~/.git-hooks` by `install.sh`, including the watermark, exposure and signature gates, which its pre-push runs concurrently. Its `README.md` owns the design and the measurements. |
| `.github/workflows/shared-*.yml` | Reusable workflows: `secret-scan`, `watermark-scan`, `dependency-advisories`, `guard-generated-files`, `no-mistakes-required`, `instruction-truth`, `fakes-smoke`, `gate-shadow`. Each is a bootstrap; see "Pinning". |
| `run/` | What each bootstrap runs from the locked gates commit: `lock.sh`, which decides that commit, and one script per gate. |
| `run/gate.py` | The gate runner: selects a repository's gates from its `.github/gates.toml` and the diff, keys each verdict by the gate's inputs, records verdicts and folds them with review and docs verdicts, and keeps a shadow row from blocking until it earns promotion; its docstring owns the contract. `gate-shadow.yml` runs it in shadow on this repository; see "The shadow job". |
| `run/gate-shadow.sh` | The shadow job's script: runs the runner and its fold on the caller's checkout, writes `shadow-verdict.txt`, and exits 0 whatever the gates decided; see "The shadow job". |
| `run/permissions-diff.py` | The permissions-diff gate over the protected surface; see "The permissions-diff gate". |
| `run/docs-truth.py`, `.github/docs.toml` | The four document rules (owner and reader, commands, byte budget, em dash) and this repository's own owner and reader list; see "The documentation rows". |
| `run/tool-gates.py`, `policy/` | The pinned-tool gates (zizmor, actionlint, offline OSV-Scanner) and the gates-owned configuration they read instead of the tree's; see "The tool rows". |
| `run/ui-gates.py`, `run/ui-gates.mjs` | The UI rows: axe accessibility and Linux pixel baselines in a pinned Chromium; see "The UI rows". |
| `run/pr-text.py` | The pull request text scan over a title and body read as JSON on stdin; see "The text rows". |
| `run/scratch-guard.py` | The scratch guard, which runs a gate or suite and refuses a file it left behind outside its own `TMPDIR`; see "The scratch rows". |
| `run/change-gates.py` | The contract, migration and fact rows over a change, read from a caller's `.github/changes.toml`; see "The change rows". |
| `run/slop-rules.py` | The shadow slop rows (source-text assertions, symbol truth and the deterministic UI rules of STANDARDS) over a change; see "The slop rows". |
| `run/fakes.py` | The fake rows: test doubles a caller's `.github/fakes.toml` does not declare, declared fakes with no real counterpart, fakes past their freshness or drifted, and the GitHub record-and-compare and daily real smoke; see "The fake rows". |
| `run/mutation.py` | The mutation rows: a mutant of a change's lines that no test kills, with a full run when the change deletes or weakens a test; see "The mutation rows". |
| `run/review.py` | The review rows: a finder session on a model other than the author's, then a verifier pass that must reproduce each blocking finding before it blocks, each seat within its token and time budget, with a Haiku router checked by a rule floor and a veto rule that settles disagreement; see "The review rows". |
| `run/corpus.py`, `corpus/` | The seeded corpus for the design, licence and release reviews and the runner that counts what a review row catches and what it flags on it; see "The seeded corpus". |
| `run/lens-eval.py`, `corpus/lens/` | The role lenses: `kind = "lens"` rows a routed review assigns to its finders, the thirteen seed rows, and the ablation eval that measures each lens with and without itself; see "The role lenses". |
| `run/critic.py`, `run/critic-eval.py`, `corpus/critic/` | The critic lens: one seat on another model writes the strongest counter-case to a decision request or a study and re-derives two numbers it rests on, and the gate holds it when a number does not survive; the eval measures each seat on planted wrong numbers, planted one-sided requests and their clean twins; see "The critic lens". |
| `run/weakening.py`, `policy/relaxations.toml` | The weakening guard: a change that relaxes its tests, workflows or branch rulesets against the base, unless a recorded policy covers it; see "The weakening guard". |
| `run/licence.py` | The licence row: dependency and vendored-file licences against a caller's `.github/licence-policy.toml`; see "The licence row". |
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
The advisories judge counts an alert as fixed by a pull request when the dependency compare shows the package removed with that GHSA, at the alert's own manifest and adding no version still carrying it.
For an alert filed against a `package.json`, whose npm changes the compare lists under the lockfile only, a `package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock` or `pnpm-lock.yaml` in the same directory stands in; a lockfile in another directory, or any other manifest type, gets no such credit (measured 2026-10-25 on pointback PR 139).
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

## The verdict cache

`run/gate.py run --store DIR` reuses a `pass` recorded under a check's cache key instead of running the check, so a record is trusted only when the runner itself wrote it.
Each row and each of its tool probes runs in a sandbox that cannot write `DIR`: macOS's `sandbox-exec` with a write denial on the store's real path, or bubblewrap (`bwrap`) elsewhere with the store bound read-only and the rest of the host as it was.
The runner writes a row's record after the row has exited, under the key it computed, and marks it `confined`.
It reads the store once, before any row or probe runs, and reuses a `pass` only from a record marked `confined: true`.
A store holding any record marked `confined: false` reuses nothing, because that row could have written any other line in it.
A row that leaves another directory at the store's path, by moving one of its ancestors, makes the run undecided (exit 2) with nothing recorded, since the store's device and inode are compared after every row.

A host whose sandbox will not start (inside another `sandbox-exec`, on Ubuntu 24.04 with its default AppArmor limit on user namespaces, or with neither tool) runs every row unconfined, prints `gate: no sandbox keeps the rows from the store`, reuses nothing and marks its records `confined: false`.
That costs the cache, never a verdict: every selected row runs, and the fold is unchanged.
`ci.yml` installs bubblewrap and lifts the AppArmor limit for the `shell` job, which runs `.ci/test-gate-runner.py`.

A store written before this rule holds no `confined` mark, so its records still fold but are never reused; the first run after the upgrade runs every row once.
The sandbox keeps a row from the store and nothing else: a process outside the runner, under the same account, can still write it, so a store shared with unconfined tools carries no provenance.
`run_one` and `probe` take the prior records and the sandbox's argv prefix as arguments, and default to no reuse and no sandbox, so a caller that drives them inside a box of its own never reuses a record that box's rows could have written.
`.ci/test-gate-runner.py` holds each control to a case that goes red without it.
The macOS profile also denies a rename of each of the store's ancestors, a rule bubblewrap has no counterpart for because its bind follows the directory, so that control's red proof runs on macOS only and prints a skip with that reason elsewhere; the case itself, that no forged record is reused, runs on both.

## The permissions-diff gate

`run/permissions-diff.py --base B` reads `git diff B...HEAD` and refuses every change to the files that grant an agent authority, naming each path, in 0.12 s on this repository (measured 2026-10-19).
Its refusal is the signal that sizes the change L and opens a class-2 decision; an automatic change may not land it.
It refuses a narrowing too: telling a narrowing from a widening is left to that decision.

The surface is security design 7.2's list, mapped onto the paths that exist today (read 2026-10-19 across the fleet's repositories):

| 7.2 entry | Paths | What counts as a change |
| --- | --- | --- |
| `settings*.json` and worker profiles | `**/.claude/settings*.json`, `**/managed-settings.json`, `**/managed-settings.d/**`, `**/claude-code/config/settings*.json`, `internal/sandbox/profile.go` | any |
| (added) what else the worker sandbox grants | `internal/sandbox/*.go` | any, `*_test.go` aside |
| (added) the boxed run's network allowlist | `internal/adapter/claude/claude.go`, `repos/depbase.go`; `egress/*.go` | the `Egress` or `Registries` declaration differs, or a changed line names it; the proxy, any, `*_test.go` aside |
| (added) Claude Code hook scripts | `**/.claude/hooks/**`, `**/claude-code/config/hooks/**` | any |
| (added) an agent's tools | `**/.mcp.json`; `commands/**.md`, `agents/**.md` and `skills/**/SKILL.md` under `**/.claude/` and `**/claude-code/config/` | `.mcp.json`, any; the others, a front matter key other than `name`, `description`, `argument-hint`, `when_to_use`, `color` or `model`, or front matter left unclosed |
| the `fac permit` rule table | `internal/permit/permit.go` | any |
| `route.toml` `[tiers]` and `[vendors]` | `route.toml` | `[tiers]`, `[repos]` or `[vendors]` differs, or the file cannot be parsed |
| App manifests and the minter's permission subsets | `**/github-apps/roles.json`, `**/github-apps/app-token.sh`, `**/github-apps/register-app.py` | any |
| workflow `permissions:`, rulesets, Actions settings | `.github/workflows/*.yml` and `*.yaml`; `.github/ruleset*.json`, `.github/rulesets/**` | a workflow's `permissions:` blocks differ, comments aside, or a changed line spells `permissions` another way, opens a flow mapping or spells an explicit or escaped key, or the file uses a YAML anchor or alias and any line changed; a ruleset, any |
| gate registry rows with `phase = "security"` | `.github/gates.toml`, every row; `.ci/gate-registry.toml`, rows with `surface` `"Security"`, `"Code review"` or `"Gate pinning"`, and the rows `SECURITY_ROWS` names (the tool, PR-text, licence, workflow-policy, dependency-advisories, signature, exposure and commit-msg rows) | an existing row deleted or any of its fields changed; a new row passes |
| (added) the tool rows' ignore lists, pins and in-tree configuration, and the relaxation records | `policy/**`; `run/tool-gates.py`; `**/zizmor.yml`, `**/zizmor.yaml`, `**/.github/actionlint.yml`, `**/.github/actionlint.yaml`, `**/osv-scanner.toml` | any |
| (added) the PR-text, licence and weakening rows | `run/pr-text.py`, `run/licence.py`, `run/weakening.py`, `.github/licence-policy.toml` | any |
| (added) the review, secret-scan, workflow-policy and advisories rows, and the runner whose globs this gate matches with | `run/review.py`, `run/secret-scan.sh`, `.gitleaks.toml`, `.ci/test-workflow-policy.sh`, `run/dependency-advisories.sh`, `run/dependency-advisories/**`, `run/gate.py` | any |
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
- A workflow is read by lines, not parsed, because the gates are standard-library Python, which has no YAML parser; it therefore fails closed, and an edit to a workflow that uses an anchor or a flow mapping is refused even when no token changes; the bodies of `|` and `>` block scalars are skipped when looking for an anchor or alias, so a shell `*)` in a `run:` block is not one.
- 7.1 says the PR text scan shares the outbound pattern set with `fac`; it is a separate set in `run/pr-text.py`, so that file is protected beside `outbound/outbound.go`.
- Not protected, and candidates for the list: `.github/CODEOWNERS`, `.github/secret_scanning.yml`, `.githooks/**`, and a new workflow with no `permissions:` key, which takes the repository's default token.

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
`.ci/test-permissions-diff.py` runs 7.2's five red fixtures, a case for each form a workflow reader refuses and for each added path, and runs this row through `run/gate.py`, which folds it to `shadow` with a `shadow_since` and refuses without one.

## The weakening guard

`run/weakening.py --base B --repo OWNER/NAME [--tests GLOB]...` compares a change's tests, workflows and branch rulesets with its merge base and refuses one that relaxes them, unless the relaxation matches a policy recorded in `policy/relaxations.toml`.
It reads that file from `$GATES_DIR`, the gates checkout at the locked commit, never from the tree under review, so a change cannot grant itself a policy and a locked caller moves its policies with its lock bump.
`--repo` defaults to `GITHUB_REPOSITORY`; with neither, or with no `GATES_DIR`, it exits 2.

| Kind | Finding | What a record matches |
| --- | --- | --- |
| `test-exemption` | a line added to a file `--tests` names that skips, ignores or marks a test todo, or a `continue`; a line moved within the file is not new | `ref`: the line holds exactly one regex or string literal, whose source with `\/` unescaped equals `ref`, and nothing that combines or negates it |
| `context-removed` | a required status context gone from `.github/ruleset*.json` or `.github/rulesets/*.json`, or a ruleset that is not JSON | `context_to`: a pattern with `{from}` in it that one of the contexts the change added must fullmatch |
| `workflow-condition` | `paths-ignore`, `continue-on-error` other than `false`, or a job `if:` other than `always()` added to a job a required context names, or one such a job needs | none yet |
| `moving-ref` | a `uses:` added at a ref that is neither a commit nor a `sha256:` digest; a ref already present at the base is not new | `ref`: the whole `uses:` value fullmatches it |

A moving ref that replaces a commit pin of the same workflow or action is refused whatever the records say.
The verdict prints each match, `weakening: moving-ref at .github/workflows/secret-scan.yml:6 matches recorded policy first-party-shared-at-main`, and that line is the record of the claim, so a change declares nothing.
An unmatched finding refuses and names itself; answering it is a gates pull request that adds a record, which covers every later change of the same shape.

A record names the kinds it covers, the repositories it applies to and one exact matcher per kind:

```toml
[[relaxation]]
id = "first-party-shared-at-main"
ratified = 2026-10-25
by = "maintainer decision"
kinds = ["moving-ref", "test-exemption"]
repos = ["Abhijeet34/*"]
ref = '^Abhijeet34/gates/\.github/workflows/shared-[a-z-]+\.yml@main$'
```

The file is checked before anything is judged: an unknown table or key, a duplicate `id`, a `ratified` that is not a date, a kind outside the table above, a `repos` entry that is not `OWNER/NAME`, a matcher missing for a kind or present for none, or a pattern that does not compile exits 2.
`policy/**` and `run/weakening.py` are on the permissions-diff surface, so adding a record is a class-2 decision.

A caller runs it as a shadow row in its `.github/gates.toml`, naming its security tests:

```toml
[[gate]]
id = "weakening"
inputs = ["**"]
tools = { base = 'git merge-base "$BASE_SHA" HEAD' }
run = '''python3 "$GATES_DIR/run/weakening.py" --base "$BASE_SHA" --tests 'test/architecture/**' '''
shadow_since = 2026-10-26
```

`.ci/test-weakening.py` runs an inline job moved into a shared workflow at `@main`, with the test that requires a commit pin taught to skip exactly that ref, with and without the record, then a widened literal, a third-party ref, a repository outside the record's scope, a commit pin turned back into `@main`, a caller's own `policy/relaxations.toml`, each finding kind and each schema refusal.
`.ci/gate-registry.toml` proves the rows `weakening-test-exemption`, `weakening-context-removed`, `weakening-workflow-condition` and `weakening-moving-ref`.

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
`.ci/check.sh` runs `owner`, `commands` and `emdash` on this tree and not yet `budget`, though this tree's own `AGENTS.md` now fits it.

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
A refusal that is not a finding exits 2 and prints `tool-gates: refused (TYPE)`, with TYPE one of `missing-tool`, `wrong-version`, `missing-database`, `stale-database`, `undecided` or `tool-error`, so a runner without the tool, or with an old database, cannot fold its row to a pass.

| Tool | Pin | Where the pin lives | Checked by |
| --- | --- | --- | --- |
| zizmor | 1.29.0 | `PINS` in `run/tool-gates.py` | `zizmor --version` before every run |
| actionlint | 1.7.12 (the latest release, 2026-03-30) | the same | `actionlint --version` |
| OSV-Scanner | 2.7.0 (12 days old on 2026-10-24) | the same | `osv-scanner --version` |

A runner row also lists the version probe under `tools`, so the runner's cache key moves with the pin.

**Install.**
Nothing here installs them; the `shell` job installs them for the `--real` cases (see "In CI" below).

```bash
uv tool install zizmor==1.29.0
curl -fsSLO https://github.com/google/osv-scanner/releases/download/v2.7.0/osv-scanner_linux_amd64
```

- `osv-scanner_linux_amd64` has sha256 `160c6253e2922e9e0a6a9771c5c4c40a4b3c6090b34a3cdd5e80c96cc2e4218b` and `osv-scanner_darwin_arm64` has `d4926ef8da134332543be276dff157e4c170f99ad91fc48e89448a8816bdf70b`, from the release's `osv-scanner_SHA256SUMS` (read 2026-10-24). Verify before `chmod +x`.
- From 2.7.0 a lockfile package entry with no `resolved` URL is treated as local and skipped, so it scans clean. The row refuses such an entry as `undecided` (see "What the rows do not read"), and a hand-written npm fixture that wants a finding needs `resolved`.
- actionlint 1.7.12 is the release tarball `actionlint_1.7.12_linux_amd64.tar.gz` (sha256 `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`) or `brew install actionlint` on the Mac.

**What the rows do not read.**
A change must not be able to silence the finding that gates it, so no row reads configuration from the tree under review:
zizmor runs with `--no-config --no-ignores`, which drops `zizmor.yml`, `.github/zizmor.yml` and inline `# zizmor: ignore` comments;
osv-scanner and actionlint run with `--config` and `-config-file` naming `policy/osv-scanner.toml` and `policy/actionlint.yaml` of this repository, which stops `osv-scanner.toml` (at the root or beside a lockfile) and `.github/actionlint.yaml` from being read;
osv-scanner also runs with `--no-ignore`, so a `.gitignore` that names a lockfile or its folder does not hide it from the scan.
Both files hold a comment and no ignore.
An ignore, when one is justified, is an entry in one of them with a reason, and `policy/**` is on the permissions-diff surface, so adding one is a class-2 decision.
The one relaxation zizmor honours is a `moving-ref` record of `policy/relaxations.toml` (see "The weakening guard"): the row reads zizmor's `json-v1` output and drops an `unpinned-uses` finding only when a record covers that `uses:` value for `GITHUB_REPOSITORY`, read from `$GATES_DIR` as `run/weakening.py` reads it.
The verdict prints `tool-gates: unpinned-uses at .github/workflows/ci.yml:5 matches recorded policy first-party-shared-at-main` for each one dropped, and every other finding keeps the row red.
An `unpinned-uses` finding with `GATES_DIR` or `GITHUB_REPOSITORY` unset, or a policy file that fails its schema, is refused `undecided`; output that is not a JSON list of findings drops nothing.
zizmor judges the whole tree with no base, so it cannot tell a commit pin turned back into `@main` from a new reference; the `weakening-moving-ref` row refuses that one, so a caller runs both.
actionlint reads a project's config only inside a git repository, so a fixture tree needs a `.git` to show the bypass; `.ci/test-tool-gates.py --real` plants one, which osv-scanner's `.gitignore` cases need too.

A `package-lock.json` or `npm-shrinkwrap.json` entry that has a `version`, no `resolved` and no `link`, whether under a `node_modules/` key or in a v1 `dependencies` map at any depth, is refused `undecided`, naming the file and the entry, before osv-scanner runs.
A lockfile that cannot be read as JSON is refused the same way.
osv-scanner's exit 128 ("no package source") passes only on a tree with no file osv-scanner 2.7.0 parses by default (every name in `PACKAGE_SOURCES` in `run/tool-gates.py`, from its lockfile, sbom and directory presets); on a tree that holds one it is refused `undecided`.
Whether npm ever writes a registry package without `resolved` was not measured; if it does, that entry is refused until it is judged some other way.

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
Nine registry rows (`workflow-zizmor`, `workflow-zizmor-moving-ref`, `workflow-actionlint`, `advisories-osv`, `advisories-osv-undecided`, `advisories-osv-undecided-v1`, `advisories-osv-unscanned`, `advisories-osv-stale`, `tool-missing`) prove the script by execution with stub tools, because the `gates-proof` job installs none of the real ones.

**In CI.**
The `shell` job installs the three pinned tools, with the checksums above, and downloads an npm-only database, then `.ci/check.sh` runs `.ci/test-tool-gates.py --real` when `CI` is set; a bare checkout runs the stub cases only, because a skip on a missing tool would pass the same way.
The download step and the `--real` run in CI were not run for this change, since the sandbox it was built in cannot reach the database host.
By hand, with the pinned binaries on `PATH`:

```bash
OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY=$HOME/.cache/osv-db python3 .ci/test-tool-gates.py --real
```

No workflow calls `run/tool-gates.py` itself.

## The UI rows

`run/ui-gates.py axe|pixel|record|stamp` judges a page (an http(s) URL or an HTML file) in a pinned headless Chromium; its docstring owns the exit statuses and the refusal types.
It is plan row P3a-ROWS's last two rows, and its rules are the factory's `fac browse` gate rules (`docs/browse.md` there, "The gates"), on the same pins:

- **axe**: axe-core's `wcag2a` and `wcag2aa` rules; a `serious` or `critical` violation is a finding, a `moderate` or `minor` one is printed and passes. axe is evaluated over the debugger protocol, so a page's `script-src 'none'` cannot keep it out.
- **pixel**: a screenshot at 1280x800 against a baseline PNG; a pixel differs when any 8-bit channel moved by more than 25, two sizes are a finding, and a refusal keeps the screenshot and a diff (the shot faded, each differing pixel red) under `TMPDIR`. It judges only on Linux, where baselines are recorded: elsewhere it is refused `not-linux`, so a Mac's font rasterisation never turns it red. `record` writes a baseline, on Linux too.

**Why not `fac browse` itself.**
Its verbs answer only inside `fac workspace lease <root> --browse`, which needs a factory home and the clean workspace root of an admitted task, and `fac` is built from a private repository, which a caller's runner cannot check out (see "Pinning").
So the row runs the same install through Playwright directly (`run/ui-gates.mjs`) and re-states the two rules in `run/ui-gates.py`, PNG decoding included, with no new dependency: Playwright and axe-core are the engine's own pins, and the comparison is the standard library's `zlib`.

| Component | Pin | Checked by |
| --- | --- | --- |
| Playwright | 1.63.0 | the driver's report, before any verdict |
| axe-core | 4.13.0 | the same |
| Chromium headless shell | 153.0.8010.12 (Playwright build v1243) | `browser.version()`, the same |

**Install.**
`UI_GATES_MODULES` names a `node_modules` that holds the two packages, and Playwright finds the shell through its own `PLAYWRIGHT_BROWSERS_PATH`.
On a machine with `fac browse install` done, its directory serves: the `node_modules` under `~/.cache/fac/browse/<key>/` and the shell under `~/.cache/fac/browse/browsers`.
Elsewhere:

```bash
npm install --prefix "$HOME/.cache/ui-gates" --no-save playwright@1.63.0 axe-core@4.13.0
```

then `npx playwright install --with-deps chromium-headless-shell` from that directory, and `UI_GATES_MODULES=$HOME/.cache/ui-gates/node_modules`.
Chromium aborts inside this machine's agent sandbox (Seatbelt), as the factory measured for `fac browse`, so a session runs the real cases with its sandbox off; inside it the row is refused `tool-error`, never passed.

**Measured on this machine** (arm64 macOS, 2026-10-24, the pinned install, `.ci/test-ui-gates.py --real`): axe on one page 0.5 to 0.9 s; pixel 0.8 s when the page matches and 1.8 s when it does not, of which about 1 s is the comparison of 1,024,000 pixels in Python.
The pixel cases ran with `uname` stubbed to Linux, since the gate refuses on a Mac; a run on Linux, where its baselines belong, has not been made, and whether the headless shell starts with its own sandbox on a GitHub-hosted Ubuntu runner is unmeasured.

**The rows**, in a caller's `.github/gates.toml`, in shadow until the promotion rule in `run/gate.py` lets them block.
The paths are the caller's; `stamp` prints the pins and the installed versions, so the runner's cache key moves with them.

```toml
[[gate]]
id = "ui-axe"
inputs = ["site/**"]
tools = { ui = 'python3 "$GATES_DIR/run/ui-gates.py" stamp' }
run = 'python3 "$GATES_DIR/run/ui-gates.py" axe site/index.html'
shadow_since = 2026-10-24

[[gate]]
id = "ui-pixel"
inputs = ["site/**", ".github/baselines/**"]
tools = { ui = 'python3 "$GATES_DIR/run/ui-gates.py" stamp' }
run = 'python3 "$GATES_DIR/run/ui-gates.py" pixel site/index.html .github/baselines/index.png'
shadow_since = 2026-10-24
```

`.ci/test-ui-gates.py` runs both rows through `run/gate.py` against a stub browser and folds each to `shadow`, and with no window to a block.
Three registry rows (`ui-axe`, `ui-pixel`, `ui-pixel-not-linux`) prove the script by execution with the stubs `.ci/fixtures/ui/build.sh` writes, because the `gates-proof` job installs no browser.

**Not in CI.**
`ci.yml` installs no browser, so `.ci/check.sh` runs the stub cases only, and `.ci/test-ui-gates.py --real` is run by hand.
Installing the pinned browser in the `shell` job and running `--real` there is a workflow edit, so it waits for the maintainer's weekly batch; no workflow calls `run/ui-gates.py` either.

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

Five registry rows (`pr-text-slop`, `pr-text-em-dash`, `pr-text-attribution`, `pr-text-home-path`, `pr-text-security-marker`) prove the script by execution, one per rule class, under the surface "Pull request text"; `run/permissions-diff.py` protects them by id, in `SECURITY_ROWS`.
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
With a `--base`, `contracts` and `migrations` read the declarations as the base commit has them, so a change is judged by the declarations it was written against.
A base with no `.github/changes.toml` is `undeclared`, which means the change that first adds the file is refused once; a base of forty zeros has no commit to read and uses the working tree's.
`facts` reads the working tree's declaration, because the declaration is where a change records its decision, and compares it with the base's.
A snapshot, a migration directory or a migration that is, or sits behind, a symlink leaving the tree is `refused (outside-tree)`, and a declared command runs with `PATH`, the locale, `TMPDIR` and an empty `HOME`, not the runner's credentials.

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
Seven registry rows (`contract-snapshot-sources`, `contract-snapshot-command`, `migration-immutable`, `migration-dry-run`, `fact-change-sweep`, `migration-declared-at-base`, `contract-snapshot-contained`) prove the script by execution.
No workflow calls it: `BASE_SHA` and a `.github/changes.toml` come from a caller, and a step is the maintainer's weekly batch.

## The slop rows

`run/slop-rules.py ROW [--base B] [root]` holds one change to the slop a tree alone can answer.
With `--base` a row judges what `git diff B...HEAD` added, so a stale line already on the base is not the change's; without it, every file under `root`.
The changed paths come from `git diff --raw -z`, and the added lines from the patch, paired by order, because a patch header quotes a name with a quote or a backslash and pads one with a space; `run/fakes.py` and `run/mutation.py` read a change through the same function.
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
Ten registry rows prove the script by execution (`slop-tests-source-text`, `slop-symbols-path`, `slop-symbols-function`, `slop-symbols-flag`, `ui-easing`, `ui-contrast`, `ui-z-index`, `ui-z-index-named-path`, `ui-em-dash`, `ui-reduced-motion`).
No workflow calls it: `BASE_SHA` comes from a caller, and a step is the maintainer's weekly batch.

## The fake rows

`run/fakes.py undeclared|counterpart|stale|compare [--base B] [--today D] [root]` holds a repository's test doubles to what it declares in `.github/fakes.toml`.
Its docstring owns the exit statuses (0 nothing to name, 1 a finding, 2 could not decide, with a typed `refused (TYPE)` line), and each finding is one line, `fakes: ROW: ...`.
It is the first slice of the factory plan's real-tests row (r5, "P3a-RT"): the declaration, the fake lint and the stale-fake refusal.
Mutation proof on changed code is the second slice (see "The mutation rows"), and GitHub record-and-compare with a daily real smoke the third ("Record and compare", below).

**What this replaces.**
The sandbox report's task W8 (s1, section 9) was one real-tests policy task: a `fakes.toml` registry, a weekly contract or record-and-compare check per fake, a census gate that flags a new unregistered mock, and a seeded-defect proof per test, accepted when the census gate fails on a new `jest.mock` and a drifted fake fails its weekly check.
The plan folded W8 into P3a-RT, and these rows are W8's registry and census gate.
The census is widened from mock libraries to hand fakes and test-written programs, as the learning-loop report's fake lint asks (l1, 10.3), and the drift check is a declared freshness date, which `compare --write` moves.
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
drift = "2026-10-19: ..."                         # optional: written by compare --write

[[fake.github]]                                   # optional: one per recorded reply
request = "/repos/o/r/pulls?state=closed"         # a GET path on api.github.com
recording = "tests/fixtures/gh/pulls.json"        # the reply the tests replay
status = 200                                      # optional, default 200
```

| Key | Why it is there |
| --- | --- |
| `id` | Names the fake in every finding and keeps two declarations apart, as in `.github/changes.toml`. |
| `paths` | "the fake's path" (l1, 10.2 rule 3) and "outside a path and symbol registered" (10.3): globs of the files that hold or install the double, so a declared name is allowed only there. |
| `names` | The "symbol" of 10.3: the names the lint gives the doubles this fake covers. |
| `stands_for` | What real thing the fake stands in for, l1's "boundary" and "reason"; prose for the reviewer, checked only to be present. |
| `real`, `real_name` | The file of this tree that calls the real thing, and the word it calls it by. An external API cannot be probed offline, so the tree's own call is what `counterpart` checks, and a declaration cannot outlive the seam it fakes. |
| `compare` | "the record-and-compare command" (10.2 rule 3, 10.4), named in `stale`'s finding. For a GitHub fake it is `run/fakes.py compare --id ID`; for any other boundary, the caller's own command. |
| `recorded` | "a recording date": the day the last compare passed, which `stale` reads. |
| `max_age_days` | "its schedule"; the default is 7 because every boundary in l1's 10.4 table is compared weekly. |
| `drift` | The "or red" of l1's fake freshness (10.3): the last compare's first difference, which `stale` refuses until a matching compare clears it. |
| `[[fake.github]]` | Each recorded GitHub reply the fake's tests replay, and the request that produced it, so `compare` can ask again. |

Left out: a closed list of boundary classes, because rule 2's "paid or rate-limited API or the phone channel" is a judgement a reviewer makes from `stands_for`.
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
tools = { base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/fakes.py" counterpart --base "$BASE_SHA"'
shadow_since = 2026-10-19

[[gate]]
id = "fakes-stale"
inputs = [".github/fakes.toml", "tests/**", "bin/sync.py"]
scoped = true
tools = { today = 'date -u +%F', base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/fakes.py" stale --base "$BASE_SHA"'
shadow_since = 2026-10-19
```

`counterpart` and `stale` name the declaration, every fake's `paths` and every `real` file as their inputs, so a change to a test that uses a fake, or to the code it fakes, selects them: that is "a PR whose selected tests use a registered fake" (l1, 10.3).
`stale` keys on today's date through its `tools` probe, so yesterday's pass is not reused once a fake goes past its freshness.
With `--base`, `counterpart` and `stale` judge the fakes as the base declares them, so a change is judged by the declarations it was written against; the cost is that a change which refreshes a recording is judged by the stale declaration until it merges, which is why these rows stay in shadow.
A base with no `.github/fakes.toml` declares no fake, and a base of forty zeros uses the working tree's declaration.
With `--base`, `undeclared` judges only the lines a change added, so a repository's existing doubles do not turn its next change red; the change that adds or edits `.github/fakes.toml` sweeps every test file, so deleting a declaration cannot pass, and that first sweep is where existing doubles become declarations or conversion tasks (l1, 10.3: "its first run is a baseline").
l1 asks for a stale fake's verdict to be `error`; `run/gate.py` records any non-zero exit as `fail`, which refuses in the fold the same way, so the runner is unchanged here.
`.ci/test-fakes.py` runs the three rows through `run/gate.py`, folds each red one to `shadow`, and shows a change to an unrelated file leaves them unselected.
Six registry rows prove these rows by execution (`fakes-undeclared-program`, `fakes-undeclared-mock`, `fakes-undeclared-hand`, `fakes-counterpart`, `fakes-stale`, `fakes-stale-at-base`).
No workflow calls it: `BASE_SHA` and a `.github/fakes.toml` come from a caller, and a step is the maintainer's weekly batch.

**Record and compare.**
`run/fakes.py compare [--id ID] [--api URL] [--out DIR] [--write] [--today D] [root]` asks GitHub each `[[fake.github]]` request again and compares the fresh reply with the recording the caller's tests replay.
It is l1's "record-and-compare jobs and real smokes for ... GitHub" (10.6, R-3), accepted when "a hand-edited fixture field fails the weekly compare", and the plan review's "GitHub record-and-compare and daily real smoke dropped" (r4, 4, R19).
A finding is `fakes: compare: ID: RECORDING (GET REQUEST): ...`, one per difference, in key order.

The shape compared is the JSON type tree, so a value that legitimately moves (an id, a count, a timestamp, a list's length) is not drift and a field added, removed or retyped is:

| Rule | Why |
| --- | --- |
| Types are object, array, string, number, boolean and null; an integer and a float are both number. | JSON has one number type, and GitHub's `2` and `2.0` are one field. |
| null matches any type. | GitHub answers null for an optional value it lacks (`description`, `merged_at`, `license`), so null to string is a value filled in. |
| An array's element shape is the merge of every item's; an empty array matches any array. | A list's length is a value, and an empty list shows no item to compare. |
| A key some recorded items lack may be missing from every real item; a key every recorded item carries that some real items lack is `removed ... from some items`. | The issues list mixes issues and pull requests, and only the second carry `pull_request`. |
| Another status is drift (`status 200 recorded, 404 now`) and its body is not compared. | l1, 10.4 diffs "JSON shape and status codes". |
| A redirect is a status, never followed. | Following it would carry the token to wherever it points. |
| 429, or 403 with `x-ratelimit-remaining: 0`, refuses `rate-limited`; a 5xx, a timeout, a cut connection or a reply that is not JSON refuses `network`. | Neither says anything about the fake, so neither is drift and neither is a pass. |

Left out: a per-field ignore list, because nothing measured needs one, and an object whose keys are values (`/repos/o/r/languages`), which is not a fit for a shape fake.

The request is a GET by construction: `fetch` builds only `method="GET"`, and a declared `request` carrying a method, a `//` host or a `..` segment is refused (`bad-declaration`).
It sends no credential unless it runs in GitHub Actions (`GITHUB_ACTIONS=true`) with `GITHUB_TOKEN` set and `--api` left at `https://api.github.com`, so a caller's CI uses the job token it already has, a local run is unauthenticated (60 requests an hour) and never reads a login, and a token never reaches a fixture server.
No API version header is sent, so the reply is the one the caller's own `gh` or client gets from GitHub's default version, and a change of that default is drift the caller would meet.

`--out DIR` keeps each fresh reply at DIR plus its recording's path, a new replay's first recording included, so recording a fake is `compare --out DIR` then copying the reply in.
The recording itself is never rewritten: its values are what the caller's tests assert on.
`--write` moves a matching fake's `recorded` to today and drops its `drift`, and gives a drifted fake `drift = "DATE: FIRST DIFFERENCE (and N more)"`, which `stale` refuses whatever `recorded` says.
It edits `.github/fakes.toml` by line, keeping comments and layout, and re-reads the result: a declaration it cannot edit that way (an inline table, say) refuses `tool-error` and is left as it was.

**The daily real smoke** is the same command over every declared GitHub fake, which a scheduled job runs and which runs locally:

```bash
python3 "$GATES_DIR/run/fakes.py" compare --out "$RUNNER_TEMP/fakes-smoke"
```

Exit 0 means every reply matched in shape; 1 names each drift, even when another fake could not be fetched; 2 is a refusal, including no GitHub fake declared, since a smoke that compared nothing is no pass.
A scheduled job cannot commit, so its drift goes red on the run and a maintainer runs `compare --write` locally to record it in the declaration; a fake nobody re-compares goes stale at its `max_age_days` anyway.
Measured 2026-10-19 against `api.github.com`, unauthenticated, on a throwaway caller declaring three replays of this repository (the repository, five closed pull requests, five issues of any state): `--out` recorded all three, a second compare matched them, and hand-editing the repository recording (`owner.id` retyped, `default_branch` dropped, `made_up` added) gave exactly three findings and a `drift` that `stale` then refused.
Behind an HTTP proxy that cut the larger replies short, the same run refused `network` and wrote nothing, which is the case the refusal exists for.

**The rows.**
`fakes-stale` above already refuses a drifted fake; a caller adds one row that compares on a change to a recording or the declaration, scoped so no other change selects it, in shadow like the rest:

```toml
[[gate]]
id = "fakes-compare"
inputs = [".github/fakes.toml", "tests/fixtures/gh/**"]
scoped = true
run = 'python3 "$GATES_DIR/run/fakes.py" compare'
shadow_since = 2026-10-19
```

Its cache key is its inputs, so a pass is reused until a recording changes, and the daily smoke is what catches GitHub moving underneath an unchanged one.
`.ci/test-fakes.py` proves the comparison against `.ci/fixtures/gh-recorded-server.py`, a loopback server of recorded replies that lives exactly as long as the command it wraps and logs each request's method and `Authorization`, and runs this row through `run/gate.py` with a hand-edited recording folding to `shadow`.
Two registry rows prove it by execution through the same server: `fakes-compare` (a field GitHub stopped sending) and `fakes-stale-drift`.

**The workflow** is `.github/workflows/shared-fakes-smoke.yml`: `on: workflow_call`, `permissions: contents: read`, one `ubuntu-latest` job with `timeout-minutes: 10`, the three bootstrap steps of `shared-guard-generated-files.yml`, then one step with `env: GITHUB_TOKEN: ${{ github.token }}` running the smoke command above.
The `fakes-smoke-workflow` registry row proves the command it runs through the loopback server.
A caller that declares a GitHub fake schedules it in `.github/workflows/fakes-smoke.yml`, pinned like every other call (see "Calling a workflow"), which needs a tagged commit that holds the workflow first:

```yaml
on:
  schedule:
    - cron: "41 5 * * *"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  smoke:
    uses: Abhijeet34/gates/.github/workflows/shared-fakes-smoke.yml@<40-hex commit> # vN
```

## The mutation rows

`run/mutation.py rust|typescript --base B [--dir D] [--in-place] [root]` is the second slice of the factory plan's real-tests row (r5, "P3a-RT"): mutation proof on changed code through cargo-mutants `--in-diff` and Stryker `--incremental`, with a full run whenever a change deletes tests.
Its docstring owns the exit statuses (0 pass, 1 a mutant survived, 2 could not decide, with a typed `refused (TYPE)` line), and each finding is one line, `mutation: survived: FILE:LINE:COL: ...`.
GitHub record-and-compare and the daily real smoke are the third (see "The fake rows").

**What it judges.**
A mutant counts when it touches a line `git diff B...HEAD` adds or rewrites under D: cargo-mutants filters by `--in-diff`, and Stryker by `--mutate FILE:A-B` per changed range.
Stryker 10.0.0's `--incremental` report also carries reused mutants outside `--mutate` (measured 2026-10-19 on the fixture: a range holding one blank line tested 0 mutants and reported 7), so the row keeps only the mutants whose span touches a changed line.
A survivor is cargo-mutants' `MissedMutant`, or Stryker's `Survived` or `NoCoverage`; a timeout counts as killed, as Stryker scores it, and a mutant that does not compile is not counted.
Rust test code is never mutated by cargo-mutants, and the TypeScript row mutates `.ts`, `.tsx`, `.mts` and `.cts` files that are not tests, fixtures, declarations or `*.config.*`.
A run that tested no mutant proves nothing, so when the change adds a line of code (not blank, not a comment and, in Rust, before the file's first `#[cfg(test)]`) and the tool still tested zero mutants, the row is `refused (nothing-mutated)`; a change with no such line, such as a comment or an added assertion, mutates nothing and passes with "nothing to mutate".
The full run applies the same refusal when it tests none.

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

In the full run a survivor is named unless the same mutant also survived at the merge base, which the row checks by running again on an export of the base, matched by file, function or mutator, replacement and the original source text.
A mutant of new code has no base counterpart and is named, as in the diff run.
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
Six registry rows prove the script by execution on a real crate and package with that stub (`mutation-rust`, `mutation-rust-weakened`, `mutation-typescript`, `mutation-typescript-weakened`, `mutation-tool-missing`, `mutation-nothing-mutated`).
The stub is a fake of a local binary, which l1's rule 2 allows for nothing; it stands in only where a tool is not installed: the `gates-proof` job and a bare checkout.
`.ci/test-mutation.py --real` puts the same fixtures to cargo-mutants 27.1.0 and Stryker 10.0.0, installing the fixture package's devDependencies with npm:

```bash
python3 .ci/test-mutation.py --real
```

**In CI.**
`ci.yml`'s `shell` job installs Node 22 and cargo-mutants (the test installs Stryker with npm), and `check_shell` runs `python3 .ci/test-mutation.py --real` when `CI` is set: a SHA-pinned `actions/setup-node` for Node 22, and a step that downloads the cargo-mutants 27.1.0 tarball and refuses it unless it has the sha256 above.
It runs on every pull request and push, over the fixtures only (2 to 13 s per case).
A full run over a real code base is not this job's: it costs 627 s on gh-axi and about 98 h on quoth, so by the maintainer's decision of 2026-10-21 a caller runs it on a weekly schedule, and at pull-request time a caller runs the diff the change touches.
No workflow here calls `run/mutation.py` on a code base, since this repository has no Rust or TypeScript source to mutate.
The stub keeps the cases whose answer the real tools cannot be made to give (a wrong version, a dirty tree, an exit status, the base comparison), and the gate registry's six mutation rows, which run in the `gates-proof` job where neither tool is installed.
The stub's copy of the weak, strong, removed and added assertion cases is retired from `.ci/test-mutation.py`; the registry rows still run those inputs.

## The review rows

`run/review.py --gate ID --base B --author MODEL --store DIR` runs one `kind = "review"` row of a caller's `.github/gates.toml` and appends its verdict to the store `run/gate.py fold` reads, so an absent or `error` review never folds to a pass.
It carries the first two slices of the factory plan's review pipeline row (r5, "P3a-RP"): review rows with the reviewer-independence rule and budgets, the verifier pass, the Haiku router, the veto rule, and dissent and independence fields on every verdict.
The seeded corpus and the licence row are the third slice ("The seeded corpus" and "The licence row", below).
`run/review.py --class C --base B --author MODEL --store DIR` routes a change instead of naming one row, as "The router" below sets out.
Its docstring owns the exit statuses (0 pass, 1 a verified blocking finding, 2 could not decide); each finding prints as one line, `review: F1 error FILE:LINE: CLAIM: verified, blocks`, `does not block` or `downgraded: WHY`.

```toml
[[gate]]
id = "code-review"
kind = "review"
inputs = ["**"]
lens = "code"
reviewers = ["claude-sonnet-5-5", "claude-opus-5-5"]
shadow_since = 2026-10-19
```

`lens`, `reviewers` and `budget` are accepted on a review row only; `run/gate.py` refuses them on any other row as unknown keys.

**Independence.**
The finder is the first of `reviewers` of another family than `--author`, and the verifier the first of another family than the finder, preferring one of another family than the author too.
A family is the `haiku`, `sonnet`, `opus` or `fable` an id names, so a dated or provider-prefixed Opus id is Opus.
The list above gives the plan's pairing: Sonnet reviews Opus and Fable work, Opus reviews Sonnet work, and the verifier sits on "the other model" (r5, "The council").

- Context is enforced, not graded. Each seat is a fresh `claude -p` with `--no-session-persistence`, `--tools ""`, `--strict-mcp-config`, `--setting-sources ""` and the review's own `--system-prompt`, run in an empty directory, so no instruction file, hook or saved session reaches it.
  Measured 2026-10-19 with CLI 2.1.302: a five-word prompt run that way costs 613 input tokens, so nothing else was loaded.
- The finder reads the change and the check records of the head; the change is the paths the row's inputs match plus the paths no row claims, as selection reads them.
  The verifier reads the change, the check records, one finding and the output of its reproducing command (r4 7.3: "given only the diff, the finding and the check results").
  Neither sees a transcript, and the verifier never sees the finder's reply or its other findings.
- Model is a label recorded per seat: `cross-model` when the families differ, `context-only` when they match or the author is `unknown`, never silently as if independent (r5, "Who reviews whom").
  With one vendor, `cross-model` is still one family, whose judges share errors (r4 7.1: judge scores correlate with error similarity at about r 0.84), so no label claims family independence; a second vendor's seat would add one.
- A seat answered by a model other than the one it asked, per the reply's `modelUsage`, is an `error`, because a fallback model would make its label false; a reply whose `modelUsage` names no model is one too.

**Budgets.**
Per seat: 60,000 input and 4,000 output tokens, read from the CLI's reported usage with cache reads and writes counted as input (r4 7.1 and 7.3 price a review at "60k-in, 4k-out").
Wall time: 300 s for the finder (r5: "5 minutes on the Mac runner") and 240 s for the whole verifier pass, reproductions included (r5: "2 to 4 minutes more only on convened changes", at its top).
A row's `budget` table may override any of `input_tokens`, `output_tokens`, `finder_seconds` and `verifier_seconds`, as the design lens's 3 minutes will; a breach or a timeout is an `error`, never a pass.
Measured 2026-10-19 through the real CLI on the fixture's floor-division change: the finder used 1,086 input and 423 output tokens, the verifier 1,136 and 267, and the whole review took 13.7 s.
Output tokens include thinking, so the 4,000 is the figure to watch once a large change reaches a seat.

**The verifier pass.**
Only an `error` finding can block (report 3.1: a review blocks only on an error-class finding), and it blocks only when all three steps hold:

1. its `quote` equals line `line` of `path` at the head, both trimmed;
2. its `reproduce` command, run as `bash -e` at the root of a fresh `git archive` of the head, with an allowlisted environment, an empty `HOME` and its own `TMPDIR`, inside a sandbox, exits non-zero and not 126 or 127;
3. a verifier session answers `"reproduced": true` to the finding and that output.

A finding that fails a step stays in the record with `blocks: false` and a `downgraded` reason naming the step, and is printed; it is never dropped.
A step that cannot decide within the pass's budget makes the review an `error`.
Running the command at the base too was rejected: a defect in a new file fails there as well, so the rule would downgrade every real finding in new code.
The command runs the change's code, so it runs sandboxed: no network, no read of anything under the user's home but its own directories, and writes only to the export, its `HOME` and its `TMPDIR`, so it can neither reach a login nor append a record to the store.
It also reads nothing under `/tmp`, `/var/folders`, `/run`, `/var/run` or the system temporary directory but its own, where other tasks' scratch and local services' sockets live, and on macOS it starts with no bootstrap port, so neither launchd nor any Mach service such as the keychain daemon answers it; a sandbox profile rule cannot refuse launchd's own requests (measured 2026-10-25: `launchctl print` and `kickstart` answered under `(deny mach*)`).
A reproduction therefore cannot resolve a user name (`id -un` prints the uid).
The sandbox is macOS's `sandbox-exec`, or bubblewrap (`bwrap`) elsewhere; a host with neither, or one where it cannot start (inside another `sandbox-exec`, or on Ubuntu 24.04 with its default AppArmor limit on user namespaces), makes the review an `error`, never an unconfined run, since a sandbox that fails to start exits non-zero as a reproduction would.
A tool installed under the user's home is therefore out of a reproduction's reach. `ci.yml` installs bubblewrap and lifts that AppArmor limit on its throwaway runners for the suites that reproduce.

**The harness.**
Models are called only through `claude -p`, and only after `claude auth status` reports `claude.ai` or `oauth_token` on `firstParty`, so an API key login is refused before any seat sits.
Every seat's environment drops credential-named variables except `CLAUDE_CODE_OAUTH_TOKEN`.
Claude Managed Code Review and Ultrareview are never called (r4 5 rejects both as gates: the first "never approves or blocks", the second bills usage credits and posts a note crediting the tool).

**The router.**
With `--class` (`trivial`, `medium` or `large`, as intake sized the change) a router seat chooses the rows, and each row it convenes runs as `--gate` would.
The router is a fresh `claude-haiku-5-5` seat (r4 7.3: "a Haiku 5.5 router"), sitting like every other seat, given only the paths the change touches with their lines added and removed, the class and the registry's review rows; it never sees the diff.
It answers `{"class": C, "reviews": [{"gate": ID, "why": ...}]}`.
It "only routes, never passes" (r4 7.3): it may raise the class and add rows, and nothing it says can lower the class, drop a row, pass or block.

A rule sets the floor the router cannot go under, so a router that skips a required review is caught, not trusted:

- a `trivial` change gets no review (r3 3.2: "trivial work runs no agent at the gate");
- otherwise every review row sits whose inputs, or a path no row claims, the change touches, as the row's own diff is chosen;
- a `large` change also seats every `lens = "security"` row (report 3.1: "a security-tagged or large change gets a separate fresh-context security session");
- a path naming auth, crypto, sandbox, credential, settings, permit or permission, or a workflow file, sizes the change `large` whatever the class says (sec1 6; report 3.2 sizes workflow files L).
  The match is a substring, so `author.py` over-reads, which costs a review and never misses one.

A floor row the router left out sits anyway and prints as `route: ID sits: the router skipped it; convened by the rule: WHY`, and its record's route says `router_skipped`.
When the router is unavailable (the login is refused, it times out at 60 s, breaks the token budget, is answered by another model, or answers with no class, no list or a row the registry does not have), the floor alone routes and the record names why.
A `trivial` class is the caller's word, and its floor seats nothing, so with no usable router answer to confirm it every selected review row is recorded `error`: `not convened: the change is declared trivial and no router answer confirmed it`.
A row the router adds whose inputs the change misses reads the whole change.
Any other review row `run/gate.py` selects that does not sit is recorded as a `pass` naming why, `not convened: a trivial change runs no review` say, unless it already holds a record for the head, so a route never covers an earlier fail.
`gate.py fold` folds every review row with a record for the head, selected or not, so a block on a row only the router convened is never dropped.
Measured 2026-10-19 with CLI 2.1.302 on the floor fixture: the real router named `code-review` with 869 input and 69 output tokens in 3.5 s.

**The veto.**
The decision rule is r4 7.3's: "any verified blocking finding blocks; a pass needs every seated member clean; an unverifiable finding is recorded as dissent, never dropped; disagreement escalates to the stronger model with the dissent attached".

- The router has no vote.
- Only a finder's `error` finding can veto, and only once the verifier pass reproduces it; one verified finding on any convened row refuses the whole change.
  This is a minority veto, never a majority: 14 validators on 366 buggy programs had a true-negative rate below 25%, 19.2% by majority, and a minority veto cut the worst precision-estimate error from 14.8% to 2.8% (r4 7.1, Jain et al.).
- The verifier raises no finding of its own.
  When it answers not reproduced to a finding whose quote held and whose check failed at the head, the seats disagree on evidence that stands, so the finding goes to the strongest of `reviewers` that outranks the verifier (Haiku, then Sonnet, then Opus, then Fable, which report 3.2 places "at Opus level or above"), preferring one that differs from the author.
  That adjudicator is a fresh seat given the verifier's evidence plus `## The dissent` with the verifier's reason, and its answer decides, inside the verifier pass's budget.
  With no stronger reviewer listed, the verifier's answer stands: with the default list, Sonnet's finding on Opus work is verified by Opus, and Opus's on Sonnet work escalates from Sonnet back to Opus.
- No round of debate: in r4 7.1's sources debate lowered accuracy in all 10 group compositions tested.
- A disagreement on a four-eyes item reaches the captain in the plan; gates has no four-eyes classifier, so this slice escalates only to the stronger model.

A veto prints as `review: veto: F1 by the finder on M, upheld by the verifier F1 on M2`.

**Dissent and independence.**
Every `error` finding carries `votes`, one `{seat, model, vote, reason}` per seat that judged it, `blocks` or `clears`.
Every record carries `veto`, each blocking finding with the finder's seat and model and the seats that upheld it, and `dissent`, each vote the outcome overruled with what overruled it: a seat, or the step that refuted the finding, such as `its reproducing check passed at the head (exit 0)`.
Each dissent prints as `review: dissent: F1: the verifier F1 on M voted clears: REASON; overruled: the adjudicator F1 on M2`, so a later reader sees who disagreed and why without opening the logs.
Every record also carries `independence`, the weakest label any judging seat holds against the author or another seat, `none` when no seat sat; the adjudicator's labels are kept against the author, the finder and the verifier.
Both lists are empty, never absent, on a pass no seat reached and on an `error`.

**The record.**
The verdict record carries `review`: the lens, the author, the CLI version, each seat's model, independence, token use, duration and reply log, each finding with its quote, reproducing command, exit status, votes and verdict, the `veto`, `dissent` and `independence` fields, and, when routed, the `route`: the given and final class, the router's seat or why it was unavailable, for each review row whether it sits, by the rule, by the router, or both, and the lenses the rule assigned with why; `lenses` names the ones its finder read ("The role lenses").

**Proof.**
`.ci/test-review.py` runs each case through `.ci/fixtures/review/claude-recorded.py`, installed as `claude` first on `PATH`, then again against a copy of the script with that case's control removed.
No case calls a model.
`replies/reproduced` holds the real finder and verifier replies captured 2026-10-19 on the floor fixture, and the real router reply on it.
`replies/unreproduced` is the real envelope from the guard fixture, where the real finder found nothing, with a false alarm written into `result`, because a model's false alarm cannot be captured on demand; its router reply is real.
`replies/escalated` holds a real Opus finder and a real Opus adjudicator on the floor fixture, around a Sonnet verifier's dissent written into a real envelope; the adjudicator, shown that dissent, answered that the zero guard "fires only when whole == 0 and whole is 2 here".
`replies/router-skips.json` and `replies/adjudicator-clears.json` write a skip and a clearing answer into those real envelopes.
The gates-proof rows are `review-verifier` (reproduced and refused, against unreproduced and downgraded), `review-budget`, `review-harness`, `review-router` (a router skipping the required row on the floor change, caught and refused, against the real route of the guard change), `review-trivial` (a trivial claim no router confirms, refused, against one the router confirms) and `review-veto` (an adjudicator upholding over the verifier's dissent, against one clearing it).
No workflow calls `run/review.py`; wiring it beside the runner waits for the maintainer's weekly batch.

## The seeded corpus

`run/corpus.py --corpus DIR --store DIR [--case NAME]... [--record DIR]` runs one lens's seeded corpus through its review row and counts what the row catches and what it flags.
It is the third slice of the review pipeline row (r5, "P3a-RP"): the "seeded corpus for design, licence and release reviews", the set each review row's recall "is measured on" (report 3.1).
Its docstring owns the exit statuses (0 every seed caught and no clean case flagged, 1 a seed missed or a clean case flagged, 2 undecided); each case prints one line, `corpus: LENS/NAME: caught|missed|clean|flagged|undecided: ...`, and the run ends with `corpus: LENS: caught N of S seeded, F false alarms on C clean, U undecided`.

**The shape.**
`corpus/LENS/corpus.toml` names the lens, how a catch is scored, the author the cases are written as and the reviewers, with an optional `budget` table passed to the row (the design lens takes report 3.2's "design lens at most 3 min").
`base/` is the tree every case changes.
Each `cases/NAME/` holds `change/`, the files the change writes over `base/`, and `case.toml`: a seeded case gives `defect`, one sentence naming the planted defect and its source, and a `[[seed]]` `{path, lines = [first, last]}` for each place it sits at the head; a clean case gives `clean`, the near miss it is, and no seed.
A key the shape does not name, or a case that is both or neither, makes the run undecided.

Each case runs as a caller's row runs: `base/` committed and tagged `base`, the change committed over it, one review row (`id = "LENS-review"`, `inputs = ["**"]`) in `.github/gates.toml`, and `run/review.py --gate` on it, so the finder, the verifier pass, the veto and every budget of "The review rows" apply unchanged.
`GATES_CORPUS_CASE=LENS/NAME` is in review.py's environment, which the real CLI ignores and the recorded one answers by.

**Scoring.**
A finding sits at a seed when its path is the seed's and its line is within the seed's lines.

| `catch` | Lens | A seeded case is caught by | A clean case is flagged by |
| --- | --- | --- | --- |
| `blocks` | licence, release | a finding that blocks, verified, at a seed | a `fail` verdict |
| `flags` | design | an `error` or `warning` finding at a seed | any `error` or `warning` finding |

Licence and release are `blocks` because their findings refuse or hold the change (report 3.5: a licence finding goes to "the PR, with the rule that refused it", a release finding to the approval queue "until approved"); design is `flags` because "design findings auto-fix and never park a run" (report 3.2), so a design warning reaches the fix loop and is the catch.
A `nit` never counts either way: style "never blocks" (r5, the code row), and counting it only on seeds would reward noise.
A review that could not decide is `undecided`, never a catch and never a clean pass.

**The cases**, one planted defect each, 16 seeded and 14 clean:

| Lens | Seeded (source) | Clean near misses |
| --- | --- | --- |
| design | report 3.2's six: a 1px off-scale `.hw-btn` padding, a removed `aria-label` on an icon-only button, a badge fill at 1.96:1 (white on `#b9b9b9`), a `.hw-row` that stops wrapping and needs 352px so it overlaps at 300px, a literal `#fff`, a "Submit" label | the same files changed rightly: padding moved along the scale, a Cancel button beside a kept label, a badge at 10.25:1, a row that still wraps, a hover colour from a new token, a label naming its outcome |
| licence | report 3.2's GPL-3.0-only dependency into an Apache-2.0 project, an AGPL-3.0-or-later one, a vendored GPL-2.0-only file, copied MIT code without its notice; review R3's GPL-3.0-only crate in a `Cargo.lock` and GPL-3.0-or-later package in a `pnpm-lock.yaml`, added after this run | an MIT and an Apache-2.0 dependency, an `MIT OR GPL-3.0-only` one, a vendored MIT file with its header |
| release | report 3.2's signing of an artefact no approved release built (an environment bypass), an entitlement widening (`disable-library-validation`), a publish whose failing dry run no longer stops it (`\|\| true`), and a bill of materials made opt-in | a verify step after signing, a version bump, a tag check before the dry run, a second bill-of-materials format |

The design cases are text: the plan's design lens reads screenshots (r5, "The nine reviews"), which no row takes yet, so these measure the lens on the change alone.

**Measured** 2026-10-19 with CLI 2.1.302 through the subscription login, author `claude-opus-5-5`, finder `claude-sonnet-5-5`, verifier `claude-opus-5-5`, every case the three corpora then held:

| Lens | Seeds caught | Clean cases flagged | Undecided |
| --- | --- | --- | --- |
| design | 5 of 6 | 0 of 6 | 0 |
| licence | 3 of 4 | 0 of 4 | 0 |
| release | 0 of 4 | 0 of 4 | 0 |

The six misses, each from its record:

- design `padding-off-scale`: the finder reported nothing.
- licence `vendored-gpl`: the finder named the GPL-2.0-only file as a `warning` ("confirm compatibility before merging"), which cannot block.
- release `entitlement-widened` and `sbom-optional`: each named at the seed as a `warning`.
- release `sign-unapproved` and `dry-run-ignored`: each named at the seed as an `error`, then downgraded by the verifier, because the reproducing command was a `grep` for the changed text, which "does not demonstrate" the defect; the licence verifier upheld `grep` checks of the same kind on all three licence catches.

The 35 seats used 1,032 to 1,643 input tokens and 9 to 1,260 output tokens each, inside the 60,000 and 4,000 budgets, and every seat finished in under 23 s.
At 4 to 6 seeds a lens these are smoke counts, not calibration: r4 7.1 bounds the miss rate below 3/n only after n clean catches, so the 138-case labelled set (r4 7.4) is what calibrates a seat.
The rows stay in shadow, and the misses are what the measurement is for: a lens rubric for the finder and a rule for when a text check reproduces a finding would each be measured on this corpus before either changes a row.

**Proof.**
`.ci/test-corpus.py` replays the recorded seats of that run, `.ci/fixtures/review/corpus/LENS/NAME/`, through `.ci/fixtures/review/corpus-case.sh` and `claude-recorded.py`, then again against a copy of the runner with each case's control removed; its first case pins the three counts above.
No case calls a model; a miss, a nit and a false alarm the live run did not give are written into the real finder's envelope.
The gates-proof rows are `review-corpus-miss` (the approval bypass the verifier cleared, against the GPL dependency it upheld) and `review-corpus-false-alarm` (a warning written onto a clean design case, against its real empty reply).
`run/corpus.py --record DIR` copies each seat's envelope and the CLI version per case, which is how the recordings were made; a model or CLI change re-runs the corpus live (r4 7.3: "a model or CLI change reruns the labelled set before that seat can block or pass").

## The role lenses

A role lens is a yes or no checklist a review's finder reads the change through, never a persona.
It is the factory plan's row "P3b-RL" (r5, section 9; review r4 9.4 and 9.5): persona lines gave no accuracy gain across 162 personas and 9 models, while yes or no checklists raised judge agreement with humans from 46.4% to 52.2% (r4 9.2), so the lens carries the checklist and the role name is one line for tone.

**The row.**
A lens is a `kind = "lens"` row of the same `.github/gates.toml` the review rows live in, so there is no second config file:

```toml
[[gate]]
id = "operations"
kind = "lens"
inputs = ["**/migrations/**", "deploy/**", "infra/**"]   # touched paths that assign it
classes = ["medium", "large"]                            # optional; this is the default
role = "operations engineer"                             # one line, for tone only
checklist = [
  "Does every deploy, flag or config step have a rollback, with how and when to use it?",
  "Is everything temporary it creates removed by a named cleanup step?",
]
question = "If this goes wrong in the night, how do we see it and how do we undo it?"
evidence = "The quoted rollout or setup line, and the rollback, signal or cleanup step it lacks."
stop = "Report only gaps that affect correctness or the stated requirements; a preference is not a finding."
```

`role`, `question`, `evidence` and `stop` are one line each, every `checklist` item is a question ending in `?`, and `classes` holds `trivial`, `medium` or `large`; `run/review.py` refuses a malformed lens before any seat sits.
`run/gate.py` accepts the six keys on a lens row only, and a lens row is no gate: it claims no path, so a registry gains one without any gate's selection, key or fold changing.

**Assignment.**
`run/review.py --class C` assigns a lens when the change's final class is in its `classes` and a touched path matches its `inputs`, by rule, the same rule that sets the review rows' floor (r4 9.4: "never by an agent's taste").
The router is never shown a lens; it may raise the class, which the floor follows, and never lower it.
Each assigned lens reaches the finder of every row that sits, after the finder's own prompt, as its role line, checklist, signature question, evidence and stop rule; it prints as `review: route: lens ID: PATH is under its inputs (class C)`, and the record's `route.lenses` and `lenses` carry it.
`--gate ID` alone names no class, so it reads no lens.

**The seed rows**, `corpus/lens/gates.toml`, for a caller to copy:

| Lens | Assigned on | Signature question |
| --- | --- | --- |
| `product-analyst`, `-developer`, `-designer`, `-customer`, `-tester`, `-stakeholder`, `-support` | `product/**`, `docs/product/**`, medium or large | the product-lifecycle playbook's seven lenses, each with its own question |
| `architect` | any path, large | What does this choice prevent us from changing later, and can it be undone? |
| `security` | any path, large, as a sensitive path sizes it | Who can reach this that should not, and what can they do once there? |
| `tester` | any path, medium or large | Would this check still pass if the change were reverted? |
| `operations` | migrations, SQL, deploy, infrastructure and workflow paths | If this goes wrong in the night, how do we see it and how do we undo it? |
| `designer` | style, markup and UI component paths | What does the user see when this is empty, slow or broken? |
| `open-alternative` | `product/**`, `docs/product/**`, medium or large | Could the incumbent's owner point at this name, picture or screen as theirs? |

**The eval.**
`run/lens-eval.py --corpus corpus/lens --store DIR [--case NAME]... [--record DIR]` is r4 9.5's ablation: 26 specs with one seeded gap each and their 22 gap-free twins, under `corpus/lens/cases/`.
Its docstring owns the case shape and the exit statuses (0 every lens that ran met its bar, 1 a bar missed, 2 undecided).
Each spec reaches a fresh finder seat as a change adding `spec.md`, through `run/review.py`'s own seat, login check, budgets and finder prompt, in four arms: `checklist` (the lens as review gives it), `bare` (without the role line), `persona` ("You are a seasoned ROLE"), and `none`.

| Gap class (seeds) | Lens |
| --- | --- |
| no rollback (3), no cleanup step (3) | `operations` |
| no auth check on a new endpoint (3) | `security` |
| no acceptance command (3) | `tester` |
| an irreversible migration with no dry run (3) | `architect` |
| no out-of-scope list (3) | `product-stakeholder` |
| a new screen with no empty, loading or error state (2) | `designer` |
| a product name holding the incumbent's mark, exact (1) or differing by case, spacing and a diacritic (1) | `open-alternative` |
| a request for the incumbent's screens, as a replica (1) or as "the same layout" (1), or its icons (1) | `open-alternative` |
| interop claims marked neither `standard`, `vendor-spec` nor `observed` (1) | `open-alternative` |

A seed is caught by an `error` or `warning` finding inside the seed's lines whose claim names one of the case's `terms`, so a finding there about something else does not count; any `error` or `warning` flags a clean spec.
The bar per lens is r4 9.5's: at least 80% of its own seeds caught as `checklist`, at most 10% of clean specs flagged.
The ablation is `keep` when checklist recall beats `none` by more than the 95% half-width of the difference, `1.96 * sqrt(2p(1-p)/n)` with `p` pooled, else `cut`; the role line is `keep` when `checklist` beats `bare`, else `remove`.
The verdicts are printed and not applied: at 2 to 6 seeds a lens they are noise, so all thirteen rows stay in shadow and nothing is cut until a larger set decides (decided in review).
The five other product lenses own no gap class here and print as unmeasured.

**The open-alternative lens** is plan row P5-OA4's lens half, for requests the factory's open-alternative template shapes (incumbent, product name, formats, workflows, jurisdictions, licence).
Its four checks are the incumbent's marks listed, the name free of them ignoring case, spacing, punctuation and diacritics, no copied icon or artwork and no screen asked to match the incumbent's layout however worded, and each interop claim marked `standard`, `vendor-spec` or `observed`.
It adds 6 seeded specs and 2 gap-free twins (a word processor and a spreadsheet), `open-alt-*` under `corpus/lens/cases/`, and so raises every lens's clean count from 20 to 22.
Measured 2026-10-21 with CLI 2.1.304, finder `claude-sonnet-5-5`, same arms: checklist caught 6 of 6 seeds (bare 4, persona 5, none 5) and flagged 3 of 22 clean specs against 10 of 22 for no lens, so the bar misses on flags only, as the other lenses do.
The finder alone catches five of six seeds, so ablation reads `cut` (checklist beats none by 0.17, half-width 0.31); only the unmarked interop claims are a gap the finder misses without the lens.
Each check was removed in turn and its cases re-run live (`.ci/fixtures/review/corpus/lens-without-N/`): without the interop item the seed is missed (0 of 1), without any of the other three the finder still catches its cases, so those three are not shown red when removed and stay in the row for the name and look gaps the finder names unprompted.


**Measured** 2026-10-20 and 2026-10-21 (the open-alternative seats, CLI 2.1.304) with CLI 2.1.302 through the subscription login, finder `claude-sonnet-5-5` on specs written as `claude-opus-5-5`, replayed over 48 cases (22 clean specs, 26 seeds, the `open-alternative` row included), 0 undecided; `.ci/test-lens-eval.py` pins these counts:

| Lens | Seeds caught: checklist, bare, persona, none | Clean flagged: checklist, none | Bar | Ablation | Role line |
| --- | --- | --- | --- | --- | --- |
| `product-stakeholder` | 3, 3, 1, 0 of 3 | 4, 10 of 22 | missed (flags) | keep | remove |
| `architect` | 2, 0, 1, 1 of 3 | 12, 10 of 22 | missed | cut | keep |
| `security` | 3, 2, 1, 0 of 3 | 5, 10 of 22 | missed (flags) | keep | keep |
| `tester` | 3, 3, 0, 0 of 3 | 21, 10 of 22 | missed (flags) | keep | remove |
| `operations` | 5, 5, 5, 1 of 6 | 7, 10 of 22 | missed (flags) | keep | remove |
| `designer` | 2, 2, 1, 0 of 2 | 5, 10 of 22 | missed (flags) | keep | remove |
| `open-alternative` | 6, 4, 5, 5 of 6 | 3, 10 of 22 | missed (flags) | cut | keep |

The original 2026-10-20 run, before the two clean twins added for `open-alternative` (220 seats, 20 clean specs, six lenses):

| Lens | Seeds caught: checklist, bare, persona, none | Clean flagged: checklist, none | Bar | Ablation | Role line |
| --- | --- | --- | --- | --- | --- |
| `product-stakeholder` | 3, 3, 1, 0 of 3 | 2, 8 of 20 | met | keep | remove |
| `architect` | 2, 0, 1, 1 of 3 | 10, 8 of 20 | missed | cut | keep |
| `security` | 3, 2, 1, 0 of 3 | 5, 8 of 20 | missed (flags) | keep | keep |
| `tester` | 3, 3, 0, 0 of 3 | 19, 8 of 20 | missed (flags) | keep | remove |
| `operations` | 5, 5, 5, 1 of 6 | 7, 8 of 20 | missed (flags) | keep | remove |
| `designer` | 2, 2, 1, 0 of 2 | 5, 8 of 20 | missed (flags) | keep | remove |

With the two new clean twins `product-stakeholder` now misses its bar: it flags 4 of 22 clean specs, over 10%, where it flagged 2 of 20 before.
`architect` moves from 10 to 12 flags and `tester` from 19 to 21; the seed counts do not change.
No lens row changed, so the flip comes from the two added clean specs alone.

Over the 20 seeds of the original run the checklist arm caught 18, the bare checklist 15, the persona line alone 9 and no lens 2; over the 26 seeds now, 24, 19, 14 and 7, which is the null persona result and the checklist result of r4 9.2 again on this set.
No lens meets the clean bar now; the finder with no lens flags 10 of 22 clean specs itself, so most flags are the finder's habit of reporting some gap, which r4 9.2 predicts.
The seed spans only the section where the gap sits, so a right finding anchored elsewhere scores a miss: on `drop-column-gap` the bare arm named the missing backup at the design line that states the drop (line 12), outside the rollout lines 14 to 17, which accounts for part of `architect`'s bare 0 of 3; the spans were set before either run and are not widened after reading the answers.
`tester` flags 21 of 22 because its second question, whether a check fails before the change and passes after, cannot be answered on a spec whose tests do not exist yet; it fits a code change, not a spec.

This is the second run.
The first, on the specs as first written, found real defects in the clean twins (a Berlin time given as CET with no date, though summer is CEST, a flag that names one index and still splits traffic 10 to 90, a rollback that hides a menu entry and leaves its route open) and credited any finding inside a seed's lines, such as a CSV-injection warning on the missing auth check.
So the clean twins were corrected once, each security twin dropped its auth assertions from its acceptance lines too, and `terms` was added, fixed before the second run; the first run's counts were 17 of 20 seeds by checklist and 8 of 20 by no lens, with no lens flagging 11 of 20 clean specs.
At these counts each figure is a smoke reading; the follow-up is the larger labelled set, enough seeds a lens for the half-width to separate the arms, before any row is cut or its role line removed.

**Proof.**
`.ci/test-lens-eval.py` replays the second run's recorded seats, `.ci/fixtures/review/corpus/lens/NAME/`, through `claude-recorded.py`, then again against a copy of the eval with each case's control removed; its first case pins the first table above (the 48-case replay).
`.ci/test-review.py` covers assignment (class, path, the final class, nothing on a trivial change, only the assigned lenses reaching the finder, a malformed lens refused before any seat) and `.ci/test-gate-runner.py` the lens row's keys and that it claims no path.
The gates-proof rows are `review-lens` (a checklist item that is no question, refused), `lens-eval-miss` (the architect lens's real catch of the missing dry run emptied), `lens-eval-false-alarm` (its real warning on a clean spec) and `lens-eval-open-alternative-miss` (the open-alternative lens's real catch of a request for the incumbent's layout emptied).
No workflow calls `run/review.py` or `run/lens-eval.py`; wiring waits for the maintainer's weekly batch.

## The critic lens

`run/critic.py --request FILE --registry TOML --author MODEL --store DIR` sits one critic seat on a decision request or a study before it reaches the captain, and holds it when a number it rests on does not survive being worked out again.
It is the factory plan's row "P3a-CRIT" (r5, "The council": "The council writes the strongest counter-case and re-derives two numbers before a recommendation reaches you"; review r4 7.4 and 8.2).
Its docstring owns the exit statuses (0 pass, 1 held, 2 could not decide); the run ends with `critic: pass|fail|error: DETAIL`.

**The lens.**
The critic is the `kind = "lens"` row `critic` of `corpus/critic/gates.toml`, in the shape of "The role lenses", checked by `run/review.py`'s `lens_of` before any seat sits and given to the seat by its `lens_text`, role line, checklist, signature question, evidence and stop rule:

| Checklist question | What the gate checks of the answer |
| --- | --- |
| Is the strongest case for the opposite choice made from the request's own evidence? | `counter_case`, and at least one of its `quotes` standing on its line |
| Does the recommendation answer each line of its evidence that cuts against it? | each `omitted` quote standing on its line |
| Does each number the recommendation rests on follow from the figures it cites? | each of `numbers` re-derived by the gate, at least two |
| Is each share stated with the count it is a share of? | none; the seat reads it |

Its `inputs`, `decisions/**` and `studies/**`, also assign it by rule to the finder of a routed review whose change commits a request or a study, as any lens is assigned.

**The seat.**
The critic sits on the first of `claude-sonnet-5-5` and `claude-opus-5-5` that is not `--author`, so Sonnet critiques Opus work and Opus critiques Sonnet work, as a review's finder is seated, in a fresh `claude -p` with no tools, through `run/review.py`'s login check, 60,000 and 4,000 token budgets and 300 s.
Its independence is recorded as `cross-model` or `context-only`, never as a different family: with one vendor the plan's "a model family different from the author's" is a different Anthropic model until a second vendor's seat joins (r5, "Who reviews whom").
It sees the request with its lines numbered and nothing else; there is no devil's advocate inside the author's session, because same-model debate collapses into agreement (r4 8.2).

**The gate decides, not the seat.**
The seat writes the arithmetic and the gate does it:

1. every quote must stand on the line it names, ignoring spacing and `*` or backtick marks; a counter-case or omitted quote must be the whole line or at least 20 characters of it, and a number's quote any part of its line;
2. a number's `stated` value and each operand's `value` must be numbers written on their quoted lines, and no operand may be the stated value on the stated line, which would agree by construction;
3. the gate evaluates `expression` itself, allowing only `+ - * /`, parentheses, the operands' values and `100`; the seat's own result, if it gives one, is never read;
4. a re-derived number disagrees when it is further from the stated one than the larger of 1% of the stated value and half a unit in its last stated digit, so `about 1.3%` for 4 of 300 agrees within 0.05 and 184.00 USD for 147.20 does not.

One disagreeing number holds the request (`fail`), whatever else the answer holds.
A pass needs two numbers re-derived and agreeing and one counter-case quote standing; fewer, or a seat that timed out, broke its budget, answered from another model or unreadably, is `error`, never a pass.
Omitted evidence prints as `critic: omitted line N: QUOTE` and `critic: one-sided: ...; recorded for the captain, does not hold`, and is recorded with the counter-case: only a number beyond tolerance holds (maintainer decision).
Each number prints as `critic: number line N: stated S, re-derived D from EXPR: agrees within T` or `disagrees beyond T, holds`, or `not re-derived: WHY`.

**The record.**
The verdict is appended to `DIR/records.jsonl` in `run/gate.py`'s shape with `gate = "critic"` and `head` the request's sha256, and `critic` holds the lens, the author, the CLI version, the seat with its model, independence, tokens, duration and reply log, the counter-case, every quote and number with why it stands or was dropped, and the tolerance.

**The eval.**
`run/critic-eval.py --corpus corpus/critic --store DIR [--case NAME]... [--record DIR]` runs `run/critic.py` on 24 requests, each once by each seat: the Sonnet critic on the request written as Opus and the Opus critic on it written as Sonnet.
Its docstring owns the case shape and the exit statuses (0 every seat met its bar, 1 a bar missed, 2 undecided).

| Plant (cases) | Requests | Caught when |
| --- | --- | --- |
| a wrong number (6) | a Mac runner's monthly cost, a cache hit rate, a review seat's monthly saving, a flaky-test share, an alert's precision, a pipeline's speedup; three are studies | the request is held on a number stated inside the seed |
| one-sided (6) | retiring a deploy script two services still use, raising a timeout most of whose hits are one job, adopting an unmaintained parser, a speedup that left out the failed runs, dropping a security seat that caught the month's only leaks, merging repositories whose trial nearly tripled CI time | an omitted quote that stands sits inside the seed |
| clean twin (12) | each of the above with its number right or its contrary evidence answered in the recommendation | never held and nothing omitted |

Every request has at least two numbers that follow from its evidence, so a right request can pass.
A hold on a one-sided case or a clean twin, whose numbers are all right, is a false hold, and an omitted quote on a clean twin a false one-sided flag.
The bar per seat is r4 9.5's applied to both plants: at least 80% of the wrong numbers held and of the one-sided cases caught, at most 10% false holds and 10% false one-sided flags.

**Measured** 2026-10-20 with CLI 2.1.302 through the subscription login, 48 seats, 0 undecided:

| Seat (on requests written as) | Wrong numbers held | One-sided caught | False holds | False one-sided flags | Bar |
| --- | --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` (Opus) | 6 of 6 | 6 of 6 | 0 of 18 | 4 of 12 | missed (flags) |
| `claude-opus-5-5` (Sonnet) | 6 of 6 | 6 of 6 | 0 of 18 | 1 of 12 | met |

Of the five false flags, three are Sonnet quoting a contrary line that the clean twin's recommendation answers in one sentence (the security seat, the sharding study, the repository merge), and two quote an evidence line no recommendation needs to answer: Sonnet the finder model's 8 of 14 seeds caught, and Opus the cache study's zero stale restores.
Since a one-sided flag never holds, its cost is the captain reading a counter-case the request had already answered; a false hold, which would stop a right request, did not happen on either seat.
The 48 seats used 1,523 to 1,679 input and 585 to 2,065 output tokens each, inside the 60,000 and 4,000 budgets, and none took over 20 s.

This is the second run.
The first, on the cases as first written, found three defects in them: the Mac's payback was stated as "within five months", which no seat can re-derive, so both seats on its clean twin were undecided; the finder-model request cited "28 seeded corpus cases" that its evidence never gave, and Opus held it there; and the disk alert's twin claimed the higher threshold "missed nothing" while actionable alerts fell from 10 to 9, which both seats flagged.
Those three requests were corrected once, with the prompt, the seeds and the scoring unchanged, and every case was run again.
The first run's counts were wrong numbers held 5 and 6 of 6, one-sided caught 5 and 6 of 6, false holds 0 and 1 of 18, false one-sided flags 4 and 1 of 12, with 4 undecided seats, Sonnet first.
Its one Sonnet miss shows the limit of the design: on the planted 184.00 USD, Sonnet's counter-case named the error in prose, then derived the number as `2300 * 0.08`, from the wrong minutes, and the gate, which checks that each operand is written on its line but not that it is the right figure, agreed.
In the second run Sonnet derived it from the macOS minutes and held it.
At 6 plants a kind these are smoke counts: a seat whose re-derivation reproduces a wrong figure from the wrong operands is the miss to measure on a larger set, before the row leaves shadow.

**Proof.**
`.ci/test-critic.py` runs `run/critic.py` on the recorded seats, `.ci/fixtures/review/corpus/critic/NAME/MODEL/`, through `claude-recorded.py`, and `.ci/test-critic-eval.py` replays the eval; each case is run again against a copy with its control removed, and the eval's first case pins the table above.
No case calls a model; an answer the live run did not give is written into a real envelope.
The gates-proof rows are `critic-hold` (the real hold on the planted cost, against the real pass on its twin), `critic-unquoted` (a paraphrased counter-case refused), `critic-eval-miss` (a hold off the seed) and `critic-eval-false-alarm` (a hold on a clean twin).
No workflow and no firstmate step calls `run/critic.py` yet; a critic seat inside the routed review, whose hold would fold as a `fail` on a change that commits a request, is the follow-up.

## The licence row

`run/licence.py [--policy FILE] [root]` judges the licence of every dependency and vendored file a repository tracks against its `.github/licence-policy.toml`.
It is report 3.2's "licence allowlist row for npm and uv (new, P3a-RP)", accepted when "a GPL-only dependency into an Apache-2.0 repository is refused", extended to Cargo and pnpm lockfiles by review R3 (P3a-LIC).
Its docstring owns the exit statuses (0 every licence allowed or reviewed, 1 a finding, 2 could not decide, with a typed `undecided (TYPE)` line); a finding is `licence: refused: WHERE: WHY` or `licence: review: WHERE: WHY`.

```toml
allowed = ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD"]
review = ["MPL-2.0", "LGPL-2.1-or-later"]
refused = ["GPL-2.0-only", "GPL-3.0-only", "GPL-3.0-or-later", "AGPL-3.0-only", "AGPL-3.0-or-later"]
vendored = ["vendor/**"]                     # files whose own licence is judged

[declared]                                   # a licence the lockfile does not record
"uv:requests" = "Apache-2.0"                 # npm:NAME, uv:NAME, cargo:NAME, any @VERSION, or path:GLOB
"path:vendor/leftpad/**" = "MIT"

[reviewed]                                   # the expression accepted, and why it needed review
"npm:some-mpl-lib" = { accepts = "MPL-2.0", reason = "maintainer decision 2026-10-19: used unmodified" }
```

| Source | Its licence |
| --- | --- |
| a tracked `package-lock.json` (none under `node_modules`) | each entry's `license`, the legacy object and list forms included; the project's own entry and workspace links are its code and are skipped; a lockfileVersion 1 records none |
| a tracked `uv.lock` | none is recorded, so a `[declared]` entry; the editable or virtual project is skipped |
| a tracked `pnpm-lock.yaml` (lockfileVersion 6 or 9) | none is recorded, so each package's own `package.json` in the installed tree beside it, keyed `npm:NAME`, taken only at the path its lock entry derives: `node_modules/.pnpm/NAME@VERSION` (a `_` peer suffix allowed, `/` in a scope written `+`) or `node_modules/NAME` when hoisted, and only when its own name and version match and no symlink lies on the path; a package not installed there needs review |
| a tracked `Cargo.lock` | each crate's `license` from `cargo metadata --manifest-path ... --offline --locked --all-features`, run from an empty directory outside the tree with `RUSTC_WRAPPER` and `RUSTC_WORKSPACE_WRAPPER` emptied and `RUSTC` unset, with the legacy `MIT/Apache-2.0` read as `OR`; a crate with no `source` is a workspace member or path dependency and is skipped |
| a tracked file under a `vendored` glob | its own `SPDX-License-Identifier` in its first 30 lines, else a `[declared]` path entry; a vendored tree's `LICENSE`, `COPYING`, `NOTICE` and `README` files are its notices and are skipped |

A dependency's exact `@VERSION` entry wins over its name, because a licence can change between releases.
Ids are compared as SPDX compares them, without regard to case, in the policy's lists, in `[reviewed]` and in the expression: `gpl-3.0-only` is refused when the policy refuses `GPL-3.0-only`.
The expression is SPDX: `OR` takes its best alternative and `AND` its worst part, so `MIT OR GPL-3.0-only` is allowed and `MIT AND AGPL-3.0-only` refused; an `X WITH EXCEPTION` pair counts only as listed, since an exception changes the terms.
An id no list names, an expression that does not parse, and a dependency or file with no licence each need review, never pass: an unknown licence is the case the row exists for.
Needing review refuses until `[reviewed]` records the expression it accepts and why, and no entry accepts a refused licence; the policy is the one place a reviewed decision lives, so the next change reads it.
An entry accepts only the expression it records, so a dependency whose licence has since changed to another needing review is reported again, naming both; an entry cannot accept a dependency with no licence, which is named under `[declared]` instead.
An id in two lists (in any case), an unknown key, a `[reviewed]` value that is not a table of `accepts` and `reason`, or a malformed table is `bad-policy`.

Both new readers need the caller's tree installed first: `pnpm install` for the package.json files, and `cargo fetch --locked` (or a `cargo vendor` directory source) for the crates, since `--offline` makes no network call.
The tree configures neither cargo nor its toolchain, because cargo takes its configuration, and rustup its override, from the working directory, and a `rustc-wrapper` or `rustc` there names a program the row would run: the toolchain is the runner's own, and a vendored tree's `[source]` replacement belongs in the runner's `CARGO_HOME` configuration, not in the tree's `.cargo/config.toml`.
A crate cargo cannot resolve offline, or a `Cargo.lock` behind its `Cargo.toml`, is `undecided (tool-error)`, because `--locked` refuses rather than rewriting the caller's lockfile; `--all-features` resolves the crates only an optional feature pulls in, without which 7 of quoth's 612 locked crates went unread (measured 2026-10-24).
`cargo deny` was rejected for this: its advisories half duplicates OSV and `cargo audit`, and only its licence half was new.

Left out: recognising a licence from its text, which a header or a `[declared]` entry states instead; any registry lookup, which would make the row neither offline nor deterministic; and any lockfile but the four named above (`yarn.lock`, `bun.lock`, `poetry.lock` and `go.sum`, say), which the row skips without a word.
The whole tracked tree is judged, not only the change, so a lockfile change also reports an older dependency no policy covers; a caller's first run is where those become `[declared]` or `[reviewed]` entries.

**The row**, in a caller's `.github/gates.toml`, in shadow until the promotion rule in `run/gate.py` lets it block, and scoped to the files it reads:

```toml
[[gate]]
id = "licence"
inputs = ["**/package-lock.json", "**/uv.lock", "**/pnpm-lock.yaml", "**/Cargo.lock", "vendor/**", ".github/licence-policy.toml"]
scoped = true
run = 'python3 "$GATES_DIR/run/licence.py"'
shadow_since = 2026-10-19
```

Measured on the licence corpus above: the row refuses 6 of 6 seeded head trees (five refused licences, among them the `gpl-crate` and `gpl-pnpm` cases, and the copied file with no licence needing review) and passes 4 of 4 clean ones, where the model review caught 3 of the 4 seeded cases it was run on; the two newer cases have no recorded model run.
On quoth's tree (2026-10-24) the row reads all 612 crates of `apps/desktop/src-tauri/Cargo.lock`: none refused, and 5 MPL-2.0 crates (`cssparser`, `cssparser-macros`, `dtoa-short`, `option-ext`, `selectors`) need a `[reviewed]` entry in quoth's policy under a proprietary-project policy.
`.ci/test-licence.py` runs each rule with its control removed, folds the row above to `shadow` under `run/gate.py` and shows an unrelated change leaves it unselected, and runs the corpus check.
The gates-proof rows are `licence-refused`, `licence-review` and `licence-vendored`.
No workflow calls `run/licence.py` or `run/corpus.py`: a step is the maintainer's weekly batch.

## The shadow job

`shared-gate-shadow.yml` runs a caller's `.github/gates.toml` through `run/gate.py` beside the caller's own check jobs and records what the runner decides, so the 14-day comparison of plan row P3a-M6 can be read later.
It gates nothing, in three places:

- `run/gate-shadow.sh` exits 0 whatever `run` and `fold` returned, and writes the outcome as `verdict=` in `shadow-verdict.txt`: `clear`, `shadow-red` (a shadow row failed), `refused` (a blocking row failed) or `undecided` (no registry, a bad one, a base that is not in the checkout).
- The runner step is `continue-on-error`.
- A caller keeps its shadow job out of the `needs` of its required status: `needs` reaches only jobs of the same file, so a caller puts the call in its own workflow file, as `gate-shadow.yml` does here.

Each run uploads `$RUNNER_TEMP/gate-shadow` as the artifact `gate-shadow-<run id>-<attempt>`: `records.jsonl`, the logs, `fold.txt` and `shadow-verdict.txt`, which names the head.
The comparison is a head-by-head read of those artifacts against the caller's check runs on the same head; agreement on 99% of heads, and no head where the shadow was `clear` and an existing job failed as a defect, are the acceptance lines of study g1's M6.

The rows run on a stock `ubuntu-24.04` runner with `bash`, `git` and `python3` and nothing installed.
A row that needs another tool declares it under `tools`, and the runner records an `error` for it where the tool is absent; the bootstrap installs none, so a caller's tool-heavy jobs wait for an install step that this window does not add.
This repository's rows are the slice of ci.yml's `shell` job that needs only those three.

**Planted failure.**
`.ci/test-gate-shadow.py` plants a row that exits 1 in a fixture caller and runs the script under `bash -e`.
The script exits 0 and the record is `fail`; against a copy that propagates the runner's status, the same case fails.
A required check cannot go red from this job in any case, because the ruleset requires `checks` alone and `checks` needs only the jobs of `ci.yml`.

**Workflow files** (for the weekly batch): `.github/workflows/shared-gate-shadow.yml` and `.github/workflows/gate-shadow.yml`.
The other seven repositories each add one caller workflow file and a `.github/gates.toml` as a follow-up; none is changed here.

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
