# Scout report: shadow `weakening` row in each caller's `.github/gates.toml` (W6)

Task `gates-issue-11`, backlog id `gates-weakening-caller-shadow-w6`. Base: `main` at `1739f45`.

## What I could not do (first)

- **I did not edit any caller.** This session's GitHub access reaches `Abhijeet34/gates` only, so I could neither read nor write a caller's `.github/gates.toml`, its workflows or its test tree. The rows below therefore carry a placeholder `--tests` glob per caller; choosing each caller's security tests needs that caller's tree.
- **I did not read `data/gate-weakening-guard-w7/report.md`.** It lives in the private `automation` repository, outside this session's scope. What W6 asks is taken from the backlog title and from README.md, "The weakening guard", which already carries the intended row.
- Code search (`gh api search/code`) does reach the other repositories' default branches, path-only; I used it to list callers and did not open any of their files. The search index can lag the branches.

## What the row is, and what it needs

README.md:248-256 gives the row a caller adds:

```toml
[[gate]]
id = "weakening"
inputs = ["**"]
tools = { base = 'git merge-base "$BASE_SHA" HEAD' }
run = '''python3 "$GATES_DIR/run/weakening.py" --base "$BASE_SHA" --tests 'test/architecture/**' '''
shadow_since = 2026-10-26
```

What it needs from the runner is already supplied, so no gates change is a precondition:

- `GATES_DIR`: set by every bootstrap's lock step (`.github/workflows/shared-gate-shadow.yml:62`).
- `BASE_SHA`: exported to the rows by `run/gate-shadow.sh:18-20` from `SHADOW_BASE` (`pull_request.base.sha || github.event.before`, `shared-gate-shadow.yml:69`).
- `--repo`: defaults to `GITHUB_REPOSITORY`, which Actions sets; without it the script exits 2 (`weakening: could not decide: no --repo and GITHUB_REPOSITORY is unset`).
- The shadow job never gates: the script exits 0 and the step is `continue-on-error` (`shared-gate-shadow.yml:64-70`), so adding the row cannot turn a caller's required `checks` red.
- Adding a row is allowed by permissions-diff ("an existing row deleted or any of its fields changed; a new row passes", README.md table under "The permissions-diff gate"). Changing it **after** it lands is a class-2 decision, so it is worth getting the row right the first time (see the no-base finding below).

## Callers found (code search, 2026-11-16)

```
filename:gates.toml path:.github user:Abhijeet34
  automation, factory, fleet-records, foliot, foliot-bench-corpus, gates, halderworks-design, quoth
shared-gate-shadow path:.github/workflows user:Abhijeet34
  factory, fleet-records, foliot, foliot-bench-corpus, gates, halderworks-design
weakening.py filename:gates.toml user:Abhijeet34
  factory
```

| Repository | Has `.github/gates.toml` | Runs it via `shared-gate-shadow.yml` | Already names `weakening.py` |
| --- | --- | --- | --- |
| factory | yes | yes | yes (search hit; content not read) |
| fleet-records | yes | yes | no |
| foliot | yes | yes | no |
| foliot-bench-corpus | yes | yes | no |
| halderworks-design | yes | yes | no |
| gates | yes | yes (`.github/workflows/gate-shadow.yml`) | no |
| automation | yes | **no** | no |
| quoth | yes | **no** | no |

A row in `automation`'s or `quoth`'s registry would not run until that repository also calls `shared-gate-shadow.yml`. The other `bin/rollout.toml` repositories (firstmate, papertrace, the `*-axi` set, ops, halderworks-site, pointback, no-mistakes) have no registry yet.

## Evidence: the row run on a fixture caller

A throwaway caller (`git init`, `GIT_CONFIG_GLOBAL=/dev/null`) with the README row, `--tests 'test/security/**'`, run through the real `run/gate-shadow.sh` with `GATES_DIR=/home/user/gates` and `GITHUB_REPOSITORY=Abhijeet34/caller`. No sandbox tool is on this host, so rows ran unconfined; that changes caching only, never the verdict (README.md, "The verdict cache").

```
--- clean change
pass weakening
verdict=clear
weakening: no weakening
--- skip added to a security test
shadow weakening: fail: exit 1 (in shadow since 2026-11-16: 1 of 50 runs, 0 of 14 days)
verdict=shadow-red
weakening: test-exemption at test/security/test_a.py:4: @pytest.mark.skip
weakening: refused: 1 weakening(s) match no recorded policy in policy/relaxations.toml
--- no base (dispatch)
shadow weakening: error: weakening: tool probe 'base' exited 128 (in shadow since 2026-11-16: 0 of 50 runs, 0 of 14 days)
verdict=shadow-red
```

`python3 .ci/test-weakening.py` on `main`: `test-weakening: 43 of 43 cases passed`.

