# Verification of gates PR 92 at head 9c5e7b76

PR: https://github.com/Abhijeet34/gates/pull/92, "perf(git-hooks): run git lfs pre-push only in repositories that use LFS".
Head `9c5e7b769a97f015923f8ca074fb815ec2204dca`, merge base with `main` `d9da7d8e8d4d2fcf8760aeba10d80c7f0fa706b2`.
Environment: Linux container, git 2.43.0, git-lfs 3.4.1, checked out with `git worktree add --detach` at each commit.

**Verdict 92: PASS**

The change works as claimed, its test fails on the base for the stated reason and passes on the head, and all neighbour suites fail identically on base and head.
One behaviour change goes beyond "skip git-lfs where it is not used" and the reviewer should accept it knowingly: see A3 under step 3.

## 1. Red then green

### My own check, written from the claim before reading the diff

`data/verify-92/claim-check.sh <pre-push> [nolfs]` installs the given hook as `core.hooksPath` with no-op stubs for the other gates and a logging fake `git-lfs`, then does real `git push`es to a local bare remote.
On the first run I expected a refused push to exit 2; `git push` exits 1 whatever the hook returns, so I corrected that expectation (my mistake, not the PR's) and re-ran.

```
$ claim-check.sh pre-push.base
FAIL no-LFS repo: git-lfs not run (rc=0 want 0, lfs_ran=yes want no)
PASS LFS attributes: git-lfs run (rc=0 lfs_ran=yes)
PASS LFS objects: git-lfs run (rc=0 lfs_ran=yes)
summary: 2 pass, 1 fail                       rc=1
$ claim-check.sh pre-push.base nolfs
FAIL no-LFS repo pushes without git-lfs installed (rc=1 want 0, lfs_ran=no want no)
PASS LFS-attributes repo without git-lfs refuses (rc=1 lfs_ran=no)
summary: 1 pass, 1 fail                       rc=1
$ claim-check.sh pre-push.head
PASS no-LFS repo: git-lfs not run (rc=0 lfs_ran=no)
PASS LFS attributes: git-lfs run (rc=0 lfs_ran=yes)
PASS LFS objects: git-lfs run (rc=0 lfs_ran=yes)
summary: 3 pass, 0 fail                       rc=0
$ claim-check.sh pre-push.head nolfs
PASS no-LFS repo pushes without git-lfs installed (rc=0 lfs_ran=no)
PASS LFS-attributes repo without git-lfs refuses (rc=1 lfs_ran=no)
summary: 2 pass, 0 fail                       rc=0
```

The base refused the no-LFS push on a machine without git-lfs with "This repository is configured for Git LFS but 'git-lfs' was not found on your path."; the head lets it land and still refuses an LFS repository ("This repository uses Git LFS but 'git-lfs' was not found on your path.").

### The PR's named test, `system-maintenance/git-hooks/test-global-hooks.sh`

Run bare (no `GIT_CONFIG_*` override), as AGENTS.md requires.

| Run | rc | FAIL lines |
|---|---|---|
| base test, base hook | 1 | the 3 kill-tree lines below |
| head test, base hook (red) | 1 | `FAIL repo without LFS: git-lfs ran` + the 3 kill-tree lines |
| head test, head hook (green) | 1 | the 3 kill-tree lines only |

The kill-tree lines are `FAIL a scanner outlived the killed hook (pid.exposure-scan.py | pid.gitleaks-pre-push | pid.watermark-scan.py)`.
They fail identically on the base, come from section 10's kill test (untouched by this diff), and pass in CI, so they are this container, not the PR.
The red is exactly the reason the PR body gives.
The red was made by copying the head's `test-global-hooks.sh` into the base worktree; the copy was reverted afterwards.

CI on 9c5e7b76 (read-only check-runs query): `checks`, `shell`, `python`, `secret scan`, `gates proof`, `record approval` all `success`.

## 2. The end-user symptom

`data/verify-92/attack.sh <pre-push>` uses the **real** git-lfs 3.4.1 (behind a wrapper that only logs `pre-push` calls) and the real LFS filters, and does real `git push`es to a `file://` bare remote.

| Case | base | head |
|---|---|---|
| E2E: `git lfs track '*.bin'`, commit a 2 KiB `.bin`, push | rc=0, git-lfs ran, remote LFS objects=1 | rc=0, git-lfs ran, remote LFS objects=1 |
| E2E: plain repository, push | rc=0, git-lfs ran, 0.071 s | rc=0, **git-lfs not run**, 0.043 s |

The symptom (git-lfs run on every push) reproduces on the base and is gone on the head, and a real LFS push still uploads its object.
I could not reproduce the 0.9 s cost: against a `file://` remote on this Linux container git-lfs costs tens of milliseconds, so the size of the saving is the PR's macOS figure and is unverified here.

## 3. Attacks (inputs the PR did not mention)

Same `attack.sh`, real git-lfs, on both commits.

| # | Input | base | head | Judgement |
|---|---|---|---|---|
| A1 | `.gitattributes` only in `a/b/`, `.git/lfs` deleted | git-lfs ran, refused (missing object) | git-lfs ran, refused | correct: the `*/.gitattributes` pathspec reaches nested files |
| A2 | linked worktree; `lfs/objects` only in the main repo's common dir | ran | ran | correct: uses `--git-common-dir` |
| A3 | LFS pointer committed, then `.gitattributes` deleted in a later commit; no local `lfs/objects` | git-lfs ran, **push refused** ("Git LFS upload failed: (missing) p.bin") | git-lfs not run, **push lands**, 0 objects on remote | behaviour change, see below |
| A4 | `.gitattributes` holding only a comment `# *.bin filter=lfs` | ran | ran (false positive) | safe direction: costs time, loses nothing |
| A5 | delete-only push (`:lfsb`) in a repo with LFS attributes on that branch | ran | not run | correct: nothing to upload |
| A6 | push of an annotated tag whose commit names the filter | ran | ran | correct: `git grep` peels the tag |
| A7 | `filter=lfs` only inside an ordinary `x.txt` | ran | not run | correct: only attributes files count |
| A8 | hook run directly with an unknown sha on stdin | ran | not run, rc=0 | acceptable: git never sends a sha it lacks |
| A9 | hook run with `GIT_DIR=/nonexistent` (rev-parse fails) | ran, rc=2 | ran, rc=2 | fail-safe: an unreadable repo still runs git-lfs |

**A3.** The hook comment states this ceiling: "a push of pointer files with neither is not uploaded".
That is accurate, but the base did not upload them either: it **refused** the push because the objects were missing.
On the head the push now lands, and the remote holds pointers with no objects.
The trigger is narrow: LFS pointers in history, no `.gitattributes` naming the filter at any pushed tip, and a clone that never fetched or created an LFS object.
It does not block the verdict, but the comment and README understate it, and the reviewer should accept it as a trade.

Not tested: a git older than 2.31 (no `rev-parse --path-format`).
On such a git the `lfs/objects` check could silently miss, leaving only the attributes check.
This machine's git is 2.43, and the target machine uses Homebrew git.

## 4. Neighbours

`.ci/check.sh` suites, run in the base and head worktrees (`secrets` needed `qpdf`, which I installed with apt; `shellcheck` was installed the same way):

| Suite | base | head |
|---|---|---|
| `secrets` | FAIL: only the 3 kill-tree lines | FAIL: identical |
| `gates-proof` | FAIL: `artifact-scan: red input was not refused`; `ok global-pre-push` | identical (logs diff empty) |
| `shell` | FAIL: 70 FAIL lines (machine-identifier tests: hostname/home-path not found in this container) | the same 70 lines; the sorted diff differs only in commit hashes inside `ok` lines |
| `shellcheck -s sh pre-push` | rc=0 | rc=0 |

None of these failures is new on the head.
Every one is a test that derives this machine's identifiers or relies on process kill semantics, and CI is green on 9c5e7b76.

## 5. Scope

`git diff --stat d9da7d8 9c5e7b7` touches 3 files, 41 insertions and 11 deletions: `pre-push`, `test-global-hooks.sh` and `README.md`.
The two merge commits bring nothing else into the diff against the merge base.

Everything in the diff serves the title, with two extras the body does mention:
- The git-lfs presence check moves from the top of the hook to just before `git lfs pre-push`.
  On a machine without git-lfs, the four gates now run before the refusal, and a non-LFS repository is no longer refused.
- The refusal message is shortened: it drops the old "remove this hook by deleting the 'pre-push' file" advice.

Test section 10c gains an `lfs/objects` directory so it still observes git-lfs's PATH.
The README gains one sentence.

## Commands

```
git fetch origin pull/92/head:pr92
git merge-base origin/main pr92                     # d9da7d8
git worktree add --detach <wt-base> d9da7d8 ; git worktree add --detach <wt-head> 9c5e7b7
data/verify-92/claim-check.sh <pre-push.{base,head}> [nolfs]
bash system-maintenance/git-hooks/test-global-hooks.sh          # in each worktree, and head test on base hook
data/verify-92/attack.sh <pre-push.{base,head}>
./.ci/check.sh secrets gates-proof ; ./.ci/check.sh secrets ; ./.ci/check.sh shell   # in each worktree
shellcheck -s sh system-maintenance/git-hooks/pre-push
```

## Spend

About 25 tool calls and roughly 20 minutes of wall time.
Most of that was the `.ci/check.sh` suites, run twice each (base and head) in parallel.
GitHub was only read: one PR `get` and one `get_check_runs`.
No comment, approval, merge or ready-marking was made.
