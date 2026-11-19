# Final verification of gates pull request 116 at 0fa3c652

Head `0fa3c652be1079204162256690878b7a32013912` ("seats: pin CLI 2.1.328, the version run 4 measured; a safety_stops that is not a count is undecided"), one commit on top of the previous round's `dfb72e84`.
Base `main` at `cc7e3c44`.
Date 2026-11-19.

Verdict 116 (final at 0fa3c652): PARTIAL

Steps 1, 4 and 5 pass. Steps 2 and 3 pass the checks they name, each with one gap to close before merging:

- Step 2: the tree is consistent (pin, 46 `version.txt` files, README and 0fa3c652's message all name 2.1.328 for run 4), but the pull request body, which a squash merge here makes `main`'s commit message, still names 2.1.323 as the pin and stops at run 3.
- Step 3: `"yes"`, `[1]`, `{"n": 1}`, `0.5` and `"0"` are now undecided, and the new test fails on dfb72e84 and passes on 0fa3c652; but `[]`, `""`, `false` and `-1` still read as no safety stop and let both approvals through, because `or 0` runs before the type check. No input a pull request author controls reaches this field.

## Step 1, the pin: PASS

`verifier/review.sh` at 0fa3c652:

```text
18:CLI_VERSION=2.1.328
19:CLI_URL=https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-$CLI_VERSION.tgz
20:CLI_SHA512=KuzL+dBHgDCFTHJxwW+xuBJdVKfBsesGx2BBcTOG9sYDGoAD7b1hUV8rsTS4xjoJsB2SmeGraGjBiFuhrYuC9w==
```

The registry's own record, `https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/2.1.328`, `dist.integrity`:

```text
2.1.328 sha512-KuzL+dBHgDCFTHJxwW+xuBJdVKfBsesGx2BBcTOG9sYDGoAD7b1hUV8rsTS4xjoJsB2SmeGraGjBiFuhrYuC9w==
```

The tarball at `CLI_URL`, downloaded here (172342166 bytes) and hashed independently with `openssl dgst -sha512 -binary | base64 -w0`:

```text
KuzL+dBHgDCFTHJxwW+xuBJdVKfBsesGx2BBcTOG9sYDGoAD7b1hUV8rsTS4xjoJsB2SmeGraGjBiFuhrYuC9w==
```

All three are the same string.
The tarball's `package/package.json` says `"name": "@anthropic-ai/claude-code-linux-x64"`, `"version": "2.1.328"`, and the `@anthropic-ai/claude-code@2.1.328` meta-package names `claude-code-linux-x64` at `2.1.328` as its optional dependency.
I did not run the binary from the tarball, so its own `--version` is not checked here; the commit message says the author did.
The download-and-check logic in `review.sh` is unchanged from dfb72e84 (only lines 18 and 20 differ).

## Step 2, the record: PASS in the tree; the pull request body is stale

- Acceptance `version.txt`: the 46 files under `.ci/fixtures/review/corpus/seats/` (20 weakenings plain and steered, 6 clean changes plain) all read `2.1.328 (Claude Code)`. The tree's other 135 `version.txt` files belong to older corpora (critic, lens, design, licence, release, replies) and read 2.1.302 or 2.1.304, matching the README sections that cite those runs.
- README line 474 at 0fa3c652: "runs 1 to 3 on 2026-11-13 with CLI 2.1.323 and the earlier prompt and reader, run 4 on 2026-11-19 with CLI 2.1.328, the version `review.sh` pins, and the whole-reply rule". At dfb72e84 it said "with CLI 2.1.323" for all four.
- Commit 0fa3c652's message: "All 46 version.txt files of acceptance run 4 say 2.1.328 (Claude Code) ... review.sh now pins 2.1.328 ... the README states 2.1.323 for runs 1 to 3 and 2.1.328 for run 4." True, as checked above.
- `git grep -E '2\.1\.32[0-9]'` over 0fa3c652 outside `version.txt` finds only README line 474 and `review.sh` line 18.
- Runs 1 to 3's version (2.1.323) cannot be checked from the tree: their recorded replies were replaced by run 4's. I take it as stated.

Stale records outside the tree, which the fix did not touch:

1. **The pull request body**, which this repository's squash merges use as the landed commit message on `main` (as `cc7e3c4`'s message shows), still says `verifier/review.sh` downloads "the `claude-code-linux-x64@2.1.323` registry tarball", and its test plan gives only runs 1 to 3 ("Live, CLI 2.1.323 ... three full runs"), with run 3 as "(recorded)". At 0fa3c652 the pin is 2.1.328 and the recorded replies are run 4's. If the PR lands with that body, `main`'s record of this change names the wrong pin and the wrong recorded run.
2. Commit dfb72e84's message still says "run 4 of the acceptance (2026-11-19, CLI 2.1.323)". 0fa3c652's message corrects it, and a squash merge drops it from `main`.

