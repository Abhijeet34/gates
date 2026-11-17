# Scout report: `gates-secret-scan-apt-c4` — writers installed on every PR in the secret scan

Base: `main` at `cc7e3c44cd125c2335b835a24791b4dab63db650`. No code change is proposed on this branch; this file is the deliverable.

## What I could not do

- **No after measurement.** Any timing for a changed workflow needs a pull request run on GitHub, and this task opens none. The before numbers below come from the GitHub Actions API, using job and step timestamps from three recent `ci.yml` pull-request runs on `main`'s lineage.
- **No local install timing.** This container is Ubuntu 24.04, like the runner, but most of the packages were already installed here (`apt-get install -s` reports only 2 new packages and 884 kB). Local timing would therefore say nothing about a fresh runner.
- **I could not find where the "132 s critical job" figure comes from.** In every run I sampled, the secret scan job took about 180–195 s and was not the critical job (see below).

## Summary

The defect's premise does not hold on current `main`:

1. **The secret scan is not the critical path.** `shell` takes 6 m 35 s to 6 m 58 s and `secret scan` takes 3 m 00 s to 3 m 15 s, so `checks` waits on `shell`. Removing the writer install entirely would cut wall-clock time to merge by **0 s**. It would save only runner minutes.
2. **Installing the writers costs almost nothing extra.** Most of the step's time is the fixed `apt-get update` plus the exiftool install. In the same runs, the jobs that install only `libimage-exiftool-perl` (`shell`, `gates proof`) spent 19–27 s on that step. The secret scan step with all four packages spent 17–53 s. The median is about 25 s for both. Mirror speed varies more between runs than the three writers add.
3. **The install is load-bearing by design.** `.ci/check.sh` uses `need` for each writer and does not skip a missing one. Its own comment says why: "a suite that skipped a writer would certify what it never measured." Installing them only "when files of those types changed" does not fit this repository: the tests generate their PDF/TIFF/mp3/wav/mkv fixtures at run time and exercise `watermark-scan.py`'s writers. Whether a PR touches such files is irrelevant.

**Recommendation: close this as won't-fix / not a defect.** If CI time on gates is the goal, the lever is the `Run .ci/check.sh shell` step (about 5 m 50 s, see below), not this install.

## Evidence

### The step in question

`.github/workflows/ci.yml:115-124` (job `secrets`):

```yaml
      # exiftool inspects; qpdf, ffmpeg and ImageMagick are the writers `clean`
      # uses for the formats exiftool cannot rewrite. ffmpeg stays because
      # test-watermark-scan.py generates and hashes mp3/wav/mkv with it.
      # Without recommends the set is 215 packages / 137 MiB, not 340 / 229 MiB
      # (measured 2026-10-26). ...
      - name: Install the watermark gate's inspector and writers
        timeout-minutes: 9
        run: run/apt-install.sh libimage-exiftool-perl qpdf ffmpeg imagemagick
```

`.ci/check.sh:185-194`, `check_secrets()`:

```bash
    # The watermark gate's inspector and its three writers. `need`ed rather than
    # skipped: the deployed hook refuses without exiftool, and a suite that
    # skipped a writer would certify what it never measured.
    need exiftool ...
    need qpdf ...
    need ffmpeg ...
    need_any 'brew install imagemagick' magick convert || return 1
```

These are the tests that use the writers (found with `grep -rn -E "qpdf|ffmpeg|magick"`; the only hits are `ci.yml`, `check.sh`, the two READMEs, `watermark-scan.py` and its test):

- `system-maintenance/git-hooks/test-watermark-scan.py:1118` runs `qpdf` unconditionally to repair a broken-xref PDF.
- `test-watermark-scan.py:1882` builds a TIFF with `magick`/`convert`, and `:2022` generates mp3/wav/mkv fixtures with ffmpeg. Both are guarded by `shutil.which`, but they print `SKIP` when the tool is missing. `check.sh` exists to stop that skip from happening in CI.

### Timings (GitHub Actions API, `list_workflow_jobs`)

| Run (PR, ci.yml) | secret scan: install step | shell: exiftool-only install | gates proof: exiftool-only install | secret scan job | shell job (critical) |
|---|---|---|---|---|---|
| 44012540676 (2026-11-17 03:46) | 25 s (03:46:37→03:47:02) | 19 s | 20 s | 3 m 15 s | 6 m 35 s |
| 44009349292 (2026-11-17 03:19) | 17 s (03:19:16→03:19:33) | 27 s | 25 s | 3 m 04 s | 6 m 58 s |
| 43988715227 (2026-11-17 00:34) | 53 s (00:34:42→00:35:35) | 25 s | 25 s | 3 m 00 s | 6 m 57 s |

What dominates each job:

- `secret scan`: `Run .ci/check.sh secrets` takes 1 m 53 s to 2 m 45 s.
- `shell`: `Run .ci/check.sh shell` takes about 5 m 47 s to 5 m 57 s (for example 03:47:20→03:53:07). That is the job `checks` waits on.

In one run out of three, the four-package install took less time than the exiftool-only install on a sibling runner. The 53 s outlier is a slow mirror, the case `run/apt-install.sh` already bounds. The packages themselves did not cause it.

## The options weighed

| Option | Effect on merge latency | Cost / risk |
|---|---|---|
| Install writers only when certain paths change (`watermark-scan.py`, its test, `check.sh`) | 0 s (not critical path); saves ≈ 0–6 s runner time | A conditional on a job behind the required `checks` status; `check.sh secrets` would have to stop `need`ing the writers, reversing a deliberate rule; a change elsewhere that breaks the writers (e.g. `run/apt-install.sh`, a runner image bump) would go unmeasured. |
| Install writers only when files *of those types* change (the defect's wording) | 0 s | Wrong trigger: the fixtures are generated at run time, so no PR ever changes a PDF/TIFF/mp3 file. The writers would effectively never be tested. |
| Cache the .debs with `actions/cache` (SHA-pinned) | 0 s; at most a few seconds of runner time, since `apt-get update` (the fixed cost) still runs | Adds an action and a cache-poisoning surface to fleet-trusted infrastructure, and needs a `gate-registry.toml` / workflow-policy review. Not worth it for single-digit seconds. |
| Leave as is | — | — |

## Recommendation

- Close `gates-secret-scan-apt-c4` with this report as evidence. The install is not on the critical path, and the writers add no time that can be told apart from mirror variance.
- If the CI-time backlog still wants a gates item, re-target it at the `shell` job's `Run .ci/check.sh shell` step, about 5 m 50 s on every PR and the actual critical path. A scout would first need per-test timings inside that suite.
- If someone revisits this later, re-measure on a PR run that installs only `libimage-exiftool-perl` in `secrets` (with `check.sh` changed to match). That is the only clean before/after, and it must not merge, because it removes coverage.
