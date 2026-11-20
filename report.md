# gates-issue-21: repoint permissions-diff's factory globs (finding sr2-7, glob half)

## Could not do / not done

- **No code change is delivered.** This task is a scout, so the change below was built and
  tested in a scratch tree and then reverted. It is given in full under "Recommended change"
  for an implementing task to apply.
- **Merging that change needs a class-2 decision.** `run/permissions-diff.py` is on its own
  SURFACE. Run against a commit holding the change, the gate refuses it:
  `permissions-diff: run/permissions-diff.py changes this gate's protected surface` /
  `refused: 1 change(s) to the protected surface; ... size the change L and open a class-2 decision` (exit 1).
- **The fixture form is a disclosure choice for the captain** (see Finding 4). I recommend the
  filtered form.

## What I did

1. Branched from `main` at `a8185d6`, the recon commit. fix-sr2-7 is merged: `.ci/test-surface-paths.py`
   arrived in `5fdaf78` (#80). It runs from `.ci/check.sh:104` in the `shell` suite. Today it prints
   `factory not checked: ... the fixture and the globs that follow its move into internal/domain and internal/platform land together`
   and exits 0.
2. Attached `Abhijeet34/factory` read-only and shallow-cloned it at
   `60638c0476bfddc13484d14b908599754d06ced5` (main, 2026-11-20). `git ls-files` lists 1721 paths.
3. Checked every factory-specific SURFACE glob against that list. Then I applied the repoint
   and a fixture, and ran the suites.

## Findings

### 1. All seven moved factory globs match nothing in factory today

`run/permissions-diff.py:452-519` is SURFACE. The `REPOSITORY` map at `.ci/test-surface-paths.py:47-55`
lists eight factory globs. I matched them against factory's `git ls-files` with `gate.glob_re`:

| SURFACE glob (today) | matches in factory | factory path now |
| --- | --- | --- |
| `internal/sandbox/profile.go` (l.457) | 0 | `internal/platform/sandbox/profile.go` |
| `internal/sandbox/*.go` (l.458) | 0 | `internal/platform/sandbox/*.go` (24 files) |
| `internal/permit/permit.go` (l.459) | 0 | `internal/domain/permit/permit.go` |
| `route.toml` (l.460) | 1 | unchanged |
| `internal/adapter/claude/claude.go` (l.494) | 0 | `internal/domain/adapter/claude/claude.go` |
| `repos/depbase.go` (l.499) | 0 | `internal/domain/repos/depbase.go` |
| `egress/*.go` (l.503) | 0 | `internal/platform/egress/*.go` (2 files) |
| `outbound/outbound.go` (l.513) | 0 | `internal/platform/outbound/outbound.go` |

None of the old directories exist in factory: `ls internal/sandbox internal/permit egress outbound repos internal/adapter`
answers `No such file or directory` for each. So the gate currently protects none of these
seven factory files.

The content readers still apply at the new paths:
- `var Egress = []string{"api.anthropic.com"}` is at `internal/domain/adapter/claude/claude.go:502`.
- `var Registries = map[string][]string{` is at `internal/domain/repos/depbase.go:32`.
- `route.toml` still has `[tiers]` (l.17), `[repos]` (l.23) and `[vendors]` (l.38).

### 2. Factory does not run permissions-diff today

- Factory's `.github/gates.toml` has only `weakening` and `review` rows.
- Factory has no `.github/gates.lock`.
- Factory's workflows pin gates by workflow file at `7c07f96851a21041ad9f1cd0915f2c3220686ce4`
  (`gate-shadow.yml:24`, `secret-scan.yml:29`, `watermark-scan.yml:21`).

So the stale globs cost nothing yet, and the repoint changes nothing in factory until factory both
adds the row and bumps its pin. The suite docstring says to refresh the fixture "at the commit whose
`.github/gates.lock` moves". Factory has no lock, so in practice the refresh happens at its
workflow-pin bump.

### 3. The repoint also moves fixture paths in `.ci/test-permissions-diff.py`

When I repointed SURFACE alone, `.ci/test-permissions-diff.py` failed 7 of 113 cases. One example:
`FAIL a permit rule granting Write on ~/.local/bin: wanted exit 1 naming 'permissions-diff: internal/permit/permit.go changes ...', got exit 0`.
The same failure hit the sandbox toolchain, Egress (3 cases), Registries and egress proxy cases.

Its fixture tree (l.186-199) and cases (l.299-301, 672-678, 856-913) use the old paths. Two cases
check that `*_test.go` files are excluded: l.678 for sandbox and l.913 for egress. With the old
paths both would pass vacuously, so they must move too.

After moving every one of those paths, the result is `test-permissions-diff: 113 of 113 cases passed`.

`.ci/gate-registry.toml`'s permissions-diff rows (l.493, l.516) use `.claude/settings.json` and
`.github/rulesets/`, so they are unaffected.

`README.md:172-198` ("The permissions-diff gate" table and its notes) names the old paths and
should follow the move.

### 4. The fixture: full file list or filtered

The suite reads `.ci/fixtures/surface-paths/factory.txt`, which is factory's `git ls-files`.

- **Full list:** 1721 paths of a private repository, committed to this public one. It includes
  layout detail such as `internal/domain/intake/testdata/golden/repos/<repo>/...` for other fleet
  repositories. That conflicts with the spirit of AGENTS.md: "tracked text names no private repository".
- **Filtered list:** `git ls-files` filtered to paths that match factory's SURFACE globs. It has
  31 lines and still does the job. A glob that stops matching after a move finds nothing in a list
  regenerated with the same filter, so the suite still goes red.

Both forms passed the suite.

### 5. Proof that the suite goes red on a stale glob

With the repoint, the fixture and `AWAITING = {}`:
```
$ python3 .ci/test-surface-paths.py
test-surface-paths: every checked repository-specific glob matches a tracked file      (exit 0)
```
With only the egress glob put back to `egress/*.go`, in both files:
```
$ python3 .ci/test-surface-paths.py
FAIL egress/*.go matches no tracked file of factory                                     (exit 1)
```
The suite's fixed cases already cover "fixture present but still in AWAITING" and "no fixture",
so leaving factory in `AWAITING` beside a fixture is also red.

`.ci/check.sh python` (ruff lint and format) passed on the prototype.

## Recommended change (one commit, class-2)

1. `run/permissions-diff.py` SURFACE: make these seven replacements and leave `route.toml` as is.
   - `internal/sandbox/profile.go` → `internal/platform/sandbox/profile.go`
   - `internal/sandbox/*.go` → `internal/platform/sandbox/*.go`
   - `internal/permit/permit.go` → `internal/domain/permit/permit.go`
   - `internal/adapter/claude/claude.go` → `internal/domain/adapter/claude/claude.go`
   - `repos/depbase.go` → `internal/domain/repos/depbase.go`
   - `egress/*.go` → `internal/platform/egress/*.go`
   - `outbound/outbound.go` → `internal/platform/outbound/outbound.go`
2. `.ci/test-surface-paths.py`:
   - Update the same seven keys in `REPOSITORY`.
   - Set `AWAITING = {}`.
   - If the filtered fixture is chosen, change the docstring to say the fixture is factory's
     `git ls-files` filtered to its SURFACE globs.
3. Add `.ci/fixtures/surface-paths/factory.txt`. Its header comment names the factory commit.
   The filtered form is built with:
   ```
   git -C factory ls-files | grep -E '^(internal/domain/(adapter/claude/claude|permit/permit|repos/depbase)\.go|internal/platform/(egress|sandbox)/[^/]*\.go|internal/platform/outbound/outbound\.go|route\.toml)$'
   ```
   It gives 31 lines at `60638c0`.
4. `.ci/test-permissions-diff.py`: apply the same path substitutions to the fixture tree and the
   cases. That covers `internal/permit/permit.go`, `internal/sandbox/`, `internal/adapter/claude/claude.go`,
   `repos/depbase.go`, `egress/egress.go` and `egress/egress_test.go`.
5. `README.md:172-198`: update the paths in the table and in the notes.

Verify with `.ci/check.sh shell python gates-proof` and then run `permissions-diff`, which is
expected to refuse (Finding: class-2).

## Side observation (not acted on)

`internal/platform/sandbox/helper/fac-probe.m` is an Objective-C source under the sandbox package.
`internal/platform/sandbox/*.go` does not match it, because `*` stays within one path segment and
it is not `.go`. I did not check whether it can widen what the sandbox grants.