Before merging, the body should name 2.1.328 as the pin and add run 4 with its version. Nothing in the tree needs to change.

## Step 3, the crash: PASS for the crash; falsy values still read as no stop

The new test, `safety_stops_not_a_count_is_undecided` in `.ci/test-seats.py`, tries `"yes"`, `[1]` and `0.5`.

- On 0fa3c652, `python3 .ci/test-seats.py` exits 0 ("seats tests passed", 96 `ok` lines), including `ok   safety_stops_not_a_count_is_undecided` and its mutant `ok   without a safety_stops that is not a count refused, safety_stops_not_a_count_is_undecided goes red: safety_stops 'yes': exit 1, verdict None, not undecided`.
- On dfb72e84, with only `.ci/test-seats.py` replaced by 0fa3c652's, it exits 1:
  `FAIL safety_stops_not_a_count_is_undecided: safety_stops 'yes': exit 1, verdict None, not undecided`, ending in
  `ValueError: invalid literal for int() with base 10: 'yes'` at `run/review.py` line 758, `if int(envelope.get("safety_stops") or 0) > 0:`.
  The only other FAIL there is the n6 mutant, whose anchor text moved in 0fa3c652 (`the mutation matches 0 places in review.py, not 1`), as expected.

My own probe, `data/final-verify-116/stops.sh`, runs the real `run/seats.py` through the earlier rounds' `attack.sh` unchanged, with the permit rm rule deleted, both seats answering `{"approve": true, "findings": []}`, and every envelope carrying the given `safety_stops`:

| `safety_stops` | dfb72e84 | 0fa3c652 |
| --- | --- | --- |
| `"yes"` | exit 1, no verdict, `ValueError` | exit 2, undecided: "gave safety_stops yes, not a count" |
| `[1]` | exit 1, `TypeError` | exit 2, undecided |
| `{"n": 1}` | exit 1, `TypeError` | exit 2, undecided |
| `0.5` | exit 0, **`{"blocking": 0}`** | exit 2, undecided |
| `1.0`, `true`, `"1"` | exit 2, safety stop | exit 2, undecided, not a count |
| `"0"` | exit 0, `{"blocking": 0}` | exit 2, undecided |
| `1`, `2` | exit 2, safety stop | exit 2, safety stop |
| `0`, `null` | exit 0, `{"blocking": 0}` | exit 0, `{"blocking": 0}` |
| `[]`, `""`, `false` | exit 0, `{"blocking": 0}` | exit 0, **`{"blocking": 0}`** |
| `-1` | exit 0, `{"blocking": 0}` | exit 0, **`{"blocking": 0}`** |

The fix closes every crash and the `0.5` approval.
Its gap: `stops = envelope.get("safety_stops") or 0` turns any falsy value into the integer 0 before the type check, so an empty list, an empty string or `false` reads as "not stopped" and the seats approve; a negative integer does too.
How much this matters: the envelope is written by the CLI, not by the model or the pull request, and every one of the 499 recorded envelopes in the tree carries `"safety_stops": 0` or `"safety_stops":0`, so no input a pull request author controls reaches this.
It still falls short of "a non-numeric or list `safety_stops` is undecided" for `[]` and `""`.
A fix: `stops = envelope.get("safety_stops", 0)`, then refuse unless `type(stops) is int and stops >= 0`, with `[]`, `""`, `False` and `-1` added to the test's values.

## Step 4, regression: PASS

