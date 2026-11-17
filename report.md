# gates-issue-12-2: shadow caller rollout to quoth (`factory-p3a-m6-rollout`), scout report

Scout only: nothing changed in any repository but this file.
Base: gates `main` at `cc7e3c44cd125c2335b835a24791b4dab63db650` (2026-11-17).
This is a re-run of `fac-remote/gates-issue-12` (report `2a08896`, 2026-11-16).
I re-checked that report's findings and closed its main open question: whether quoth's one registry row can run on the shadow runner. It cannot. Section 3 has the evidence.

## What I could not do (read first)

- **I could not open the quoth PR.** This session's GitHub access covers `Abhijeet34/gates` only.
  The builder's token is also refused on any write under `.github/workflows/` (README.md, "Bumping": "A file under `.github/workflows/` is refused to the builder's default token"). So the caller workflow has to come from the maintainer in any case.
- **I read quoth only through `gh api search/code` text-match fragments** of its default branch. I never saw a whole quoth file.
  The facts below are quoted from those fragments. Re-read the files before acting on them.
- **I could not read the backlog record for `factory-p1-g3-pin-quoth`.** It lives in the private source repository. I can only show that the pin has landed in effect (section 1).

## Summary

1. **The blocker is cleared in effect.** quoth pins both of its gates calls at `bd934625b687a5741e8a369ffb23ef5ad8eb39da # v7`. Neither call is at `@main`. Nothing has changed here since 2026-11-16.
2. **quoth already has its `.github/gates.toml`.** It holds one row, `cargo`, in shadow since 2026-10-20.
   What is missing is only the caller workflow: a search for `shared-gate-shadow` in quoth returns 0 hits.
3. **New finding, and a blocker for this item's purpose:** the `cargo` row cannot run on the shadow runner.
   - quoth is a macOS-only product. Its `run`, `apps/desktop/scripts/cargo-gate.sh`, is "the macOS CI job's exact sequence".
   - That script exits 10 ("cannot run here") unless `apps/desktop/out` exists and the `whisper-server-aarch64-apple-darwin` sidecar is staged. Neither is true on a fresh `ubuntu-24.04` checkout.
   - `run/gate.py` records any non-zero exit as `fail` (`run/gate.py:445-448`). If the runner has no `cargo`, the tool probe records `error` instead.
   - Either way, every quoth PR that touches Rust would read `shadow-red` or `error`, while quoth's own PR CI, which stubs cargo, is green.
   - That 100% environmental disagreement would poison the P3a-M6 comparison, which needs agreement on 99% of heads (README.md, "The shadow job").
4. **Recommendation:** do not ship the shadow caller to quoth as it stands, because it would record noise. Instead:
   - (a) add a quoth row that can run on Linux, and scope `cargo` out of the shadow comparison; or
   - (b) explicitly exclude quoth from the P3a-M6 agreement count and ship the caller only for wiring; or
   - (c) park this item until the bootstrap can prepare an environment or run on macOS. README.md says this window adds neither.

   This choice belongs to the captain. If (a) or (b) is chosen, the PR draft is in section 4.
5. **Pin at v7, not the goal's v5.** v7 is what quoth's other two gates calls pin, and quoth's `dependabot.yml` groups `Abhijeet34/gates/*` so the pins move together. v5 also lacks the `BASE_SHA` export (#77) and the upload-artifact bump (#68).

## Evidence

### 1. quoth's gates wiring (code search, 2026-11-17)

```
q='repo:Abhijeet34/quoth Abhijeet34/gates'          -> 4
  .github/workflows/secret-scan.yml:           uses: Abhijeet34/gates/.github/workflows/shared-secret-scan.yml@bd934625b687a5741e8a369ffb23ef5ad8eb39da # v7
  .github/workflows/dependency-advisories.yml: uses: Abhijeet34/gates/.github/workflows/shared-dependency-advisories.yml@bd934625b687a5741e8a369ffb23ef5ad8eb39da # v7
  .github/dependabot.yml:  patterns: - "Abhijeet34/gates/*"   (and exclude-patterns: - "Abhijeet34/gates/*" on the github-actions group)
  .claude/rules/secret-scanning.md
q='repo:Abhijeet34/quoth shared-gate-shadow'        -> 0
q='repo:Abhijeet34/quoth filename:gates.lock'       -> 0
q='repo:Abhijeet34/quoth filename:gates.toml'       -> 1   .github/gates.toml
```

quoth has no `.github/gates.lock`. The shadow job would therefore run `run/` from the bootstrap's own commit (README.md, "Pinning").

Its registry, as the 2026-11-16 report pieced it together from fragments:

- one row: `id = "cargo"`
- `run = "apps/desktop/scripts/cargo-gate.sh"`
- `tools = { cargo = "cargo --version" }`
- `scoped = true`
- `shadow_since = 2026-10-20`
- `inputs` mirror `RUST_PATHS` in `.githooks/pre-push.local`

All of these keys are in `ROW_KEYS` (`run/gate.py:85`).

How the sibling repositories pin the shadow caller (`user:Abhijeet34 shared-gate-shadow`, 14 hits):

