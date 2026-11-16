# gates-issue-13: lock each pinned caller in its own PR, then route copy syncs through `bin/gates.py bump`

Scout report. Backlog id `factory-p1-g3-gates-lock`. Base: `main` at `1739f45`.

## Could not do, first

- **I could not open or inspect any caller pull request.** This session's GitHub access covers this repository only, so I could not read any caller's `.github/workflows/*.yml` pins, its synced copies, or whether a lock is already there.
  For the same reason I could not run `bin/gates.py bump`: it needs the builder App's token (`app-token builder --repo <r>`, `bin/rollout.toml:13`), and that exists only on the maintainer's machine.
  So the first half of the goal, the per-caller lock PRs, is described below as an operator procedure. Every step was checked against fixtures, but none was executed against a real caller.
- **I did not read the green `checks` status of the release tags from GitHub.** `run/lock.sh` checks it at bump time anyway (`run/lock.sh:66-80`) and refuses a release without it.

## Summary

1. **The "own PR" path for a first lock already exists. It is `bin/gates.py bump <release>`, and no new code is needed for it.**
   For a caller with no lock, `bump` uses the caller's bootstrap pin as the `from` commit (`bin/gates.py:366`). It writes `.github/gates.lock` and the release's copies in a single `createCommitOnBranch` commit on `gates-lock/<sha12>`, which passes `lock-bundled` by construction (`run/lock.sh:17,116-118`), and it runs as the bump identity `hw-bodhi[bot] 343227966` (`run/bump-identity:9`), which passes `lock-author`.
   The pin PRs could not carry the lock because they also changed workflow files, so `lock-bundled` refused them. A builder-token commit also cannot touch `.github/workflows/` (README.md:132).
2. **No test covers that first-lock path.** Every fixture caller in `.ci/test-bump.py` already has a lock at R1 (`.ci/test-bump.py:206-208`).
   I re-ran the canary part of that suite with the lock commit removed, and every check passed (details under Evidence, E3). The path works, but a regression test for it should be added.
3. **The second half of the goal is a real gap, and adding locks makes it dangerous.** `.ci/gitleaks/sync.sh` copies from the working tree of whichever gates clone runs it (`.ci/gitleaks/sync.sh:22-27,88-90`). It never reads the target's `.github/gates.lock`.
   On a locked caller, that has two effects:
   - `sync.sh --check` reports false DRIFT.
   - A plain `sync.sh` writes copies that `verify.sh` then rejects, because `verify.sh` compares against the *locked* commit. The caller goes red.
   I reproduced both (E4). The hazard begins the moment the first lock merges, and it fires on the next canonical-copy change, which `AGENTS.md:18` tells the operator to handle with exactly that `sync.sh` run.

## Evidence

### E1. Controls and the bump path (read)

- **Bundling:** `run/lock.sh:17` gives `BUMP_PATHS=".github/gates.lock .gitleaks.toml .githooks/pre-push .githooks/commit-msg"`, and `:116-118` refuses any other changed path ("changes .github/gates.lock together with other content").
- **Author:** `run/lock.sh:121-125` reads `run/bump-identity` from gates `main`, never from the PR.
- **Adding a lock counts as a lock change:** `run/lock.sh:111-114`, where `from=${base_lock:-$BOOTSTRAP_SHA}`. It passes only if the release descends from the caller's pin (`:127-129`).
- **`bump` for a caller with no lock:** `bin/gates.py:356` skips the "already at or past" branch when `c.lock` is `None`. Then `frm = c.lock or c.pin` (`:366`) and `propose(target, ...)` runs (`:373`).
  `propose` writes the lock alone first, judges it with the caller's bootstrap `run/lock.sh`, then runs the release's `sync.sh` token-less, judges again and runs `verify.sh` (`:236-263`). It writes to GitHub only after all of that.
- **Preconditions that `bump` enforces on each caller:**
  - All `shared-*.yml` `uses:` resolve to one commit (`bin/gates.py:153-169`). A mix of `@main` and `@<sha>` is refused.
  - The pin's tree has `run/lock.sh` (`:188-195`). Every tag v1-v8 has it (E2).
  - No synced hook is *added* by the commit, because GitHub would add it as `100644` (`:274-278`). A caller missing a hook needs one manual `sync.sh` push first (README.md:130).
  - The release descends from the caller's pin; otherwise `lock-downgrade` refuses it.

### E2. Releases (local, after fetching tags)

```
v1..v8: run/lock.sh=y run/bump-identity=y, all on main
v6 -> HEAD canonical copies: .gitleaks.toml | 16 ++++   (v1..v5 likewise)
v7 -> HEAD, v8 -> HEAD canonical copies: no difference
v8 = 7c07f96851a21041ad9f1cd0915f2c3220686ce4, 17 commits behind main
```

So a caller pinned at v6 or earlier gets a `.gitleaks.toml` change in its lock PR when the target release is v7 or later. A caller pinned at v7 or v8 gets a lock-only PR, plus copies only if its own copies are stale.

### E3. Test runs (this container, `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null`)

