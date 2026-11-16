# gates-issue-7: move callers' `@main` lines onto a current gates release (scout report)

Scout only: nothing in any caller repository was changed. Gates `main` was at `1739f4557dad8e59583bda236eb3b380b25a97ea` (`git ls-remote origin refs/heads/main`) on 2026-11-16.

## What I could not do

- **I read no caller repository directly.** This session's GitHub access covers `Abhijeet34/gates` only. The caller inventory below comes from GitHub code search (`gh api -X GET search/code`), which shows only the indexed default branch and only text-match fragments. Treat it as a strong lead, not a census. Before the edit PRs are opened, re-check each caller's default branch.
- **I opened no caller PR.** The task brief makes this a report-only task. The merge-authority decision (2026-10-16) also puts every `.github/workflows/` edit in the maintainer's weekly batch.

## Summary

1. **The goal's premise is out of date.** It says "v5 is out; v1 is stale", but the latest release is **v8**: `7c07f96851a21041ad9f1cd0915f2c3220686ce4`, published 2026-11-03. v6 and v7 came out on 2026-11-02. 17 commits have landed on `main` since v8 (`git rev-list --count v8..HEAD` gives `17`).
2. **Only one caller still has `@main` lines: `no-mistakes`, with two of them.** Every other caller that code search finds already pins a commit, and every pin but one carries a `# vN` comment.
3. **Pinning `no-mistakes` will break its own test unless the test changes in the same PR.** `workflow_guard_generated_files_test.go` asserts the literal string `uses: Abhijeet34/gates/.github/workflows/shared-guard-generated-files.yml@main`. Its `AGENTS.md` also explains why it calls `@main`.
4. **Recommendation:** cut **v9** from current `main`, then pin both `no-mistakes` lines to it. Change the Go test and the `AGENTS.md` sentence in the same PR. Pinning to v8 is the fallback, and the trade-off is below. Separately, `pointback` is still on **v1** and `papertrace` on an untagged commit. Both are outside this task's literal `@main` scope but match its intent ("v1 is stale").

## Evidence

### Releases (gates)

`list_releases` / `list_tags` (GitHub MCP), and `git merge-base --is-ancestor <tag> HEAD` on a clone of `main`:

| tag | commit | published | ancestor of current `main` |
| --- | --- | --- | --- |
| v1 | 9634dea8b931 | 2026-10-16 | **no** |
| v2 | cef2b36e0c0e | 2026-10-21 | **no** |
| v3 | 9dda2e358e6f | 2026-10-22 | **no** |
| v4 | 2d686fa31bf6 | 2026-10-25 | yes |
| v5 | 99048291edfe | 2026-10-25 | yes |
| v6 | b58ba0a4a155 | 2026-11-02 | yes |
| v7 | bd934625b687 | 2026-11-02 | yes |
| v8 | 7c07f96851a2 | 2026-11-03 | yes |

The first commit in `main`'s history is `2b9e02d … (#51)`, from 2026-10-24. v1's history ends at `0032722 Initial snapshot from Abhijeet34/automation …`, so v1 to v3 are no longer ancestors of `main`.
Consequence: `run/lock.sh:57-78` refuses a `.github/gates.lock` that names v1 to v3 ("not on gates main"). A caller with no lock still runs them, because with no lock the gate runs from `job.workflow_sha` with no ancestry check (`run/lock.sh:7`, README "Pinning").

### Caller inventory (code search, default branches)

Query: `"Abhijeet34/gates/.github/workflows" user:Abhijeet34 path:.github/workflows`, with the text-match media type. That gave 34 workflow files in 18 repositories. The ref is the one in each match fragment.