## Findings

1. **The README row goes red on every run with no base.** `workflow_dispatch` (and any event where `SHADOW_BASE` is empty) passes `BASE_SHA=0000…0` (`run/gate-shadow.sh:17`). The probe `git merge-base "$BASE_SHA" HEAD` exits 128, `run/gate.py:270-273` raises, and the row records `error` (`run/gate.py:415-422`), so the shadow verdict is `shadow-red` on a change that weakened nothing. An `error` is not a fresh record (`run/gate.py:372`), so it does not move promotion, but it is noise in the comparison.
2. **Guarding only the probe makes it worse.** With the probe made to print `none` for the all-zero base, `run/weakening.py:424-425` compares against the empty tree, so every skip or `continue` already in the `--tests` files is reported. On the fixture:
   ```
   shadow weakening: fail: exit 1 (in shadow since 2026-11-16: 1 of 50 runs, 0 of 14 days)
   weakening: test-exemption at test/security/test_a.py:3: if i: continue
   ```
   That is a fresh `fail` record, counted toward the 50 runs, on a run that changed nothing: a false alarm that would be upheld and restart the row's window (`run/gate.py` docstring, "Promotion").
3. **`continue` in an ordinary loop is a `test-exemption`** (`run/weakening.py:47-50`; the README lists it). A `--tests` glob over test files that loop with `continue` (common in Go and Python table tests) will go red in shadow when someone adds such a loop. That is by design, but it argues for narrow globs that name the security tests only, as W6 says, not `**/*_test.go`.
4. **A row exit of 2 (undecided) folds as `fail`, not `error`** (`run/gate.py:447`), so a `weakening: could not decide` in a caller (a bad policy file at the locked gates commit, a missing `GITHUB_REPOSITORY`) also counts as a fresh `fail` toward promotion.
5. README.md, "The permissions-diff gate", still says "`BASE_SHA` and `GATES_DIR` come from the calling workflow, which does not exist yet"; `shared-gate-shadow.yml` and `run/gate-shadow.sh` now supply both. Stale sentence, not a blocker.

## Recommendation

Use a row that skips cleanly when there is no base, in every caller, instead of the README's row as written:

```toml
[[gate]]
id = "weakening"
inputs = ["**"]
# The verdict depends on the base, which the runner neither keys nor passes to
# `run`, so the merge base enters the key through a probe. With no base (a
# dispatch) there is no change to judge, so the probe and the row say so.
tools = { base = 'case "$BASE_SHA" in 0000000000000000000000000000000000000000) echo none ;; *) git merge-base "$BASE_SHA" HEAD ;; esac' }
run = '''case "$BASE_SHA" in 0000000000000000000000000000000000000000) echo "weakening: no base, nothing to compare" ;; *) python3 "$GATES_DIR/run/weakening.py" --base "$BASE_SHA" --tests '<SECURITY TESTS GLOB>' ;; esac'''
shadow_since = <date it lands>
```

The no-base run then records a `pass` that judged nothing, which slightly flatters the 50-run count. If that is unacceptable, the alternative is a gates change rather than a caller row: have `run/weakening.py` print "no base" and exit 0 for `NO_BASE`, or have `run/gate.py` skip diff-based rows without a base. Either touches the permissions-diff surface (`run/weakening.py`, `run/gate.py`), so it is a class-2 decision; it would also be the cleaner fix for permissions-diff's identical probe. Update README.md:248-256 to whichever form is chosen, so the documented row is the one callers copy.

Per caller, in this order:

1. **fleet-records, foliot, foliot-bench-corpus, halderworks-design** (registry and shadow job present): add the row with `--tests` naming that repository's security tests. Pass one `--tests` per glob; omit `--tests` entirely if a repository has no security tests, which still judges workflows, rulesets and `uses:` refs.
2. **factory**: already names `weakening.py`; check its row for finding 1 and align it with the chosen form.
3. **gates** itself: add the row to `.github/gates.toml`. Its security tests are under `.ci/` (for example `.ci/test-workflow-policy.sh`, `.ci/test-permissions-diff.py`, `.ci/test-weakening.py`); name them individually rather than `.ci/**`, which would catch every `continue` in the fixtures. Note gates' `.github/gates.toml` is under `.github/`, so a change to it selects every row.
4. **automation, quoth**: add the row together with a `gate-shadow.yml` that calls `Abhijeet34/gates/.github/workflows/shared-gate-shadow.yml@main` (covered by the `first-party-shared-at-main` record in `policy/relaxations.toml`), or the row never runs.

Each caller's change is a single new row, so it passes that caller's permissions-diff and needs no relaxation record. Promotion is automatic once a row has 14 days and 50 fresh records in shadow with no upheld false alarm.
