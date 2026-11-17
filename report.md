# Scout report: advisories judge and package.json-manifest alerts

Task `gates-issue-1-2`, backlog `gates-advisories-manifest-sibling-a2`, base `main` at `cc7e3c4`.

## What I could not do

- **I could not read pointback, its PRs 138 and 139, its `.github/gates.lock`, or its live alerts and compare.**
  This session's GitHub access covers `Abhijeet34/gates` only.
  So I could not confirm which gates commit pointback's advisories job actually runs, or what the live compare returns for PR 138.
- I did not call the live `dependency-graph/compare` endpoint, so this report has no new measurement of its shape.
  The only measurement is the 2026-10-25 one recorded in the repository (see below).

## Summary

**On `main` the defect, as the backlog states it, is already fixed.**
`9fe6ca0` (#65, 2026-10-25, "credit a package.json advisory fixed by its sibling lockfile in the compare") added a sibling-lockfile rule to the judge.
`8a945d6` (#105, 2026-11-11) kept that rule.
For the PR-139 shape (a `package.json` alert, with the fix listed under `package-lock.json` in the same directory), the judge on `main` passes.
The test suite pins that, including a mutation that removes the rule.

If pointback 138/139 are still blocked, the likely reasons are these, most likely first:

1. **Pointback runs a gates commit older than `9fe6ca0`.** That would be a `.github/gates.lock`, or a pinned `uses: …@sha`, from before 2026-10-25.
   The fix is a lock bump (`bin/gates.py bump`), not a gates change.
2. **PR 138 has a shape the sibling rule does not cover.** The rule credits a lockfile only in the *same directory* as the `package.json`, and only `package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock` and `pnpm-lock.yaml`.
   The probes below show which shapes still fail.
3. **The live compare does not put the GHSA on the `removed` entry**, or it spells the manifest path differently (`./`, a leading `/`).
   Either way the judge gives no credit.
   The 2026-10-25 measurement says it does put it there, so this is the least likely.

## Evidence

### The rule on main

`run/dependency-advisories/judge.sh`, in the verdict program (`def siblings` and `def fixed`, around lines 114–128):

```jq
def siblings($x):
  if ($x.manifest | test("(^|/)package\\.json$"))
  then ($x.manifest | sub("package\\.json$"; "")) as $dir
       | ["package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml"] | map($dir + .)
  else [] end;
def fixed($chg; $x):
  (touches($chg; $x; "removed") and (touches($chg; $x; "added") | not))
  or ( siblings($x) as $s
       | any($s[]; . as $m | touches_at($chg; $x; "removed"; $m))
         and (any($s[]; . as $m | touches_at($chg; $x; "added"; $m)) | not) );
```

`README.md:63-64` documents it as "measured 2026-10-25 on pointback PR 139".

```
$ git log -1 --format='%h %ad %s' --date=short 9fe6ca0
9fe6ca0 2026-10-25 fix(run): credit a package.json advisory fixed by its sibling lockfile in the compare (#65)
```

### The suite passes

```
$ PATH=<mikefarah yq v4.44.3>:$PATH bash .ci/test-dependency-advisories.sh
...
ok   a package.json alert is fixed by the sibling lockfile removing the version
ok     and names the package.json alert as fixed
ok   a sibling lockfile that still adds the affected version gives no credit
ok   a lockfile in another directory gives a package.json alert no credit
ok   the sibling rule does not reach a requirements.txt alert
...
ok   dropping the sibling rule fails the PR-139 shape
dependency-advisory gate: every property pinned        (exit 0, 143 ok, 0 FAIL)
```

The PR-139 fixture is at `.ci/test-dependency-advisories.sh:331-357`, and the mutation is case 7 at `:761-773`.

**Pitfall in the environment:** the container's `/usr/bin/yq` is the Python jq wrapper, which reports itself as `yq 0.0.0`.
With it, about 30 deferral and floor cases fail with `jq: Unknown option -o=json` or `severity_floor is ""high""`.
That is an environment fault, not a code fault.
Use mikefarah `yq` v4 before reading those failures as real.

### Probes of the real judge script

I ran `judge.sh` with `EVENT_NAME=pull_request`.
Each probe used one high runtime alert for `markdown-it` / `GHSA-gfr2-rv44-r23c` at the alert manifest shown.
Its compare removes 15.0.2 and adds 15.0.3 at the compare manifest shown.

| alert manifest | compare manifest | GHSA on `removed` | exit | credited |
| --- | --- | --- | --- | --- |
| `package.json` | `package-lock.json` | yes | 0 | yes |
| `web/package.json` | `web/package-lock.json` | yes | 0 | yes |
| `package.json` | `package.json` | yes | 0 | yes |
| `packages/a/package.json` | `package-lock.json` (root) | yes | 1 | **no**: npm, yarn or pnpm workspace |
| `package.json` | `/package-lock.json` | yes | 1 | **no**: path spelling |
| `package.json` | `./package-lock.json` | yes | 1 | **no**: path spelling |
| `package.json` | `bun.lock` | yes | 1 | **no**: lockfile not listed |
| `package.json` | `package-lock.json` | **no** (empty list) | 1 | **no**: needs the GHSA on the removed row |

The workspace row is the most plausible real gap.
In a workspace monorepo the alert is on the member's `package.json`, but the only lockfile sits at the root.
Today that gets no credit, by design: the test "a lockfile in another directory gives a package.json alert no credit" pins it.

## Recommendation

1. **First, check pointback's pin** (in a session with pointback access).
   Read `.github/gates.lock`, or the `uses:` ref of its advisories workflow.
   If it is older than `9fe6ca0`, bump it with `bin/gates.py bump`, then re-run 138/139.
   No gates change is needed for the PR-139 shape.
2. **If PR 138 is still red on a current pin**, read its live compare:
   `gh api repos/<owner>/pointback/dependency-graph/compare/<base>...<head>`.
   Compare its `manifest` values and its `removed[].vulnerabilities` with the alert's `dependency.manifest_path`.
   Then extend the rule only to the shape actually measured:
   - For a workspace, credit an ancestor-directory lockfile only when no lockfile exists in the `package.json`'s own directory.
     Add a fixture and a mutation case, as for PR 139.
     Widening without that guard would let an unrelated root lockfile credit a nested project.
   - For path spelling, normalise a leading `./` or `/` on both sides in `touches_at`.
   - `bun.lock` and `bun.lockb` can be added to `siblings` if a caller uses Bun.
3. **Update the backlog item.** "Never credits" is not true of `main`.
   Re-scope it to whichever of the causes above the pointback check confirms, or close it.

Nothing in the gate code was changed by this task.
