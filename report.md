# gates-issue-12: shadow caller rollout to quoth (`factory-p3a-m6-rollout`), scout report

Scout only. Nothing in any repository was changed except this file. Base: gates `main` at `1739f4557dad8e59583bda236eb3b380b25a97ea`, 2026-11-16.

## What I could not do (read first)

- **I could not open the quoth pull request.** The deliverable is a PR in `Abhijeet34/quoth`, but this session's GitHub access covers `Abhijeet34/gates` only. There is also a second reason the PR has to come from the maintainer: the builder's token is refused on any write under `.github/workflows/` (README.md:132, measured 2026-10-16), and by the 2026-10-16 merge-authority decision workflow edits go in the maintainer's weekly batch.
- **I read quoth only through GitHub code search** (`gh api -X GET search/code`, default branch only, text-match fragments). I never saw a whole quoth file. Re-check quoth's default branch before you open the PR.
- **I could not read `apps/desktop/scripts/cargo-gate.sh`** or quoth's `ci.yml` in full. So I cannot say whether quoth's one registry row can pass on a stock runner. Section 3 explains why that matters.
- **I could not confirm the blocker `factory-p1-g3-pin-quoth` from its record.** The evidence below shows it has landed in effect (quoth pins gates at v7), but its backlog state lives in the private source repository.

## Summary

1. **The blocker is cleared in practice.** quoth's two gates calls are pinned to a release commit, `bd934625b687a5741e8a369ffb23ef5ad8eb39da # v7`. Neither is at `@main`.
2. **Half of the deliverable already exists.** quoth already has a `.github/gates.toml` with one row, `cargo`, in shadow since 2026-10-20. What is missing is only the caller workflow: no quoth file mentions `shared-gate-shadow` (search total 0).
3. **The pin should be v7, not the v5 the goal names.** v5 runs fine for quoth's row, but v7 is what quoth's other gates calls pin. quoth's `dependabot.yml` groups `Abhijeet34/gates/*`, so all the pins should move together. v6 and later also carry two shadow fixes v5 lacks (section 2).
4. **Main risk:** quoth's only row runs cargo against a Tauri crate on a stock `ubuntu-24.04` runner. The shadow bootstrap installs nothing (README.md:1742-1744). If `cargo-gate.sh` needs the system libraries or the built frontend that quoth's own CI sets up first, every selected run will record an environmental `fail`. The 14-day comparison would then measure the runner, not the gate. Check this before or right after merging.
5. **What remains is one maintainer PR in quoth**, adding `.github/workflows/gate-shadow.yml` (draft in section 4), plus a merge word from the captain.

## Evidence

### 1. quoth's current state (code search, 2026-11-16)

```
q='repo:Abhijeet34/quoth Abhijeet34/gates'        -> 4 hits
  .claude/rules/secret-scanning.md
  .github/dependabot.yml
  .github/workflows/secret-scan.yml
  .github/workflows/dependency-advisories.yml
q='repo:Abhijeet34/quoth shared-gate-shadow'      -> 0
q='repo:Abhijeet34/quoth filename:gates.lock'     -> 0
q='repo:Abhijeet34/quoth filename:gates.toml'     -> 1  .github/gates.toml
q='repo:Abhijeet34/quoth path:.github/workflows gates' -> secret-scan.yml, dependency-advisories.yml, ci.yml
```

Fragments:

- `secret-scan.yml`: `uses: Abhijeet34/gates/.github/workflows/shared-secret-scan.yml@bd934625b687a5741e8a369ffb23ef5ad8eb39da # v7`
- `dependency-advisories.yml`: `uses: Abhijeet34/gates/.github/workflows/shared-dependency-advisories.yml@bd934625b687a5741e8a369ffb23ef5ad8eb39da # v7`
- `dependabot.yml`: a group with `patterns: - "Abhijeet34/gates/*"`, and `exclude-patterns: - "Abhijeet34/gates/*"` on the general `github-actions` group. This means Dependabot moves all gates pins in one PR.
- `.github/gates.toml` (pieced together from several fragments):

  ```toml
  # quoth's gate registry, read by gates' run/gate.py (and `fac gate`) from the base.
  # inputs mirror RUST_PATHS in .githooks/pre-push.local, which stays the one list;
  # .ci/test-rust-gate.sh fails when they drift.
  # pre-push still runs this same script: the row blocks nothing for 14 days and 50
  # runs (shadow), and `fac gate` cannot run cargo inside its sandbox yet.
  [[gate]]
  id = "cargo"
  inputs = [
    "apps/desktop/src-tauri/**",
    ...
    "apps/desktop/scripts/cargo-gate.sh",
    ".githooks/pre-push.local",
  ]
  run = "apps/desktop/scripts/cargo-gate.sh"
  tools = { cargo = "cargo --version" }
  scoped = true
  shadow_since = 2026-10-20
  ```

  The keys used (`id`, `inputs`, `run`, `tools`, `scoped`, `shadow_since`) are all in `ROW_KEYS` (`run/gate.py:85`), so the registry should load. With no `.github/gates.lock` in quoth, the shadow job runs from the bootstrap's own commit (README.md:74).

### 2. Which release to pin

Release commits (`git rev-parse vN^{}`), and the shadow-relevant differences between them:

| tag | commit | date | `export BASE_SHA` in `run/gate-shadow.sh` | upload-artifact in the bootstrap |
| --- | --- | --- | --- | --- |
| v5 | `99048291edfeff0062eb60981f0955b701ee624a` | 2026-10-26 | no | `ea165f8… # v4.6.2` |
| v6 | `b58ba0a4…` | 2026-11-02 | yes | `cf430e0… # v7.0.2` |
| v7 | `bd934625b687a5741e8a369ffb23ef5ad8eb39da` | 2026-11-02 | yes | `cf430e0… # v7.0.2` |
| v8 | `7c07f96851a21041ad9f1cd0915f2c3220686ce4` | 2026-11-03 | yes | `cf430e0… # v7.0.2` |

`git log --oneline 99048291..HEAD -- run/gate.py run/lock.sh .github/workflows/shared-gate-shadow.yml run/gate-shadow.sh` lists only `e3106cc` (#77, the `BASE_SHA` export) and `0aa29fc` (#68, the actions bump). quoth's `cargo` row does not read `BASE_SHA`, so v5 would work. v7 is still the better pin: it matches quoth's other two pins, so the grouped Dependabot update keeps the three lines on one commit and the job runs the same `run/` as quoth's other gates.

The siblings pinned the shadow caller in different ways (code search, `filename:gate-shadow.yml`): foliot, foliot-bench-corpus and fleet-records at v5 (`99048291`); halderworks-design at v4 (`2d686fa3`, with the comment "Pinned to the same gates commit as the callers in ci.yml"); factory at v8. The precedent that fits here is halderworks-design's: match the repository's existing gates pin.

The "pinned at v5" in the goal was written on 2026-10-25, before v6 to v8 existed.

### 3. The cargo-on-a-stock-runner risk

- The shadow bootstrap runs on `ubuntu-24.04` and installs nothing (`.github/workflows/shared-gate-shadow.yml:21-23`, README.md:1742-1744). A row whose `tools` probe fails records `error`. A row whose probe passes but whose `run` fails records `fail`.
- GitHub's `ubuntu-24.04` image ships a Rust toolchain, so the `cargo --version` probe will probably pass. That comes from my prior knowledge of the image; I did not measure it in this session. So the row will actually run.
- README.md:1217 and :1264 record that quoth's crate builds from outside its directory: tauri-build reads `../out`, `../../../docs/SECURITY.md` and the gitignored whisper sidecar. It needs "the frontend built and the sidecar stubbed first, as its own CI does before `cargo test`". A Tauri crate on Linux usually also needs WebKitGTK development packages from apt.
- `scoped = true` (`run/gate.py:36-41`, `:221-226`) limits the damage. The row is selected only when a path under its inputs changes, when the registry changes, or when there is no base. Its shadow verdict cannot block anything (README.md:1730-1734).
- What it can do is poison the P3a-M6 comparison. The acceptance line is agreement on 99% of heads (README.md:1740). Every Rust PR would read `shadow-red` while quoth's own CI is green.

Unless `cargo-gate.sh` prepares its own environment, the comparison will need one of these:
  (a) the bootstrap gains an install/prepare step. That is a gates change, and README.md:1744 says this window does not add one.
  (b) a quoth row whose `run` first does what quoth's CI does before `cargo test`.
  (c) accept that this row's shadow records are environmental, and leave quoth out of the agreement count until (a) lands.

### 4. Draft of the quoth PR (for the maintainer)

`.github/workflows/gate-shadow.yml`, modelled on gates' own caller (`.github/workflows/gate-shadow.yml`), with the pin matched to quoth's existing gates pin:

```yaml
name: gate shadow

# Runs .github/gates.toml through gates' run/gate.py in shadow and keeps the
# records for the P3a-M6 comparison. Not a required status: it lives in its own
# file, so no required job's `needs` can reach it; gates' README.md, "The
# shadow job", owns the design.
on:
  pull_request:
    branches: [main]
  workflow_dispatch:

concurrency:
  group: gate-shadow-${{ github.event_name }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

permissions:
  contents: read

jobs:
  shadow:
    # Pinned to the same gates commit as secret-scan.yml and dependency-advisories.yml; Dependabot moves them together.
    uses: Abhijeet34/gates/.github/workflows/shared-gate-shadow.yml@bd934625b687a5741e8a369ffb23ef5ad8eb39da # v7
```

Notes for that PR:

- **Trigger:** `pull_request` only, with no `push` run. This follows gates' own drop of push-to-main runs (`b871523`, #76). It also means every shadow run has a base, so the `scoped` row is not selected on unrelated PRs.
- **Branch:** check quoth's default branch name before you use `branches: [main]`.
- **`.github/gates.toml`:** no change is needed. It already exists, and adding or editing the registry would select every row anyway (`run/gate.py:222`).
- **Required checks:** the job name is `shadow / gate runner (shadow)`. Do not add it to quoth's ruleset.
- **`tools`:** quoth's workflow-policy checks, if any mirror gates', should pass. The file has no `pull_request_target`, `workflow_run`, `issue_comment` or `self-hosted`, and no secret.

## Recommendations

1. Treat `factory-p1-g3-pin-quoth` as satisfied (quoth pins v7) and unblock this item. Update the goal text from "pinned at v5" to "pinned at quoth's current gates release (v7)".
2. Before the PR, the maintainer reads `apps/desktop/scripts/cargo-gate.sh` and the steps of quoth's `ci.yml` that run before `cargo test`. Then choose (a), (b) or (c) from section 3. If you choose (b), the row change goes in the same PR.
3. The maintainer opens the one quoth PR from section 4 in the weekly workflow batch. It merges on the captain's word.
4. After the first Rust-touching PR in quoth, download its `gate-shadow-<run>-<attempt>` artifact. Check `shadow-verdict.txt` and the `cargo` log, which shows whether it was `fail` for environmental reasons. Do this before counting quoth's heads toward the 14-day comparison.