`data/reverify-116/cases.sh` (the earlier rounds' driver, unchanged, over `data/verify-116/attack.sh`, unchanged) run on worktrees of dfb72e84 and 0fa3c652.
37 cases each.
The two outputs differ only in the fixture head shas inside the verdicts (commit timestamps).
Per case, exit and `blocking`:

| Case | 7ede1f49 (round 2's record) | dfb72e84 | 0fa3c652 |
| --- | --- | --- | --- |
| a1, a2, a3, a4a, a5 | 0 / 0 | 0 / 0 | 0 / 0 |
| a4h | 1 / 2 | 1 / 2 | 1 / 2 |
| r1, r2, r5-r8, r10-r15 | 2 / none | 2 / none | 2 / none |
| r3 | 0 / 0 | 0 / 0 | 0 / 0 |
| r4, r9 | 1 / 1 | 1 / 1 | 1 / 1 |
| n1, n2a-c, n4, n4n, n5a, n5b | 2 / none | 2 / none | 2 / none |
| n1q, n5d, n6 | **0 / 0** | 2 / none | 2 / none |
| n3, n3f, n5c, n5e, n7 | 0 / 0 | 0 / 0 | 0 / 0 |

Every case keeps the verdict it had on dfb72e84.
n1q, n5d and n6 are the round-2 failures that round 3 found fixed on dfb72e84, and they stay fixed.
Round 3's own new cases (the `s` cases its fix cites) were not saved in a branch; `stops.sh` above stands in for them.

### `.ci/check.sh all`, head 0fa3c652 against main cc7e3c4

Same command, same container, worktrees of both commits:

```text
0fa3c652:  ok python   | failed: shell secrets gates-proof   (rc=1)
cc7e3c4:   ok python   | failed: shell secrets gates-proof   (rc=1)
```

The FAIL lines are the same on both, five each:

```text
missing tool: shellcheck - install with: brew install shellcheck
FAIL shell
missing tool: qpdf - install with: brew install qpdf
FAIL secrets
FAIL artifact-scan: red input was not refused
FAIL gates-proof
```

They are this container's missing tools, not the branch's: `main` fails the same way, and CI installs them.
The only lines on the head and not on main are three more passes, `ok   seats-hold`, `ok   seats-quoted-approval`, `ok   seats-eval-steered`.
`python3 .ci/test-seats.py` on its own: exit 0, as under step 3.

## Step 5, scope: PASS

`git diff --stat dfb72e84 0fa3c652`:

```text
 .ci/test-seats.py  | 13 ++++++++++++-
 README.md          |  2 +-
 run/review.py      |  8 +++++++-
 verifier/review.sh |  4 ++--
 4 files changed, 22 insertions(+), 5 deletions(-)
```

- `verifier/review.sh`: `CLI_VERSION` and `CLI_SHA512` only (step 1).
- `README.md`: line 474 only (step 2).
- `run/review.py`: the `safety_stops` read in `sit` only (step 3).
- `.ci/test-seats.py`: the new case, its row, and the n6 row's anchor moved from the old `int(...)` line to `if stops > 0:` (step 3). The n6 mutant still removes the same control.

Nothing else changed.

## How this was run

```text
curl https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/2.1.328            -> dist.integrity as quoted
curl -o cli.tgz <CLI_URL>; openssl dgst -sha512 -binary cli.tgz | base64 -w0           -> as quoted
git worktree add --detach <wt> dfb72e84 / 0fa3c652 / cc7e3c4
python3 .ci/test-seats.py   (0fa3c652)                                                 -> exit 0
python3 .ci/test-seats.py   (dfb72e84, test file from 0fa3c652)                        -> exit 1, as quoted
data/reverify-116/cases.sh <wt> <work>   (dfb72e84, 0fa3c652)                          -> as tabled
data/final-verify-116/stops.sh <wt> <work>   (dfb72e84, 0fa3c652)                      -> as tabled
.ci/check.sh all   (0fa3c652, main cc7e3c4)                                            -> as above
```

Read-only on GitHub: one `pull_request_read` of #116; no comment, review, approval or merge.

## Spend

One session, roughly 70 minutes wall-clock, most of it the two `.ci/check.sh all` runs in parallel.
Network: one 172 MB registry tarball and two small registry JSON reads.
Model tokens are not visible from inside the session, so I cannot report them.
No CI minutes and no paid API calls: the seats ran only against the recorded stub.
