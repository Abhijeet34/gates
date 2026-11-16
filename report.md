# gates-issue-15: permissions-diff paths, glob test, derived protected set (sr3-1, sr3-2, sr3-7)

Scout report on `main` at `1739f4557dad8e59583bda236eb3b380b25a97ea`. No change is proposed on this branch beyond this file.

## What I could not do (read first)

- **I could not read factory's file list.** This session can reach only the `gates` repository, and the source report (`data/factory-p3a-sr3-security-rereview/report.md`, section 6) lives in a private repository I cannot open. So I can't give a verified old-to-new mapping for factory's moved paths (sr3-1). The one moved path I can confirm from inside gates is `internal/domain/permit/` (see F1). The other factory globs are listed below as **unverified**.
- Because of that, I didn't write the factory fixture `.ci/fixtures/surface-paths/factory.txt`. `.ci/test-surface-paths.py` is built so that the fixture and the moved globs land together.

## What already exists (so the backlog item is partly done)

| Finding | State on `main` | Evidence |
| --- | --- | --- |
| sr3-2: test that every declared glob matches a tracked file | **Exists, but checks only gates' globs.** Factory is parked in `AWAITING`, and `**/` and `GENERIC` globs are not checked at all | `.ci/test-surface-paths.py:71-77` (AWAITING), `:97` (the `specific` filter); commit `5fdaf78` (#80) |
| sr3-7: protected set derived from protected rows | **Half done.** The *files* a protected row runs are derived (`row_files`), but which *rows* are protected is still a hand-kept id list, `SECURITY_ROWS` | `run/permissions-diff.py:331-361` (`SECURITY_SURFACES`, `SECURITY_ROWS`, `protected`), `:367-389` (`row_files`); commit `b04aaf5` (#79) |
| sr3-1: globs follow factory's moved paths | **Not done** | `run/permissions-diff.py:474-476, 485-497, 502` still name the old layout |

All three suites currently pass, and that is the problem: they pass while the factory globs match nothing.

```
$ python3 .ci/test-surface-paths.py
test-surface-paths: factory not checked: its file list is not readable from a gates checkout; the fixture and the globs that follow its move into internal/domain and internal/platform land together
test-surface-paths: every checked repository-specific glob matches a tracked file
$ python3 .ci/test-protected-row-files.py
test-protected-row-files: 35 of 35 protected row files refused
$ python3 .ci/test-permissions-diff.py
test-permissions-diff: 113 of 113 cases passed
```

## Findings

### F1 (sr3-1, confirmed): a change to factory's permit overlay at its new path passes the gate

gates itself records that factory's permit code now lives at `internal/domain/permit/`. The owner-verify tier list uses `verified = ["internal/domain/permit/"]` (`README.md:299`, `.ci/test-owner-verify.py:55`), and its fixture names `internal/domain/permit/permit.go` (`.ci/test-owner-verify.py:409`). `SURFACE` still protects `internal/permit/permit.go` (`run/permissions-diff.py:476`).

Reproduction in a throwaway repository holding both paths:

```
# edit internal/domain/permit/permit.go
$ python3 run/permissions-diff.py --base HEAD~1
permissions-diff: 1 changed path(s), none on the protected surface        rc=0
# edit internal/permit/permit.go
permissions-diff: internal/permit/permit.go changes the fac permit deny overlay
permissions-diff: refused: 1 change(s) to the protected surface; ...     rc=1
```

So once factory runs this row, its permit deny overlay can change without a refusal.

The other factory-specific `SURFACE` globs probably went stale in the same move, but I haven't verified them. `AWAITING` says the move went "into internal/domain and internal/platform":

| `SURFACE` glob | Line | Likely new home (unverified) |
| --- | --- | --- |
| `internal/sandbox/profile.go`, `internal/sandbox/*.go` | 474-475 | `internal/platform/sandbox/…` |
| `internal/permit/permit.go` | 476 | `internal/domain/permit/permit.go` (confirmed above) |
| `route.toml` | 477 | unknown; may be unmoved |
| `internal/adapter/claude/claude.go` (`Egress`) | 494-497 | `internal/platform/adapter/claude/…` ? |
| `repos/depbase.go` (`Registries`) | 498-502 | unknown |
| `egress/*.go` | 503 | unknown |
| `outbound/outbound.go` | 516 | unknown |

The README table (`README.md:170-186`) and the "Where 7.2 no longer matches" notes (`:190-191`) name the same old paths and need the same edit.

### F2 (sr3-2, confirmed): the glob test exempts every glob it can't check, and no `**/` glob is checked anywhere

- `REPOSITORY` maps all eight factory globs to `factory`, and `AWAITING["factory"]` turns them into a note, not a failure (`.ci/test-surface-paths.py:47-54, 71-77, 105-110`). The test is green with zero factory coverage. That is the exact state that let F1 through.
- `specific = [g for g in globs if not g.startswith("**/") and g not in GENERIC]` (`:97`) leaves out every `**/` glob: `**/github-apps/*`, `**/captain.md`, `**/memory-write-guard.sh`, `**/claude-code/config/**`, `**/.claude/**` and the rest. Many of these name factory or automation layout too. If one of them stops matching anything in the fleet, nothing notices.

### F3 (sr3-7, confirmed): two tool-gates rows that run a protected script are themselves unprotected, because protection is an id list

`SECURITY_ROWS` lists `advisories-osv`, `advisories-osv-undecided` and `advisories-osv-stale`, but it leaves out their siblings `advisories-osv-undecided-v1` and `advisories-osv-unscanned` (`.ci/gate-registry.toml:1003, 1027`). All five run `run/tool-gates.py`, and that file is itself on `SURFACE` (`run/permissions-diff.py:489`).

Reproduction: change only the `finding` of `advisories-osv-unscanned` in a worktree commit.

```
$ python3 run/permissions-diff.py --base HEAD~1
permissions-diff: 1 changed path(s), none on the protected surface        rc=0
```

Deleting the row passes the same way. That removes the gates-proof check that the `unscanned` refusal still fires.

I listed every unprotected row whose `gates` or `command` matches a `SURFACE` glob:

```
checks-verdict          ['.github/workflows/ci.yml']
guard-generated-files   ['.github/workflows/shared-guard-generated-files.yml']
instruction-path-truth  ['.github/workflows/shared-instruction-truth.yml']
instruction-size-limit  ['.github/workflows/shared-instruction-truth.yml']
gate-runner             ['run/gate.py']
gate-runner-scoped      ['run/gate.py']
advisories-osv-undecided-v1 ['run/tool-gates.py']
advisories-osv-unscanned    ['run/tool-gates.py']
fakes-smoke-workflow    ['.github/workflows/shared-fakes-smoke.yml']
gate-shadow             ['.github/workflows/shared-gate-shadow.yml', '.github/workflows/gate-shadow.yml']
```

- The two `advisories-osv-*` rows are plain omissions.
- `gate-runner` and `gate-runner-scoped` prove `run/gate.py`, which `SURFACE` protects as "the gate runner and the globs this gate matches with" (`:493`). Their rows can be edited freely, which looks like the same class of gap.
- The workflow matches are weaker. `SURFACE` reads a workflow only for its `permissions:` blocks, so it is a judgement call whether those rows are security rows.

Also unprotected, and arguably security controls: `gates-proof` ("Gate of the gates", `.ci/gates-proof.py`), `checks-verdict` (the required-check verdict) and the pre-push rows `repo-pre-push`, `global-pre-push` and `watermark-scan`. `README.md:193` already lists `.githooks/**` as a candidate.

## Recommendations

1. **sr3-1: move the factory globs, with a fixture, in one gates pull request.** First take factory's `git ls-files` at its current locked commit into `.ci/fixtures/surface-paths/factory.txt` (the format is in `.ci/test-surface-paths.py:10-14`). Then rewrite each factory glob in `SURFACE` (and its `REPOSITORY` key, README table and 7.2 notes) to a path that file contains. Finally drop `factory` from `AWAITING`. This is a self-change to `run/permissions-diff.py`'s `SURFACE`, so it is itself class-2 by the gate's own rule. While the old layout may still be live in some lock, keep both old and new globs, then drop the old ones in a later change.
2. **sr3-2: make `AWAITING` fail, and check `**/` globs.**
   - Turn an `AWAITING` entry into a failure, or give it an expiry date, so a repository can't stay unchecked indefinitely.
   - Check each `**/` glob against the union of the fleet fixtures (gates' `ls-files` plus every `.ci/fixtures/surface-paths/*.txt`). A glob that matches no file in any of them should fail, or be listed in an explicit "deliberately speculative" set with a reason (for example `**/managed-settings.d/**`, which protects a file a caller *could* add).
3. **sr3-7: derive which rows are protected, instead of listing ids.**
   - Make `protected(row)` true when the surface is in `SECURITY_SURFACES`, **or** any `gates` or `command` path of the row matches a `SURFACE` entry read whole (`how is None`). That alone covers both `advisories-osv-*` rows and both `gate-runner*` rows, and later rows that run a protected script are covered automatically.
   - Keep `SECURITY_ROWS` only for rows with no such file, and add a test that fails when an id in it is redundant or missing from the registry.
   - Add the two F3 reproductions as cases in `.ci/test-permissions-diff.py`. Extend `.ci/test-protected-row-files.py` to also edit each protected row (not only its files) and expect a refusal.
   - Decide, as a class-2 call, whether `gates-proof`, `checks-verdict` and the pre-push rows join the protected set.
4. Order: land 3 before any P3a-M6 row enters shadow (it only touches gates). Land 1 and 2 together once someone with factory access supplies the fixture.

## Commands run

- `python3 .ci/test-surface-paths.py`, `.ci/test-protected-row-files.py`, `.ci/test-permissions-diff.py`: all pass (output above).
- I loaded `run/permissions-diff.py` as a module and compared `SECURITY_ROWS`, `protected()` and `SURFACE` with `.ci/gate-registry.toml`. Every id in `SECURITY_ROWS` exists in the registry. The unprotected rows touching `SURFACE` are listed in F3.
- The F1 and F3 reproductions were throwaway repositories and worktrees run with `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null`, removed afterwards.
