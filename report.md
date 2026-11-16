# gates-issue-2: provenance-scrutiny-items-i7 — blocked, nothing implemented

## Blocker (read first)

The task asks for "the seven adjustments in `data/provenance-design-scrutiny-s4/report.md`" to be made to `system-maintenance/git-hooks/watermark-scan.py`.
That report does not exist in this repository, and this session cannot read the repository that holds it.

- `ls data` at the repository root: `No such file or directory`.
- A search of the whole tree for `scrutiny`, `provenance-design`, `s4` and `i7` finds nothing that lists the adjustments. The only design-record id in the gate's files is `provenance-gate-audit-v7-decision-generic-ai-marker-posture` (`system-maintenance/git-hooks/watermark-scan.py:120`, `system-maintenance/git-hooks/README.md:99`), which is an earlier audit, not this report.
- The `data/` path is in the private source repository (AGENTS.md: "The private `automation` repository holds the history and the dated records these files cite"). This session's GitHub access covers this repository only, so it cannot fetch it.
- The fac task name `gates-issue-2` does not match this repository's issue/PR #2, which is an unrelated closed pull request about the no-mistakes marker check. It holds no list of adjustments.

Without the seven items, any change to the scanner would be a guess. The scanner is a gate deployed machine-wide and run in CI for every caller, so I made no code change.

## What I checked, so the next run starts further along

Branch base: `main` at `1739f45`.

- The gate is `system-maintenance/git-hooks/watermark-scan.py` (2685 lines). Its suite is `system-maintenance/git-hooks/test-watermark-scan.py` (2192 lines). README.md in that directory, "The no-watermarking gate" through "Which formats `clean` can write" (about lines 69–200), owns the design.
- It is registered twice in `.ci/gate-registry.toml`: `id = "watermark-scan"` (line 377, the scanner and its suite) and `id = "watermark-scan-workflow"` (line 128, `run/watermark-scan.sh` and the shared workflow). A change that adds a rule or a finding id needs the `red`/`clean` inputs of those rows to still prove it (`.ci/check.sh gates-proof`).
- The scanner is a `PAYLOAD` file for `install.sh`. Merging a change does not put it in service on the maintainer's machine until `install.sh deploy` runs, then `install.sh check` (AGENTS.md).
- Posture switches already in place that an adjustment might touch:
  - `GENERIC_AI_MARKER_UNCLEARABLE = False` (`watermark-scan.py:124`). README.md:99 says this one constant is the whole change for the generic-AI-marker decision.
  - `UNCLEARABLE` (`watermark-scan.py:126`): `binary.watermark-assertion` and `binary.pixel-watermark-known`.
  - The advisory `binary.tool-tag` tier, which is `NOTED` and never refuses (README "A tool tag is examined and NOTED").
  - `ATTRIBUTIVE_FIELDS`, which detection and `clean` both read (README "Attributive provenance survives the strip").
  - `clean_verdict`, the only place a clean verdict is produced (README "inspect, clean, verify").

## Something else I found: this container cannot run the gate's suite

```
$ for t in exiftool ffmpeg qpdf magick; do command -v $t || echo "$t missing"; done
exiftool missing
/usr/bin/ffmpeg
qpdf missing
magick missing

$ GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null python3 system-maintenance/git-hooks/test-watermark-scan.py
...
FileNotFoundError: [Errno 2] No such file or directory: 'exiftool'
```

README.md:499 lists exiftool, ffmpeg, qpdf and magick as the suite's requirements. So a session in this environment could not verify a scanner change even with the report in hand, unless those are installed first (for example `apt-get install -y libimage-exiftool-perl qpdf imagemagick`, not attempted here).
A smaller point: when exiftool is missing the suite crashes with a Python traceback instead of a one-line "needs exiftool" refusal. The scanner's own refusal when exiftool is gone is tested; the harness's is not. It is harmless, but it reads like a test failure rather than a missing dependency.

## Recommendation

1. Re-queue the task with the seven adjustments written into the contract's `goal`. The text of the report's adjustment section is enough. Alternatively, mirror the report under a path this repository tracks, after checking it against the exposure rules in AGENTS.md (no private repository names, run ids or local paths).
2. Run it in an environment with exiftool, qpdf and ImageMagick installed, so that `test-watermark-scan.py`, `.ci/test-watermark-scan-workflow.sh` and `.ci/check.sh gates-proof` can be run before the push.
3. Ship each adjustment with a fixture in `test-watermark-scan.py` that is re-graded once neutralised, which is the suite's existing convention. Update README.md in the same commit wherever an adjustment changes a tier, a verdict or an exemption.
