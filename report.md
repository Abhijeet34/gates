# Re-verify: AGENTS.md paths flagged by the memory check

## What I did

Checked out `main` at `b1067fcf4b3dcbf5d13d989729893f4b8c4603a7` (the recon commit; `AGENTS.md` sha256 `dcf7fd71…`), read the four flagged lines, and looked up each path in the code and READMEs that own it.
I could not read `docs/memory-check.md`: it is not in this repository (`ls: cannot access 'docs/memory-check.md'`).

## Finding: all four paths are correct, so nothing should change

Each flagged path names a file in a **caller** repository, not in this one. `AGENTS.md` already says so on each line, and the code that reads or writes the file uses exactly that path.

| AGENTS.md line | Path | How the line already marks it | Code/doc evidence for the path |
|---|---|---|---|
| 11 | `.github/gates.lock` | "the caller's `.github/gates.lock` (absent here)" | `run/lock.sh:13` `LOCK=.github/gates.lock`; `README.md:75`, `README.md:79` |
| 17 | `.githooks/commit-msg` | "`system-maintenance/git-hooks/commit-msg` (synced as `.githooks/commit-msg`)" | `.ci/gitleaks/sync.sh:71` installs `$COMMIT_MSG:.githooks/commit-msg` in the caller; `run/lock.sh:17` `BUMP_PATHS=... .githooks/commit-msg`; `README.md:9` |
| 19 | `.githooks/pre-push.local` | "a caller extends the hook through … `.githooks/pre-push.local` (absent here)" | `system-maintenance/git-hooks/README.md:26-36` (the contract), `.githooks/pre-push` runs it; `README.md:9` |
| 36 | `.github/gates.lock` | "move a caller's `.github/gates.lock` (absent here)" | `bin/gates.py:26` `LOCK = ".github/gates.lock"` |

Here, `.githooks/` holds only `pre-push` and `.github/` has no `gates.lock`. That matches what the lines say: these files are absent here by design.
The source that line 17 names, `system-maintenance/git-hooks/commit-msg`, does exist.

## Recommendation

Make no edit. Changing these paths would break the pointers to real caller-side contracts, and deleting the lines would lose sharp-edge knowledge that still applies.
The false positives come from the checker: it treats every backticked path as a path in this repository.
Follow-up for fac's memory check: skip a backticked path when its own line marks it as absent or caller-side, for example `(absent here)`, "a caller's", or "synced as".
