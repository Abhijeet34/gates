# Scout report: gates-issue-1 — advisories judge vs. package.json-manifest alerts

Backlog id `gates-advisories-manifest-sibling-a2` (defect, P1, recorded as "in flight").
Base: `main` at `1739f4557dad8e59583bda236eb3b380b25a97ea`.

## Could not do

- **Could not look at the callers (pointback PRs 138/139), their `.github/gates.lock`, or their workflow runs.** This session's GitHub access covers this repository only. So I cannot say which gates commit those PRs actually ran, or whether they are still blocked today.
- The local `/usr/bin/yq` is the Python jq-wrapper (`yq 0.0.0`), not mikefarah yq v4. With it, 30-odd unrelated cases in `.ci/test-dependency-advisories.sh` fail (for example `severity_floor is ""high""`, because the wrapper prints JSON-quoted strings). I downloaded mikefarah `yq v4.44.3` into a scratch directory and put it first on `PATH`. Every result below uses that binary.

## Summary

**The defect described in the goal is already fixed on `main`.** Commit `9fe6ca0` (#65, 2026-10-25), "fix(run): credit a package.json advisory fixed by its sibling lockfile in the compare", added the sibling-lockfile rule. It has tests, including a mutation test, and all of them pass. The backlog item is stale as written.

If pointback 138/139 are still blocked, the likely cause is the caller's pin, not the judge: their `.github/gates.lock` (or `uses:` SHA) may name a gates commit from before `9fe6ca0`. That could not be checked from here.

Two smaller gaps remain in the rule as merged, both confirmed below:
- npm/yarn/pnpm **workspaces**: a member `package.json` whose lockfile sits at the workspace root gets no credit.
- **bun** lockfiles are not on the sibling list.

## What the code does now

`run/dependency-advisories/judge.sh`:

- `:107-110` `touches_at($chg; $x; $type; $m)` matches a compare change by type, manifest, normalised name and GHSA.
- `:114-118` `siblings($x)`: for a manifest matching `(^|/)package\.json$`, it returns `package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock` and `pnpm-lock.yaml` in the **same directory**. Any other manifest gets `[]`.
- `:119-123` `fixed`: the same-manifest rule, **or** "some sibling removes a version carrying the GHSA, and no sibling adds one".
- `:144` every open alert is marked with `fixed: fixed($chg; .)`, and `failing` excludes fixed alerts (`:153`).

`README.md:64` documents the rule and its scope ("a lockfile in another directory, or any other manifest type, gets no such credit (measured 2026-10-25 on pointback PR 139)").

`.ci/test-dependency-advisories.sh:331-357` builds the PR-139 shape: a markdown-it `package.json` alert, with the fix listed under `package-lock.json` only. `:761-770` is a mutation test that removes the sibling rule and requires that shape to fail.

## Evidence

```
$ git log --oneline -S'def siblings' -- run/dependency-advisories/judge.sh
9fe6ca0 fix(run): credit a package.json advisory fixed by its sibling lockfile in the compare (#65)

$ PATH=<scratch>/yqbin:$PATH bash .ci/test-dependency-advisories.sh ; echo rc=$?
...
ok   a package.json alert is fixed by the sibling lockfile removing the version
ok     and names the package.json alert as fixed
ok   a sibling lockfile that still adds the affected version gives no credit
ok   a lockfile in another directory gives a package.json alert no credit
ok   the sibling rule does not reach a requirements.txt alert
ok   dropping the sibling rule fails the PR-139 shape
dependency-advisory gate: every property pinned
rc=0        (143 `ok` lines, 0 FAIL)
```

To check edge cases I ran `judge.sh` directly (`bash -e`, `EVENT_NAME=pull_request`, `SEVERITY_FLOOR=high`, no deferral file). Each run had one high markdown-it alert and a compare record that removes 15.0.2 (carrying the GHSA) and adds 15.0.3 (clean) at the lockfile shown:

| alert manifest | compare lists change under | exit | credited? |
| --- | --- | --- | --- |
| `package.json` | `package-lock.json` | 0 | yes |
| `docs/package.json` | `docs/package-lock.json` | 0 | yes |
| `docs/package.json` | `docs/pnpm-lock.yaml` | 0 | yes |
| `package-lock.json` | `package-lock.json` | 0 | yes (same-manifest rule) |
| `packages/a/package.json` | `package-lock.json` (workspace root) | 1 | **no** |
| `package.json` | `bun.lock` | 1 | **no** |
| `./package.json` | `package-lock.json` | 1 | no (path spelling; GitHub is not known to emit `./`, so this is theoretical) |

## Recommendations

1. **Close or re-scope the backlog item.** The "dependency compare lists only the lockfile" defect is fixed by `9fe6ca0`/#65 and pinned by tests.
2. **Check pointback's pin.** If PR 138/139 still show red, look at the gates commit in pointback's `.github/gates.lock` (or its `shared-dependency-advisories.yml@` SHA). If it is older than `9fe6ca0`, move it with `bin/gates.py bump` (README.md, "Bumping"). Then re-run the PR's `checks` and confirm the summary prints "Fixed in this pull request, so not failing". That needs access to the private caller, which this session did not have.
3. **Optional follow-ups**, each a separate small defect that needs a live measurement before changing the rule (in line with README.md:64's "measured" wording):
   - **Workspaces:** credit a member `package.json` alert from a lockfile in an ancestor directory. To keep the "another directory gets no credit" property, only do this when the ancestor `package.json` declares `workspaces` (or a `pnpm-workspace.yaml` exists). Otherwise this could credit an unrelated sibling project. Needs a fixture taken from a real workspace compare answer.
   - **bun:** add `bun.lock` to `siblings` if GitHub's dependency graph lists bun changes under it (not verified here). Add a test case next to `.ci/test-dependency-advisories.sh:350-357`.
   - Neither change touches a `SECURITY_ROWS`/permissions surface. Both are edits to `run/dependency-advisories/judge.sh` plus its test. The gate-registry row (`.ci/gate-registry.toml:43-53`) already covers those files.
4. **Developer environment note:** `.ci/test-dependency-advisories.sh:37-39` only checks that a `yq` exists. With the Python `yq` it reports dozens of misleading failures. A `yq --version | grep -q mikefarah` guard (exit 2 with a clear message) would save the next session the detour.
