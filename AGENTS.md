# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- **This repository was cut from `Abhijeet34/automation` at `10ff49e1759885c60b8fe56be46ce16db4ac22af` as a history-free snapshot.**
  The private `automation` repository holds the history and the dated records these files cite; a reference here to a measurement's date is the pointer into that history.
- **This repository is public, so tracked text names no private repository, alert count, run id or local path.**
  Those measurements stay in `automation`; a comment here states the finding and its date, never where it was measured.
- **`gitleaks dir .` in `.ci/check.sh` scans ignored directories too**, so an untracked log quoting a fixture value (a pooled worktree's `.fm-evidence/`, say) fails `secrets` locally while CI passes.
  Validate in a clean clone before reading that failure as the branch's.
- **Each `shared-*.yml` is a bootstrap; the gate is a `run/` script it runs from a checkout of `job.workflow_repository` at the commit the caller's `.github/gates.lock` names, or at `job.workflow_sha` when there is none.**
  `run/lock.sh` decides that commit and owns the controls; README.md, "Pinning", owns the design.
  Logic belongs in `run/`, because YAML reaches a pinned caller only through a workflow-file pin bump, while `run/` reaches it through a lock bump; a lock-less caller at `@main` runs `main` either way.
  From a private repository that checkout answers `Not Found` with the caller's token (measured 2026-09-16), so the bootstraps need this repository to be public for any other repository to use them.
- **Every `run/` script runs on the runner as `bash -e`, so its contract test runs it with `bash -e`.**
  Under `-e` a failing command substitution aborts on the assignment, so an `rc=$?` on the following line never runs, and a `grep` that matches nothing takes the step down under `pipefail`.
- **`.gitleaks.toml`, `.githooks/pre-push` and `system-maintenance/git-hooks/commit-msg` (synced as `.githooks/commit-msg`) are compared byte for byte with gates at the locked commit, not by digest.**
  Changing any of them means re-running `.ci/gitleaks/sync.sh` over every clone that tracks `main`, or those callers go red on their next pull request; a locked caller moves its copies with its lock.
  A caller extends the hook through a tracked, executable `.githooks/pre-push.local`, never by editing its copy; `system-maintenance/git-hooks/README.md` owns the contract.
- **`system-maintenance/git-hooks/README.md` owns the machine-wide hook design**: the allowlist keyed on the normalised `origin`, why the watermark gate is opt-out while gitleaks is opt-in, the `clean`/`verify` verdicts, and the latency numbers.
  Read it before changing a rule, an exemption or the scope.
- **`gitleaks git` exits 0 and reports "no leaks found" when it scanned nothing, and counts only commits it read a patch from.**
  `.githooks/pre-push` compares the scan's count with its own `rev-list` count and refuses a short scan; `.ci/gitleaks/test-pre-push.sh` pins that, including a binary-only push that must still land.
- **Merging a change to anything `install.sh` deploys does not put it in service on this machine; `system-maintenance/git-hooks/install.sh check` after the merge is what shows it.**
  On 2026-09-17 the merged exposure gate was absent and two earlier merges were still stale in `~/.git-hooks`.
  A sandboxed agent shell cannot run `deploy` (`~/.git-hooks` and `~/.gitconfig` answer `Operation not permitted`, every `cp` fails before any rename, so nothing half-lands); that write is deliberately not granted, so the deploy is a one-off unsandboxed act with the fleet confirmed idle.
- **Every workflow file is held to three properties by `.ci/test-workflow-policy.sh`**: no `pull_request_target`, `workflow_run` or `issue_comment` and no `self-hosted` anywhere in the file, comments included, and no secret but `GITHUB_TOKEN`.
  Reword a comment rather than weakening the match; the list form `on: [push, workflow_run]` is why it matches words, not keys.
- **A skipped job satisfies a required status check**, so `checks` must never skip on a pull request; `.ci/checks-verdict.sh` allows no skip and `.ci/test-checks-verdict.sh` fails when a job in `ci.yml` is missing from `checks.needs`.
- **A fixture repository must not inherit this machine's git config**: set `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null`, or `core.hooksPath` runs the global hooks inside the fixture.
  `test-global-hooks.sh` is the exception and must run bare, because it deploys into a throwaway `HOME` whose global config it writes.
- **Tracked text carries no machine identifier and no security-alert count, tests included, because `exposure-scan.py` refuses both on a push to this public repository.**
  A test plants such a value by reading it from the machine or assembling it from a variable at run time; `system-maintenance/git-hooks/README.md`, "The exposure gate", owns the rules.
- **Every gate is registered in `.ci/gate-registry.toml` with executable `red` and `clean` inputs, a `run` and a `finding`, and `.ci/check.sh gates-proof` executes them: red must be refused naming the finding, clean must pass, and red must pass once the row's `command` is neutered.**
  It also fails on an unregistered gate file or a test no suite runs.
  Adding a workflow, hook, `run/` file or `.ci` script means adding its row in the same commit; a git-lfs shim or an installer goes under `[[exempt]]` with a reason.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
