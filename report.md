# Scout report: permissions-diff globs, glob coverage test, protected set (gates-issue-15-2)

Task: P3a-SR3F1 (`factory-p3a-sr3f1-permissions-paths`), findings sr3-1, sr3-2 and sr3-7.
Base: `main` at `cc7e3c4`. This is a scout report; no gate code was changed.

## What I could not do (read first)

1. **I could not read factory's tree.** This session's GitHub access is scoped to `Abhijeet34/gates`, so factory's `git ls-files` is unavailable, and I cannot name the exact new paths for sr3-1. The only factory path this repository confirms is `internal/domain/permit/permit.go` (in owner-verify's tier list, `README.md:299`, and `.ci/test-owner-verify.py:434`).
2. **I could not read the source report** (`data/factory-p3a-sr3-security-rereview/report.md`, section 6). It is in the private repository. I took the meaning of sr3-1, sr3-2 and sr3-7 from the backlog title and from the code. If section 6 says something more specific, it overrides my reading below.
3. **Any fix is a class-2 change.** `run/permissions-diff.py` is on its own surface (`run/permissions-diff.py:497`), and so is `.ci/gate-registry.toml`'s security rows. A change to either is refused once a caller promotes the gate out of shadow.

## Summary

| Finding | State on `main` | Remaining work |
| --- | --- | --- |
| sr3-1: globs follow factory's moved paths | **Not done.** All 8 factory-specific globs still name the old layout. PR #80's description says it moved them, but the merged commit `5fdaf78` changed no glob. | Needs factory's file list. |
| sr3-2: test that every declared glob matches a tracked file | **Partly done** by #80 (`.ci/test-surface-paths.py`). It skips factory, which is in `AWAITING` and has no fixture, and it skips every `**/` glob. | Add a factory fixture. Cover the `**/` globs. |
| sr3-7: protected set derived from protected rows | **Partly done** by #79: protected *files* now come from protected rows (`row_files`). Protected *rows* still come from a hand-kept id list (`SECURITY_ROWS`), and that list has already drifted. Two security rows are unprotected (shown below). | Derive the row set too. |

## Evidence

### sr3-1: the factory globs name paths that no longer exist

`run/permissions-diff.py:450-500` (SURFACE) still names `internal/sandbox/profile.go`, `internal/sandbox/*.go`, `internal/permit/permit.go`, `internal/adapter/claude/claude.go`, `repos/depbase.go`, `egress/*.go`, `outbound/outbound.go` and `route.toml`. `.ci/test-surface-paths.py:45-55` maps them to `factory`.

The test admits the gap and passes anyway:

```
$ python3 .ci/test-surface-paths.py
test-surface-paths: factory not checked: its file list is not readable from a gates checkout; the fixture and the globs that follow its move into internal/domain and internal/platform land together
test-surface-paths: every checked repository-specific glob matches a tracked file
```

PR #80's body says: "Point each factory-specific glob at the current path (the worker sandbox profile and environ, the permit overlay, the claude adapter's Egress list, the dependency base registries, the egress proxy and the outbound pattern set)". But `git show 5fdaf78 -- run/permissions-diff.py` is empty: the commit touched only the test, its README text and `.ci/check.sh`.

The test is wrong in a second way. `git log -S internal/domain` shows that `d9da7d8` (#110) already uses `internal/domain/permit/` as a live factory path. So the old `internal/permit/permit.go` glob protects nothing in factory today. The same is very likely true of the other seven globs.

The README is stale too. Its mapping table and its "Where 7.2 no longer matches" notes (`README.md:166-197`) name the old paths, and `.ci/test-permissions-diff.py:186-199` builds its fixtures on them.

### sr3-2: what the coverage test does not check

I counted each SURFACE glob's matches in this repository's `git ls-files`:

- **All 8 factory globs:** 0 matches. They are unchecked because factory is in `AWAITING`.
- **27 `**/` globs:** exempt by design (`test-surface-paths.py:91`). 26 of them match nothing here, for example `**/github-apps/roles.json`, `**/claude-code/config/hooks/**`, `**/captain.md` and `**/memory-write-guard.sh`. Several name one repository's layout (the private repository's `github-apps/`, `claude-code/config/`, `captain.md`). If that layout moves, these globs go stale as silently as the factory ones did.
- **GENERIC globs and gates' own globs:** all pass.
- **Fixture staleness:** nothing ties the fixture to the gates pin. The docstring says the fixture "is refreshed with the gates pin", but neither `bin/gates.py bump` nor any test checks this.

### sr3-7: the hand-kept row list misses two security rows

`protected(row)` (`run/permissions-diff.py:320`) is `surface in SECURITY_SURFACES or id in SECURITY_ROWS`. `SECURITY_ROWS` was written in #57. #61 then added two siblings of the protected `advisories-osv*` rows: `advisories-osv-undecided-v1` and `advisories-osv-unscanned`. Both run `run/tool-gates.py`, and neither was added to the list:

