# gates

The checks that stand between a commit and a public push: a gitleaks pre-push hook and its rules, a scanner that refuses machine-detectable origin marks, the machine-wide git hooks that run both, and five reusable GitHub Actions workflows that re-check the same things in CI.

Licensed under the Apache License 2.0; see `LICENSE` and `NOTICE`.

## What is here

| Path | What it is |
| --- | --- |
| `.githooks/pre-push`, `.gitleaks.toml` | The repository-local secret gate and the canonical rules. Callers also receive `system-maintenance/git-hooks/commit-msg` as `.githooks/commit-msg`. Callers carry byte copies, distributed by `.ci/gitleaks/sync.sh`, and extend the gate through a tracked, executable `.githooks/pre-push.local`, which it runs last. |
| `system-maintenance/git-hooks/` | The machine-wide hooks deployed to `~/.git-hooks` by `install.sh`, including `watermark-scan.py`. Its `README.md` owns the design and the measurements. |
| `.github/workflows/shared-*.yml` | Reusable workflows: `secret-scan`, `watermark-scan`, `dependency-advisories`, `guard-generated-files`, `no-mistakes-required`. Each is a bootstrap; see "Pinning". |
| `run/` | What each bootstrap runs from the locked gates commit: `lock.sh`, which decides that commit, and one script per gate. |
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
| Downgrade | a pull request moves the lock to a commit that does not descend from the base's. | `lock-downgrade` |
| Drift | the caller's synced copies differ from gates at the locked commit (`run/secret-scan/verify.sh`). | `lock-drift` |

Adding or removing the lock is a lock change: a removal moves the lock to the bootstrap's own commit, and passes only as a bump would.
The bump identity is read from `main` rather than from the caller or the lock, because the pull request under judgement must not name its own author as authorised.
It is bound by numeric id as well as login, because a login is claimed by whoever registers an App of that name first; until the bump App exists, `run/bump-identity` names nobody and every lock change is refused.

The green-checks call uses the caller's `GITHUB_TOKEN` against this public repository.
That a caller's token granted only `contents: read` can read these check runs is not yet measured from another repository; if it cannot, the lock is refused, never trusted.

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
