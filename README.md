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
