# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- **This repository was cut from `Abhijeet34/automation` at `10ff49e1759885c60b8fe56be46ce16db4ac22af` as a history-free snapshot.**
  The private `automation` repository holds the history and the dated records these files cite; a reference here to a measurement's date is the pointer into that history.
- **Every shared-workflow step body runs on the runner as `bash -e {0}`, so its contract test runs the extracted body with `bash -e`.**
  Under `-e` a failing command substitution aborts on the assignment, so an `rc=$?` on the following line never runs, and a `grep` that matches nothing takes the step down under `pipefail`.
  Each `.ci/test-*.sh` extracts its `run:` block by indentation and asserts a marker at both ends, because a continuation line at a shallower indent truncates the body silently.
- **`.gitleaks.toml` and `.githooks/pre-push` are pinned by digest in `shared-secret-scan.yml`, and `.ci/test-secret-scan.sh` fails when a pin goes stale.**
  Changing either file means changing its pin in the same commit and re-running `.ci/gitleaks/sync.sh` over every clone, or every caller goes red on its next pull request.
- **`shared-watermark-scan.yml` reads its scanner from a checkout of `job.workflow_repository` at `job.workflow_sha`, never from the caller's tree.**
  From a private repository that checkout answers `Not Found` with the caller's token (measured 2026-09-16), so the workflow needs this repository to be public for any other repository to use it.
  `.ci/test-watermark-scan-workflow.sh` reads the scanner path from the YAML and fails if a scanner carried by the caller decides the verdict.
- **`system-maintenance/git-hooks/README.md` owns the machine-wide hook design**: the allowlist keyed on the normalised `origin`, why the watermark gate is opt-out while gitleaks is opt-in, the `clean`/`verify` verdicts, and the latency numbers.
  Read it before changing a rule, an exemption or the scope.
- **`gitleaks git` exits 0 and reports "no leaks found" when it scanned nothing, and counts only commits it read a patch from.**
  `.githooks/pre-push` compares the scan's count with its own `rev-list` count and refuses a short scan; `.ci/gitleaks/test-pre-push.sh` pins that, including a binary-only push that must still land.
- **Every workflow file is held to three properties by `.ci/test-workflow-policy.sh`**: no `pull_request_target`, `workflow_run` or `issue_comment` and no `self-hosted` anywhere in the file, comments included, and no secret but `GITHUB_TOKEN`.
  Reword a comment rather than weakening the match; the list form `on: [push, workflow_run]` is why it matches words, not keys.
- **A skipped job satisfies a required status check**, so `checks` must never skip on a pull request; `.ci/checks-verdict.sh` allows no skip and `.ci/test-checks-verdict.sh` fails when a job in `ci.yml` is missing from `checks.needs`.
- **A fixture repository must not inherit this machine's git config**: set `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null`, or `core.hooksPath` runs the global hooks inside the fixture.
  `test-global-hooks.sh` is the exception and must run bare, because it deploys into a throwaway `HOME` whose global config it writes.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