| Command | Result |
| --- | --- |
| `bash .ci/test-lock.sh` | rc=0, 41 ok, "every control refuses its red scenario, and each deleted guard lets its own through" |
| `python3 .ci/test-bump.py` | rc=0, 39 ok, after installing gitleaks with `run/install-gitleaks.sh`. Without gitleaks, 4 cases fail with `gitleaks: command not found`, which is an environment problem, not a code problem. |
| `bash .ci/gitleaks/test-sync.sh` | "all cases passed" |
| First-lock experiment: a throwaway copy of `test-bump.py` with the `lock gates at R1` commit removed from `caller()`, run as far as the canary checks, then deleted | rc=0. All 9 canary checks pass, including "the bump moves the lock and the copies the release changed, nothing else", "the hook keeps its executable mode" and "the bump pull request's own CI passes every lock control and the scan". The bases had no lock (`git show main:.github/gates.lock` exit 128). |

Note that `bash -e .ci/test-lock.sh` stops after 9 cases with rc=1. The suite is run without `-e` (`.ci/check.sh:88`), so that is not a defect.

### E4. The `sync.sh` hazard on a locked caller (reproduced)

The fixture caller was locked at v6 and carried v6's copies. I ran `sync.sh` from a clone at `main`, and ran `verify.sh` with `GATES_DIR` at v6, which is what the caller's CI does:

```
verify before sync rc=0
--- sync.sh --check from main:
DRIFT <caller>/.gitleaks.toml: differs from canonical        <- false: it matches its lock
rc=1
::error::.gitleaks.toml has DRIFTED from gates' canonical copy (.gitleaks.toml at b58ba0a). Re-run gates' .ci/gitleaks/sync.sh.
verify after sync rc=1                                         <- the documented remedy turns a green caller red
```

The remedy that `verify.sh` prints (`run/secret-scan/verify.sh:12,18`) and the instruction in `AGENTS.md:18` both send the operator to `sync.sh`, which is the wrong tool for a locked caller.

## Recommendation

### Step A: land first, in gates. Make `sync.sh` lock-aware

This should merge before any caller's first lock, or at the latest before the next change to a canonical copy.

1. In `.ci/gitleaks/sync.sh`, when `<target>/.github/gates.lock` names a commit, take each source from that commit (`git -C "$ROOT" show <lock>:<path>`) instead of `$ROOT`'s working tree. Do this both for writing and for `--check`.
   Refuse, and name `bin/gates.py bump`, when:
   - the commit is missing from `$ROOT`, or
   - the commit has no source for a copy. Old releases may lack `system-maintenance/git-hooks/commit-msg`.

   Parse the lock the way `run/lock.sh:32-39` does.
   With this change:
   - A manual `sync.sh` on a locked caller becomes a safe repair. It restores the locked copies, which is still the README.md:130 remedy for a missing executable hook, and it can never move copies past the lock.
   - The only way to advance copies becomes `bump`. `bump` already writes the lock before it runs the release's `sync.sh` from a gates checkout at that release (`bin/gates.py:238-254`), so the two agree.
2. Add `test-sync.sh` cases:
   - A target locked at an older fixture commit gets that commit's copies.
   - `--check` reports no drift for that target.
   - A lock that names an unknown commit is refused.
3. Make the `verify.sh` drift message say "move it with `bin/gates.py bump`, or `sync.sh` restores the locked copies" when `.github/gates.lock` exists.
4. Rewrite `AGENTS.md:18`: "Changing any of them reaches a locked caller only through a release and `bin/gates.py bump`; `sync.sh` writes a locked caller's copies at its lock. Re-run `sync.sh` only over a clone with no lock."

Also update README.md:65 and "Bumping".

5. Add a first-lock case to `.ci/test-bump.py`: a caller with no lock, pinned at R1, bumped to R2, plus one bumped to R1 itself. This is the experiment in E3 made permanent.
   Do not change the `gitleaks-sync` row in `.ci/gate-registry.toml`. Its surface is `Security` (`.ci/gate-registry.toml:271-273`), which `run/permissions-diff.py:337` protects. `sync.sh` and `verify.sh` themselves are not on `SURFACE`.

### Step B: on the maintainer's machine. One lock PR per caller

Do this after Step A is on `main` and released, so the copies the PRs carry come from a lock-aware `sync.sh`.

1. Cut the release:

   ```
   gh workflow run ci.yml --ref main
   gh release create vN --target <that commit>
   ```

   Run the second command once `checks` is green (README.md:84). Alternatively, use v8 if Step A is deferred. Either way, the release must be at or ahead of every caller's pin.
2. For each caller, check `.ci/gitleaks/sync.sh --check <clone>`. If a hook is missing or not executable, do a one-off `sync.sh` push first (README.md:130).
3. Run `bin/gates.py bump vN`. That opens one PR per caller in the canary stage, each holding only the lock and the copies. Re-run it daily: fleet PRs open 2 days after the canaries merge green, and products 5 days after that (`bin/rollout.toml`).
   Any `REFUSED:` line names its cause (mixed pins, pin newer than release, mode). Fix that cause and re-run; nothing is written for a refused caller.
4. Merge each PR on its own. The PR's CI runs `run/lock.sh` from the caller's pin and must report `gates locked at <vN>`.

After Step B, every later canonical-copy change goes: merge to gates, release, `bin/gates.py bump`. That is the "lock and copies move together" the backlog item (g2 2.4) asks for, and Step A makes it the only route that works.

## What I changed

Nothing outside this file. The experiment script was a throwaway and was deleted, and the gitleaks install happened only in this container.
