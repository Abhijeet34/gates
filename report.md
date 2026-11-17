# P3a-SR3F4: the weakening guard and deleted tests, deleted `Test*` functions and a bare early return (sr3-6)

Scout report for backlog id `factory-p3a-sr3f4-weakening-guard`, against `main` at `cc7e3c44cd125c2335b835a24791b4dab63db650`.

## What I could not do

- I could not read the source report (`data/factory-p3a-sr3-security-rereview/report.md`, section 6) because it lives in the private repository. This report works from the goal line alone: the guard does not read deleted test files, deleted `Test*` functions or a bare early return. I reproduced each of those three gaps against the code. If section 6 names other cases, this report does not cover them.
- This report changes no code, as a scout's should. The recommended fix is laid out below but not written.

## What I did

1. Read `run/weakening.py` (the whole file), README.md "The weakening guard" (lines 216–261), the review prompts in `run/review.py` (`FINDER`, `LENS_PREAMBLE`), the `tester` lens in `corpus/lens/gates.toml`, the caller in `run/owner-verify.py`, and the test-deletion design in `run/mutation.py` / README "Deleting or weakening a test" (lines 1242–1253).
2. Built a scratch fixture repository with a TypeScript architecture test and a Go `_test.go` file. I ran `run/weakening.py --tests 'test/architecture/**' --tests '**/*_test.go'` against seven changes: the three gaps, a renamed test file, and two controls that should be refused.
3. Ran the existing suite: `python3 .ci/test-weakening.py` gives `test-weakening: 43 of 43 cases passed`. No current case covers any of the gaps.

## What I found

### Reproduction

The fixture was run with `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null`, `GATES_DIR` set to this checkout, `--repo Abhijeet34/x`, and each change committed on the base:

| Change | Guard output | rc |
| --- | --- | --- |
| 1. `git rm test/architecture/extra.test.ts` | `weakening: no weakening` | 0 |
| 2. `func TestTokenRejected` deleted from `internal/auth_test.go` | `weakening: no weakening` | 0 |
| 3a. bare `return` added as the first line of `TestTokenRejected` | `weakening: no weakening` | 0 |
| 3b. `return;` added at the top of the TS `test('pins', …)` body | `weakening: no weakening` | 0 |
| 4. `git mv test/architecture/extra.test.ts test/extra.test.ts` (moved out of `--tests`) | `weakening: no weakening` | 0 |
| control: `continue;` added in the TS loop | `weakening: test-exemption at test/architecture/supply-chain.test.ts:6: continue;` … `refused` | 1 |
| control: `t.Skip("x")` added to the Go test | `weakening: test-exemption at internal/auth_test.go:6: t.Skip("x")` … `refused` | 1 |

Each of these changes disables a security test just as completely as the `skip` and `continue` the guard does refuse.

### Why: code references

- **Deleted files (gap 1) and renamed-away files (gap 4).** `run/weakening.py:179-181` lists candidate paths with `git diff --name-only --no-renames -z --diff-filter=AMT`. `D` is filtered out, so a deleted file never reaches `changed_lines`. With `--no-renames`, a rename appears as a `D` of the old path plus an `A` of the new one. If the new path falls outside every `--tests` glob, it is skipped at line 184.
- **Deleted `Test*` functions (gap 2).** `exemptions()` (`run/weakening.py:177-200`) reads only *added* lines. The removed lines feed only the `gone` Counter (lines 187-189), which stops a moved exemption line from counting as new. No count of test declarations is compared between base and head.
- **Bare early return (gap 3).** `EXEMPT` (`run/weakening.py:47-50`) matches `continue`, `.skip`/`.skipIf`/`.todo`, `skip:`/`todo:`, `pytest.mark.skip`, `unittest.skip`, `skipTest(`, `#[ignore` and `t.Skip*(`. It does not match `return`, so a `return` that ends a test before its assertions is invisible. Python's `pytest.skip()` call, as opposed to the mark, is not matched either.

### The documentation does not state these gaps

