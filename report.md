# gates-issue-5: secret scan's apt install of qpdf, ffmpeg and ImageMagick

Scout report. No change to CI is proposed in this branch; this file is the deliverable.

## Not done

- **No "after" job time.** This was a scout, so no changed workflow ran on a runner. The savings below are worked out from per-step timestamps of the current runs, not measured on a changed job.
- **No path-conditional or cached install was built or tested**, because the evidence below shows neither pays for its risk.

## Summary

1. **The premise is wrong: `secret scan` is not the critical job.** In every recent PR run, `shell` takes 6.6–8.7 min, while `secret scan` finishes in 2.7–3.0 min. `checks` waits on both. Making `secret scan` faster does not change when `checks` reports.
2. **The three writers cost about 12 s, not 31 s.** The install step takes 23–37 s. But the same step would still run `apt-get update` and install exiftool without the writers, and `shell` does exactly that in 11–22 s. The extra cost of qpdf, ffmpeg and ImageMagick is the difference: about 11–15 s of runner time per PR.
3. **Installing the writers only when certain files change would weaken a gate on purpose.** `.ci/check.sh` `need`s all three and fails without them. Its comment says why: "a suite that skipped a writer would certify what it never measured." `test-watermark-scan.py` runs qpdf, ffmpeg and ImageMagick on generated files on every run, whatever the PR touches.
4. **Recommendation: close the defect as not worth fixing, or re-scope it to the `shell` job.** If any change is wanted anyway, the only one I'd call safe is a small trim inside `run/apt-install.sh` (details below), worth about 2–3 s.

## What I did

- Read `.github/workflows/ci.yml` (jobs `secrets` lines 115–138, `shell`, `gates-proof`, `checks`), `.ci/check.sh` (`check_secrets`, lines 185–211), `run/apt-install.sh`, and every use of the writers in `system-maintenance/git-hooks/`.
- Pulled job and step timings for the most recent completed `pull_request` runs of `ci.yml` with `gh api repos/<owner>/gates/actions/runs/<id>/jobs`.
- Pulled the full `secret scan` job log for the 2026-11-14 19:57 run and broke the install step down by timestamp.

## Evidence

### 1. Job times: `shell` is the critical path

The first three runs below are green; the 2026-11-16 run is included because its `secret scan` passed. Runs are named by start time (UTC).

| Run started | `shell` | `secret scan` | ..of which: install step | ..of which: `check.sh secrets` | `gates proof` |
|---|---|---|---|---|---|
| 2026-11-14 19:57 | 397 s | 162 s | 37 s | 112 s | 51 s |
| 2026-11-14 16:04 | 425 s | 172 s | 25 s | 140 s | 66 s |
| 2026-11-14 13:10 | 522 s | 167 s | 23 s | 141 s | 53 s |
| 2026-11-16 13:18 (`shell` failed) | 443 s | 182 s | n/a | n/a | 66 s |

How the numbers were read: per-step `started_at`/`completed_at`. For example, the 2026-11-14 19:57 run:

```
secret scan  19:57:01 .. 19:59:43
  Install the watermark gate's inspector and writers = 19:57:11 .. 19:57:48
  Run .ci/check.sh secrets                           = 19:57:48 .. 19:59:40
shell        19:57:01 .. 20:03:38
  Run .ci/check.sh shell                             = 19:57:52 .. 20:03:36
```

The defect's "132 s critical job" is about the length of `check.sh secrets` (112–141 s), not of the job. The job is not critical in any of these runs.

### 2. What the writers add on top of exiftool

`shell` and `gates-proof` install only `libimage-exiftool-perl` through the same `run/apt-install.sh`. In `shell` that step ("Install the watermark gate's inspector") took 22 s, 14 s and 11 s in the 19:57, 16:04 and 13:10 runs of 2026-11-14. The `secret scan` install step took 37 s, 25 s and 23 s in the same runs. So the writers add about 15, 11 and 12 s.

Breakdown of the 37 s install in the 2026-11-14 19:57 run, from the job log:

