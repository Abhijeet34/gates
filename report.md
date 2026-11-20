# gates-issue-21: repoint permissions-diff's factory globs (sr2-7, glob half)

## Could not do

- **Read factory's tracked file list.** This session holds one clone (`gates`); its GitHub access is scoped to `Abhijeet34/gates` alone, so `factory` could be neither cloned nor searched. Every factory-specific glob's new path therefore stays unverified, and no glob was changed: a guessed path would land a SURFACE entry that protects nothing while looking repointed, which is the failure sr2-7 is about.
- Nothing else was changed on the branch; this report and `.fac/result.json` are the delivery.

## What exists on `main` (a8185d6)

**fix-sr2-7 has merged** as 5fdaf78 "test(permissions-diff): check repository-specific surface globs (#80)". The suite is `.ci/test-surface-paths.py`, run by `.ci/check.sh:104`.

It already does the "go red" half, except for factory:

- `problems()` fails `"{g} matches no tracked file of {repo}"` for every repository-specific glob with no match (`.ci/test-surface-paths.py`, `problems`), proven by the fixed case `moved/*.go`.
- Other repositories' file lists come from `.ci/fixtures/surface-paths/<repo>.txt` (`git ls-files` output). That directory does not exist yet.
- `factory` sits in `AWAITING`, so its globs are skipped with a note. A fixture for a repo still in `AWAITING` fails, so the fixture and the `AWAITING` removal must land together.

```
$ python3 .ci/test-surface-paths.py; echo rc=$?
test-surface-paths: factory not checked: its file list is not readable from a gates checkout; the fixture and the globs that follow its move into internal/domain and internal/platform land together
test-surface-paths: every checked repository-specific glob matches a tracked file
rc=0
```

The factory-specific SURFACE entries (`run/permissions-diff.py`, the `SURFACE` list, lines ~453-503), all tagged `"factory"` in `REPOSITORY`:

| Glob today | Grants | How read |
| --- | --- | --- |
| `internal/sandbox/profile.go` | worker sandbox profile | any |
| `internal/sandbox/*.go` | what the sandbox grants | `source` (tests aside) |
| `internal/permit/permit.go` | fac permit deny overlay | any |
| `route.toml` | routing table | `tables("tiers","repos","vendors")` |
| `internal/adapter/claude/claude.go` | boxed run's proxy hosts | `declarations("Egress")` |
| `repos/depbase.go` | base build's proxy hosts | `declarations("Registries")` |
| `egress/*.go` | egress proxy | `source` |
| `outbound/outbound.go` | outbound pattern set | any |

The only hint in this repository of the new layout is `README.md:300` (owner-verify example: `verified = ["internal/domain/permit/"]`) and its fixture in `.ci/test-owner-verify.py:56,434` (`internal/domain/permit/permit.go`). That suggests `internal/permit/permit.go` became `internal/domain/permit/permit.go`, but it is an example, not a file list, so it is not evidence enough. The `corpus/seats` cases use the old `internal/permit`, `internal/sandbox` and `internal/outbound` layout of a synthetic `example.com/control` module and say nothing about factory.

## Recommended change (for a worker with both clones)

1. In factory at the commit its `.github/gates.lock` moves to, run `git ls-files > gates/.ci/fixtures/surface-paths/factory.txt` (lines starting `#` are ignored, so a header naming the factory commit and date fits).
2. For each glob above, find its file in that list (likely under `internal/domain/` or `internal/platform/`; `route.toml` may not have moved) and replace the glob in `run/permissions-diff.py` SURFACE **and** the key in `REPOSITORY` in `.ci/test-surface-paths.py`. Keep each entry's `how` reader: `declarations("Egress")` and `declarations("Registries")` still need the declaration to be in the named file.
3. Remove `factory` from `AWAITING`.
4. Update `README.md` "The permissions-diff gate" table (lines 172-185) and the "Where 7.2 no longer matches" bullets (191-198) that name the old paths.
5. Run `python3 .ci/test-surface-paths.py` (must print no `not checked` note and exit 0), then plant a wrong path and confirm it prints `FAIL ... matches no tracked file of factory`, then `.ci/check.sh`.
6. Note the AGENTS.md rule: `run/permissions-diff.py` is in its own SURFACE, so the gate refuses this change. Once a caller has promoted the gate out of shadow, this is a class-2 change and needs a captain decision; it is not an automatic change.

## Follow-up worth recording

`AGENTS.md` says each fixture is refreshed with the gates pin. Nothing enforces freshness: a stale `factory.txt` would keep a moved glob green. A check comparing the fixture's header commit against factory's lock (or the bump in `bin/gates.py` regenerating it) would close that.