- README.md:224 defines `test-exemption` as "a line added … that skips, ignores or marks a test todo, or a `continue`". That is accurate, but nothing in the section names what it does not read. README.md:29 calls the guard the check for "a change that relaxes its tests", and a reader would take that to include deleting them.
- The module docstring (`run/weakening.py:8`) matches the README and is equally silent.
- The review prompts never send a reviewer looking for these gaps:
  - `FINDER` (`run/review.py:134-153`) asks for defects in general.
  - The `tester` lens (`corpus/lens/gates.toml:144-156`) asks whether new checks would fail before the change. It never asks whether existing tests were deleted or cut short.
- `.github/PULL_REQUEST_TEMPLATE.md:17` asks the author to declare "Tests removed or weakened". `run/pr-text.py` does not enforce that section (no match for it), so it is self-report only.
- The mutation rows partly cover gap 2, but only for Rust and TypeScript (README.md:1242-1253; `run/mutation.py rust|typescript`):
  - A deleted test file counts as zero.
  - A falling `it(`/`test(`/`#[test]` count forces a full mutation run.
  - Go `func Test*` and Python `def test_` deletions are covered by nothing.
  - A bare `return` keeps every count unchanged, so it passes there too.

### A related finding the goal does not name

`run/owner-verify.py:490-496` runs `weakening.py` with `--base`, `--head` and `--repo` but **no `--tests`**. In the verifier's judge, `exemptions()` therefore matches no path, and the `test-exemption` kind can never fire. Even the cases the guard does read today (`skip`, `continue`) go unseen on that path.

README.md:289 and :310 describe owner-verify as refusing "when `run/weakening.py` finds a weakening", which overstates what it checks. This needs either a `--tests` source (for example a `tests` key in the base's `.github/owner-verify.toml`) or a sentence saying owner-verify judges workflows and rulesets only.

## What I recommend

Close the three gaps in code, and add one README sentence for what remains. Do this before P3a-M6 puts the `weakening-*` rows in shadow.

1. **Add a `test-removed` kind** for files under `--tests`:
   - **Deleted file.** Change `--diff-filter=AMT` to `ADMT` in `exemptions()`. Emit `test-removed at PATH: file deleted` for a deleted path that matches `--tests`. A rename out of scope then reads as a deletion, which is the safe reading; a rename within scope carries its lines across and should pass.
   - **Deleted test function.** Count test declarations per file at base and head, as `run/mutation.py`'s `counts()` does, and emit a finding when the count falls. Suggested patterns:
     - Go: `^func Test\w*\(`
     - Python: `^\s*(?:async\s+)?def test_`
     - TS/JS: `\b(?:it|test)(?:\.\w+)*\(`
     - Rust: `#\[(?:\w+::)?test\]`

     Counting rather than diffing keeps a moved test from counting as a removal, matching the mutation row's reasoning.
   - **Record matcher.** Add `path` to the record schema in `KINDS` and `covers()`, as a pattern that fullmatches `PATH` or `PATH: TestName`. A ratified deletion can then be recorded like any other relaxation. Add `test-removed` to `load_policy()`'s schema checks.
2. **Extend `EXEMPT` to a bare early return:**
   - `\breturn\s*;?\s*(?:$|\})`: a `return` with no value, alone or closing a one-line guard.
   - `\bpytest\.skip\(`.

   The existing moved-line Counter already absorbs a re-indented `return`. A bare `return` in a test helper is a possible false positive, and the existing literal-record mechanism or a `path` record answers it.
3. **Tests and registry, in the same commit (AGENTS.md: a gate row needs its `red`/`clean` inputs):**
   - Add cases 1–4 above and each control to `.ci/test-weakening.py`.
   - Add a `weakening-test-removed` row to `.ci/gate-registry.toml` beside `weakening-test-exemption` (line 742).
   - `run/weakening.py` is on the permissions-diff surface (`run/permissions-diff.py:478`), so this is a class-2 change.
4. **Documentation:**
   - Update the README table at line 224 and the docstring at `run/weakening.py:8`.
   - Add a sentence stating what the guard still does not read: an assertion replaced by a weaker one, a conditional `return` such as `if (x) return` written across two lines, and a test file outside `--tests`.
   - Add one checklist line to the `tester` lens so the reviewer covers what is left: "Does the change delete a test, or end one before its assertions, without saying why?"
5. **Owner-verify:** fix or document the missing `--tests` described above.

If the code change is declined, the minimum that satisfies the goal is step 4 alone: state all three gaps in README "The weakening guard" and in the docstring, and add the `tester` lens line so a reviewer is told to look.
