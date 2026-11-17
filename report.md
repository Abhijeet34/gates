# Re-verify: gates AGENTS.md stale paths (task memory-path-gates-agents-md-5)

## Could not do

- `docs/memory-check.md`, cited by the goal as the checker's documentation, is not in this repository (`ls docs` → `No such file or directory`). It presumably lives in the fac repository, so I could not check how the checker treats the qualifiers described below.

## What I did

Checked out `main` at `cc7e3c44cd125c2335b835a24791b4dab63db650` and, for each flagged path, checked three things: whether the repository tracks it, what the AGENTS.md line says about it, and whether live code still refers to it.

```
$ git ls-files .githooks .github | grep -E 'gates.lock|commit-msg|pre-push.local'
(no output, rc=1)
```

So none of the four paths exists in this repository. That is expected. Every one of them is a **caller-side** path: a file that a repository *using* gates holds, not gates itself. AGENTS.md already says so on each line.

## Findings

| AGENTS.md line | Path | How the line describes it | Live code that uses the path |
|---|---|---|---|
| 11 | `.github/gates.lock` | "the caller's `.github/gates.lock` (absent here)" | `run/lock.sh:13` `LOCK=.github/gates.lock`; `run/lock.sh:17` `BUMP_PATHS=".github/gates.lock …"`; README.md, "Pinning"/"Bumping"; the `shared-*.yml` bootstraps |
| 17 | `.githooks/commit-msg` | "`system-maintenance/git-hooks/commit-msg` (synced as `.githooks/commit-msg`)" | `.ci/gitleaks/sync.sh:71` writes `$COMMIT_MSG:.githooks/commit-msg` into callers; `run/lock.sh:17`; README.md:9. The source file `system-maintenance/git-hooks/commit-msg` exists. |
| 19 | `.githooks/pre-push.local` | "A caller extends the hook through a tracked, executable `.githooks/pre-push.local` (absent here)" | `.githooks/pre-push:187` `LOCAL_HOOK=.githooks/pre-push.local`; `run/lock.sh:22`; `system-maintenance/git-hooks/README.md:26-36` |
| 36 | `.github/gates.lock` | "move a caller's `.github/gates.lock` (absent here)" | same as line 11; `bin/gates.py bump`/`rollback` |

**None of the four is a stale path.** Each one names a file that gates creates or reads in a caller repository, and the shipped code still uses each one, so the paths are correct. The lines also already say that the file is absent here ("(absent here)", or "synced as" for the one that sync.sh writes out). These are false positives from the memory check: it treats every backticked path as one that should exist in this repository.

## Recommendation

- **Make no change to AGENTS.md.** If you "correct" these paths, they would point at the wrong file. If you remove the lines, you lose sharp-edge knowledge that the code still depends on: the lock, the byte-compared synced hook, and the extension contract.
- **Fix the checker, not the file.** The memory check (docs/memory-check.md in fac) should skip a backticked path when the same sentence marks it as absent: "(absent here)", "synced as", or "a caller's …". An alternative is a per-repository allowlist of caller-side paths: `.github/gates.lock`, `.githooks/commit-msg` and `.githooks/pre-push.local`.
- Optional, for the checker's convenience only: line 17 could add "(absent here)" after `.githooks/commit-msg`, so all four lines use one marker. I did not make this edit, because the task's deliverable is this report.