| repo | file(s) | ref |
| --- | --- | --- |
| **no-mistakes** | `.github/workflows/secret-scan.yml` | **`shared-secret-scan.yml@main`** |
| **no-mistakes** | `.github/workflows/guard-generated-files.yml` | **`shared-guard-generated-files.yml@main`** |
| pointback | `ci.yml` (secret-scan), `dependency-advisories.yml` | `9634dea… # v1`: stale and on the history `main` no longer contains |
| papertrace | `secret-scan.yml` | `5fdaf78d8d10…` with **no `# vN` comment**. The commit is on `main` (`git describe --contains` gives `v6~3`, #80), but with no tag comment Dependabot has nothing to rewrite it from |
| halderworks-design | `ci.yml` (secret-scan, watermark-scan), `gate-shadow.yml` | v4 |
| foliot | `dependency-advisories.yml` v4, `gate-shadow.yml` v5 | |
| fleet-records | `watermark-scan.yml` v4, `gate-shadow.yml` v5, `secret-scan.yml` v7 | |
| foliot-bench-corpus | `gate-shadow`, `secret-scan`, `watermark-scan` | v5 |
| chrome-devtools-axi, gh-axi, quota-axi, tasks-axi | `guard-generated-files`, `secret-scan` | v7 |
| quoth | `dependency-advisories`, `secret-scan` | v7 |
| automation, factory, firstmate, ops | all of theirs | v8 |

`filename:gates.lock user:Abhijeet34` finds a lock only in `automation`, `firstmate` and `ops`. Every other caller runs the gate code (`run/`) from its pinned bootstrap commit, so its workflow pin is also its gate version.

### `no-mistakes` evidence (search fragments at blob ref `219f2e6a69ea…`)

- `.github/workflows/secret-scan.yml`: job `secrets`, top-level `permissions: contents: read`, `uses: Abhijeet34/gates/.github/workflows/shared-secret-scan.yml@main`.
- `.github/workflows/guard-generated-files.yml`: job `check`, with an `if:` that exempts `github-actions[bot]` and `release-please[bot]`, `uses: Abhijeet34/gates/.github/workflows/shared-guard-generated-files.yml@main`.
- `workflow_guard_generated_files_test.go`, `TestGuardGeneratedFilesWorkflowCallsTheSharedCheckBody`: `const shared = "uses: Abhijeet34/gates/.github/workflows/shared-guard-generated-files.yml@main"` followed by `strings.Contains(content, shared)`. **This test fails once the line is pinned.**
- `AGENTS.md`: "That is why this repository's guard calls `…shared-guard-generated-files.yml@main` rather than carrying its own body". The reason given is to call the shared body instead of inlining a copy. A commit pin still calls the shared body, so only the ref wording needs to change.
- `.github/workflows/no-mistakes-required.yml` is a local workflow in `no-mistakes`, not a gates call. Gates dropped `shared-no-mistakes-required.yml` in `a817e45 (#109)` after v8. Before pinning, check that nothing in `no-mistakes` still calls the shared one.

### Which release to pin: v8 or a new v9

For the two `no-mistakes` bootstraps, v8 and `main` are byte-identical:
`git diff --quiet v8 HEAD -- .github/workflows/shared-secret-scan.yml .github/workflows/shared-guard-generated-files.yml && echo identical-v8-main` prints `identical-v8-main`.
The synced copies match too: `git diff --stat v8 HEAD -- .gitleaks.toml .githooks/ system-maintenance/git-hooks/commit-msg` prints nothing. So `run/secret-scan/verify.sh` judges `no-mistakes`' copies the same way at either commit.

`no-mistakes` has no lock, so pinning changes which `run/` executes, from `main` to the pinned commit. The gate-relevant change between v8 and `main`:
`git diff --stat v8 HEAD -- run/secret-scan run/secret-scan.sh run/lock.sh run/guard-generated-files.sh run/install-gitleaks.sh` gives `run/install-gitleaks.sh | 5 ++++-`, which is `faffe1d fix(install-gitleaks): retry the download through a connection reset (#114)`.
**A v8 pin would silently drop that fix for `no-mistakes`.** That is why v9 from current `main` is the better target, provided its `checks` run is green on the tagged commit. The procedure is in README "Pinning": `gh workflow run ci.yml --ref main`, then `gh release create v9 --target <commit> --generate-notes`.

For the follow-up callers, one default moved after v8: `shared-dependency-advisories.yml`'s `fail_on_development` went from `default: true` to `default: false` (`8a945d6 (#105)`). Moving an advisories caller (foliot, pointback, quoth, automation) from v8 or earlier to v9 loosens its default. A caller whose build bundles devDependencies must then set `fail_on_development: true` explicitly.

### Constraints the edit PRs meet

- **Merge authority:** every edit touches `.github/workflows/`. The builder token is refused there (README "Bumping": `Resource not accessible by integration`), so these PRs go to the maintainer's weekly batch. `bin/gates.py bump` cannot make them because it moves only `.github/gates.lock`.
- **Pinning is one-way:** README "The weakening guard" says "A moving ref that replaces a commit pin of the same workflow or action is refused whatever the records say." Once pinned, `no-mistakes` cannot return to `@main`. The `first-party-shared-at-main` record in `policy/relaxations.toml` exists only to allow the `@main` lines that are already there.
- **Ongoing bumps:** after this, Dependabot's `github-actions` updater is the writer that moves a commit pin with a `# vN` comment (README "Pinning"). I could not confirm whether `no-mistakes` configures it; code search found no `dependabot.yml` hit.

## Recommended plan (three PRs, matching plan row P1-G3's three tasks)

1. **gates:** dispatch CI on `main` and, once `checks` is green, cut **v9** at that commit. Optionally change README.md:53's example comment from `# v1` to `# vN`, since it reads as advice to pin v1.
2. **no-mistakes**, one PR in the weekly batch:
   - `secret-scan.yml`: `@main` becomes `@<v9 sha> # v9`.
   - `guard-generated-files.yml`: the same change.
   - `workflow_guard_generated_files_test.go`: assert the shared path plus a 40-hex commit pin, matching for example `shared-guard-generated-files\.yml@[0-9a-f]{40} # v[0-9]+`, rather than one literal sha that every Dependabot bump would also have to edit.
   - `AGENTS.md`: reword the `@main` sentence.

   Check: the PR's `secrets` and `check` jobs pass, and `go test ./...` passes.
3. **Follow-up, not `@main`, same batch:** move `pointback` (v1, two files) and `papertrace` (untagged `5fdaf78`) to `@<v9 sha> # v9`. The v1 and v8 bootstraps for `shared-secret-scan.yml` and `shared-dependency-advisories.yml` are byte-identical (`git diff --quiet v1 v8 -- …` gives `identical-v1-v8`). The only run-side difference from v1 to v8 is 16 added lines in `.gitleaks.toml`, so `pointback` needs `.ci/gitleaks/sync.sh` run in the same PR. Without it, `pointback`'s copies drift against v9 and `secrets` fails. The v4, v5 and v7 callers are Dependabot's or the lock's job, not this task's.
