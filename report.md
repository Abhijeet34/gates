# gates-issue-21: repoint permissions-diff's factory globs (sr2-7, glob half)

## Could not do

- **Repoint the factory globs.** This needs factory's tracked file list after its move into `internal/domain` and `internal/platform`. This session has the `gates` clone only: its GitHub access covers `abhijeet34/gates` and no other repository, so `factory` could not be cloned or read. The one new factory path visible from `gates` is `internal/domain/permit/` (README.md:300, `.ci/test-owner-verify.py:56`, :434, from the owner-verify tier example). That suggests `internal/permit/permit.go` became `internal/domain/permit/permit.go`. It is one example in a test, so it is not evidence about the other seven globs. I did not guess any of them, because a wrong glob silently drops a file from the protected surface.
- **Add the factory fixture.** The suite reads `.ci/fixtures/surface-paths/factory.txt` (factory's `git ls-files` output). The same missing input blocks it.

## What exists already (fix-sr2-7 has merged)

- `.ci/test-surface-paths.py` exists and runs in `.ci/check.sh:104`.
- It already goes red when a repository-specific glob matches no tracked file: `problems()` emits `"<glob> matches no tracked file of <repo>"`, and its fixed case `moved/*.go` pins that. It also goes red on a glob with no owning repository, a stale `REPOSITORY` entry, a repository with neither a fixture nor an `AWAITING` entry, and a fixture for a repository still in `AWAITING`. So the "make the suite go red" half of the goal is done for every repository that has a fixture.
- `factory` sits in `AWAITING`, so its globs are not checked today:

```
$ bash -e -c 'python3 .ci/test-surface-paths.py'; echo rc=$?
test-surface-paths: factory not checked: its file list is not readable from a gates checkout; the fixture and the globs that follow its move into internal/domain and internal/platform land together
test-surface-paths: every checked repository-specific glob matches a tracked file
rc=0
```

## The factory globs to repoint (run/permissions-diff.py, SURFACE, lines 452-518)

| Line | Glob | What it protects | How a change is judged |
| --- | --- | --- | --- |
| 457 | `internal/sandbox/profile.go` | worker sandbox profile | any change |
| 458 | `internal/sandbox/*.go` | what the sandbox grants | `source` (`*_test.go` aside) |
| 459 | `internal/permit/permit.go` | fac permit deny overlay | any change |
| 460 | `route.toml` | routing table | `tables("tiers","repos","vendors")` |
| 494 | `internal/adapter/claude/claude.go` | boxed run's proxy hosts | `declarations("Egress")` |
| 499 | `repos/depbase.go` | base build's proxy hosts | `declarations("Registries")` |
| 503 | `egress/*.go` | egress proxy | `source` |
| 513 | `outbound/outbound.go` | outbound pattern set | any change |

The suite's `REPOSITORY` map (`.ci/test-surface-paths.py:45-53`) attributes the same eight globs to `factory`.

## Recommended change, for a worker with both clones

All of it goes in one commit, because the suite refuses a half-done version:

1. In factory, at the commit whose `.github/gates.lock` will move to the resulting gates commit, run `git ls-files > .ci/fixtures/surface-paths/factory.txt` in gates. Lines starting with `#` are skipped, so a `# factory <sha>` header line is allowed.
2. For each glob in the table, find the moved file in that list. Replace the glob in `SURFACE` and the key in `REPOSITORY`. Keep each judge (`declarations("Egress")`, `declarations("Registries")`, `tables(...)`, `source`): a move does not change what counts as a change. Also check that the `Egress` and `Registries` `var` declarations still live in the moved files.
3. Remove `factory` from `AWAITING`.
4. Update README.md's surface table (lines 172-185) and the "Where 7.2 no longer matches" bullets (191-192, 198) to the new paths. Consider also `corpus/seats` (`internal/permit/permit.go`, `internal/sandbox/profile.go` in `case.toml` `steer_at`). That is a self-contained example repository, not factory, so it can stay as it is.
5. Run `.ci/test-surface-paths.py` and `.ci/test-permissions-diff*` (whatever `.ci/check.sh` runs for the gate).
6. This edits `run/permissions-diff.py`, which is on its own `SURFACE` (line 517). The gate will therefore refuse the change, and it is a class-2 decision once a caller promotes the gate out of shadow (AGENTS.md).

Hazard: until this lands, a factory change to its moved sandbox, permit, egress or outbound files passes permissions-diff unrefused, because the old globs match nothing.