```
$ git log --format='%h %ad %s' --date=short -S'"licence-vendored"' -- run/permissions-diff.py
dbd86c8 2026-10-24 fix(run): make permissions-diff read workflows fail closed and protect the security rows by id (#57)
$ git log --format='%h %ad %s' --date=short -S'advisories-osv-unscanned' -- .ci/gate-registry.toml
8f84d5a 2026-10-25 fix(run): scan gitignored lockfiles and refuse unscanned package sources in the osv row (#61)
```

To reproduce: in a scratch clone, rewrite one row's `finding` in `.ci/gate-registry.toml`, commit, and run `python3 run/permissions-diff.py --base HEAD~1`:

```
== advisories-osv-unscanned
permissions-diff: 1 changed path(s), none on the protected surface
== advisories-osv-undecided-v1
permissions-diff: 1 changed path(s), none on the protected surface
== advisories-osv-undecided          (listed sibling, for contrast)
permissions-diff: .ci/gate-registry.toml changes the gate proof's security rows: row 'advisories-osv-undecided' changed (finding)
permissions-diff: refused: 1 change(s) to the protected surface; ...
== repo-pre-push
permissions-diff: 1 changed path(s), none on the protected surface
== checks-verdict
permissions-diff: 1 changed path(s), none on the protected surface
```

An automatic change can therefore weaken or delete the osv row that refuses unscanned package sources. `README.md:178` also lists the protected rows as "the tool ... rows", which implies both are covered.

**How much of the list can be derived.** Take the rule "a row is protected when its `command` is a file SURFACE reads whole". It reproduces 16 of the 19 `SECURITY_ROWS` ids. It also picks up the 2 missed osv rows, plus `gate-runner` and `gate-runner-scoped`, which run `run/gate.py`, already protected whole.

The 3 ids it does not reproduce are `commit-msg`, `exposure-scan` and `signature-check`. Their commands are under `system-maintenance/git-hooks/`, which is not on SURFACE.

Hook rows that neither rule protects: `repo-pre-push` (`.githooks/pre-push`, which runs gitleaks; README already calls `.githooks/**` "a candidate"), `global-pre-push`, `watermark-scan` and `artifact-scan`. `checks-verdict` is also unprotected; it holds AGENTS.md's "a skipped job satisfies a required status check" rule.

The suites pass on `main`: `test-permissions-diff.py` 113/113, `test-protected-row-files.py` 35/35, `test-surface-paths.py` passes with the factory note.

## Recommendation

Land these in one class-2 change, before P3a-M6 puts any of these rows in shadow:

1. **sr3-1:**
   - Take factory's `git ls-files` at the commit whose `.github/gates.lock` names the gates commit being locked, and commit it as `.ci/fixtures/surface-paths/factory.txt`.
   - Delete `AWAITING["factory"]`.
   - Rewrite the 8 globs and their `REPOSITORY` keys to the new `internal/domain/...` and `internal/platform/...` paths, starting with `internal/domain/permit/permit.go`. PR #80's text also names a sandbox "environ" file, so check whether `internal/sandbox/*.go` needs to become two entries.
   - Re-check that `declarations("Egress")` and `declarations("Registries")` still name the right Go variables at their new homes. A renamed variable makes those readers pass every change silently.
   - Move `.ci/test-permissions-diff.py`'s fixtures and the README table and notes to the new paths in the same commit.
2. **sr3-2:**
   - Remove the blanket `**/` exemption. Give each `**/` glob an owning repository in `REPOSITORY`, or a deliberate "any caller" entry in `GENERIC`, so a glob that names one repository's layout is checked against that repository's fixture. This needs a fixture for the private repository too, which this public repository may hold only as a path list with no identifiers (see AGENTS.md's exposure rule).
   - Have `bin/gates.py bump` refuse when a caller's fixture is older than the lock it moves, or record the fixture's source commit in its header and check it against the lock.
3. **sr3-7:**
   - Replace `SECURITY_ROWS` with a derivation: `protected(row) = surface in SECURITY_SURFACES or command is read whole on SURFACE`.
   - Add `system-maintenance/git-hooks/{commit-msg,exposure-scan.py,signature-check.py}` and `.githooks/pre-push` to SURFACE, so the three hook ids, and `repo-pre-push`, derive too.
   - Decide `checks-verdict` explicitly.
   - Add a case to `.ci/test-protected-row-files.py`: a registry row whose command is protected must itself be protected. The two osv rows are its red input.
   - If a registry key is preferred to derivation, it would be `protected = true`. `run/gate.py` refuses unknown keys only in `.github/gates.toml`, not in `.ci/gate-registry.toml`, but `gates-proof.py` must be checked before adding one.