```
19:57:11.28  run/apt-install.sh libimage-exiftool-perl qpdf ffmpeg imagemagick
19:57:20.81  Fetched 11.6 MB in 2s            <- apt-get update, ~9.5 s (needed with or without the writers)
19:57:21.84  92 newly installed / Need to get 67.9 MB of archives
19:57:30.76  Fetched 67.9 MB in 9s            <- download, ~9 s (almost all of it is the writers)
19:57:41.02  Setting up ffmpeg ...            <- unpack + configure, ~8 s
19:57:41.02  Processing triggers for libc-bin <- 4.2 s
19:57:45.27  man-db: Not building database; man-db/auto-update is not 'true'.
19:57:45.27  Processing triggers for hicolor-icon-theme  <- 1.6 s
19:57:46.84  needrestart output ("No services need to be restarted.") <- ~1.3 s to step end
19:57:48.16  Run .ci/check.sh secrets
```

### 3. Why the install cannot be skipped based on which files changed

- `.ci/check.sh:186-194`: `check_secrets` fails without the writers, by design:
  ```
  # The watermark gate's inspector and its three writers. `need`ed rather than
  # skipped: the deployed hook refuses without exiftool, and a suite that
  # skipped a writer would certify what it never measured.
  need qpdf ... need ffmpeg ... need_any 'brew install imagemagick' magick convert
  ```
- `system-maintenance/git-hooks/test-watermark-scan.py`, which `check_secrets` runs on every PR, uses the writers no matter what the PR touches. It calls `qpdf` at line 1118, decodes with `ffmpeg` at line 1846, looks up ImageMagick at line 1882, and generates mp3/wav/mkv files with `ffmpeg -f lavfi` at lines 2022–2076. `ci.yml:128-129` says this: "ffmpeg stays because test-watermark-scan.py generates and hashes mp3/wav/mkv with it."
- The writers are what `watermark-scan.py clean` calls (`watermark-scan.py:2131-2217`). A rule like "install only when PDF/image/media files changed" tests the wrong thing: the changes that can break the writers are edits to `watermark-scan.py` and its test, and Ubuntu updates to ffmpeg, qpdf or ImageMagick (the scanner keeps workarounds pinned to specific versions, e.g. `watermark-scan.py:925` for ffmpeg 6.1.1 vs 9.0.1). A path filter can't see an archive update, so that drift would show up later, on some unrelated PR.
- Every PR must run the full suite for another reason too: a release commit needs a green `checks` on the commit itself (`ci.yml:19-22`), and a filtered run on the PR that preceded it would have certified less.

### 4. Why caching the .debs isn't worth it

`run/apt-install.sh:73-75` already downloads into apt's cache first, then installs with `--no-download`. So caching `/var/cache/apt/archives` with `actions/cache` would skip only the ~9 s download:

- `apt-get update` still has to run to resolve packages (~9.5 s).
- Unpacking and triggers still run (~15 s).
- It adds a third-party action pinned by SHA, a cache restore for ~68 MB, and a cache key that has to change when Ubuntu publishes new versions.

That nets maybe 5–7 s on a job that isn't critical. It also adds an action and cache state that the workflow-policy and permissions-diff checks would then have to cover.

### 5. Process constraints that apply to any change

- Any edit to `ci.yml` is on `run/permissions-diff.py`'s SURFACE, but only for `permissions:` (`run/permissions-diff.py:464-465`). A step edit that leaves permissions alone isn't blocked there.
- `run/apt-install.sh` is under `[[exempt]]` in `.ci/gate-registry.toml:2178`. Its contract test is `.ci/test-apt-install.sh`, which runs it against a stub `apt-get`.

## Recommendation

1. **Close `gates-secret-scan-apt-c4` as not worth fixing.** Only about 12 s of the 31 s figure is caused by the writers, and the job isn't on the critical path, so PR latency gains nothing. Making the install conditional would go against the `need` contract in `.ci/check.sh`.
2. **If the backlog item exists to cut PR wall-clock time, re-target it at `shell`.** `.ci/check.sh shell` alone takes 5.7–8 min (the 2026-11-14 19:57 run: 19:57:52 → 20:03:36). That is what `checks` actually waits on.
3. **Optional and minimal, if a change is still wanted:** trim fixed overhead in `run/apt-install.sh`, which every job and the `shared-watermark-scan.yml` callers share.
   - Pass `-o Acquire::Languages=none` so update skips the `Translation-en` indexes.
   - Set `NEEDRESTART_SUSPEND=1` for the install so needrestart doesn't scan (about 1.3 s above).

   Expect about 2–3 s per apt step. It needs a matching assertion in `.ci/test-apt-install.sh`, and it reaches pinned callers only through a lock bump.