| repository | pin |
| --- | --- |
| foliot | `99048291… # v5` |
| fleet-records | `99048291… # v5` |
| foliot-bench-corpus | `2d686fa3…`, which is v4 per the ops forge snapshot |
| halderworks-design | `2d686fa3… # v4`, with the comment "Pinned to the same gates commit as the callers in ci.yml" |
| factory | `7c07f968… # v8`, with the comment "the same one every gates call here pins" |

The rule the siblings follow is to match the repository's other gates pins. For quoth that is v7.

### 2. Release commits and shadow-relevant drift

```
$ git rev-parse 'v5^{}' 'v7^{}' 'v8^{}'
99048291edfeff0062eb60981f0955b701ee624a
bd934625b687a5741e8a369ffb23ef5ad8eb39da
7c07f96851a21041ad9f1cd0915f2c3220686ce4
$ git log --oneline v5..HEAD -- .github/workflows/shared-gate-shadow.yml run/gate-shadow.sh run/gate.py
0aa29fc ci: bump the actions group across 1 directory with 3 updates (#68)
e3106cc fix(run): export BASE_SHA from the shadow script so documented rows see their base (#77)
$ git diff --stat v5 HEAD -- .github/workflows/shared-gate-shadow.yml run/gate-shadow.sh run/gate.py run/lock.sh
 .github/workflows/shared-gate-shadow.yml | 2 +-     (upload-artifact v4.6.2 -> v7.0.2)
 run/gate-shadow.sh                       | 3 +++   (export BASE_SHA)
```

The goal's "pinned at v5" was written on 2026-10-25, before v6, v7 and v8 existed (v5 is dated 2026-10-26).

### 3. Why quoth's `cargo` row cannot run in the shadow job (new)

Quotes from quoth (code search fragments):

- `apps/desktop/scripts/cargo-gate.sh`:
  - "The Rust gate: the macOS CI job's exact sequence, plus the model its sidecar tests need."
  - "Exit 0 pass, 10 cannot run here, 11 fmt, 12 bare clippy, 13 bare test, 14 featured clippy, 15 featured test."
  - `[[ -d ../out ]] || cannot "apps/desktop/out is missing; run 'npm run build' in apps/desktop"`
  - `[[ -x binaries/whisper-server-aarch64-apple-darwin ]] || cannot "the whisper sidecar is not staged; run apps/desktop/scripts/build-whisper.sh"`
- `.github/workflows/ci.yml`:
  - "macOS bills at 10.3x the Linux minute rate … so the macOS job runs weekly and on demand rather than on every pull request. There is no Windows job: quoth is a macOS-only product (objc2, tauri-nspanel, macOS private API)"
  - "The per-push Rust gate's decisions and path coverage, with cargo stubbed; the gate itself needs a Mac and runs on every push from one."

What gates does with that row:

- `.github/workflows/shared-gate-shadow.yml:21` runs on `ubuntu-24.04` and installs nothing. README.md, "The shadow job", says the bootstrap installs no tools and "this window does not add" an install step.
- `run/gate.py:436-448` runs `bash -e -c <run>`. A non-zero exit is `verdict = "fail"`, so exit 10 is recorded as `fail`, with no separate "cannot run" verdict. A failing tool probe raises `Undecided`, which is recorded as `error` (`run/gate.py:412-421`).
- `run/gate-shadow.sh` turns a failed shadow row into `verdict=shadow-red`.
- `scoped = true` (`run/gate.py:221-226`) limits how often the row is selected: only by its inputs or a registry change on a PR. But the gate.py docstring says "No base and no change still select it", so a `workflow_dispatch` run selects it too.

Result: on every Rust-touching quoth PR, the shadow reads `shadow-red` (or the row reads `error`), while quoth's PR CI is green with cargo stubbed. The P3a-M6 comparison would measure the runner's operating system, not the gate.

### 4. Draft of the quoth caller (for the maintainer, if option (a) or (b) is chosen)

`.github/workflows/gate-shadow.yml`, modelled on gates' own `.github/workflows/gate-shadow.yml`:

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

- **Default branch:** confirm quoth's default branch is `main`. Its `ci.yml` fragment shows `push: branches: [main]`.
- **Required checks:** do not add the job (`shadow / gate runner (shadow)`) to quoth's required checks.
- **Option (a)** also changes `.github/gates.toml` in the same PR. Any registry change selects every row (`run/gate.py:222`), so the first run exercises all of them.

## Recommendations

1. **Captain decision first:** choose (a), (b) or (c) from the summary. My pick is (c), park.
   - quoth's only real gate needs a Mac, so a Linux shadow can only record noise.
   - A Linux-only stand-in row under (a) would measure something quoth's PR CI already runs with cargo stubbed, which adds little to M6.
   - Revisit when the bootstrap gains a prepare step or a macOS runner option.
2. **Update the backlog goal text** from "pinned at v5" to "pinned at the repository's current gates release (v7 for quoth)". Treat `factory-p1-g3-pin-quoth` as satisfied: quoth pins v7.
3. **If shipping anyway:** the maintainer opens the section 4 PR in the weekly workflow batch, and it merges on the captain's word.
   After the first Rust-touching PR, download `gate-shadow-<run>-<attempt>` and confirm the `cargo` log shows exit 10 ("cannot run here"). Then keep those heads out of the 99% count.
