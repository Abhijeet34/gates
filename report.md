# Scout report: the weakening guard misses deleted tests and a bare early return (gates-issue-18)

Backlog id `factory-p3a-sr3f4-weakening-guard`, finding sr3-6. Scouted at `main` = `1739f4557dad8e59583bda236eb3b380b25a97ea`. No code was changed; this report is the only deliverable.

## What I could not do

- **I could not read the source report** (`data/factory-p3a-sr3-security-rereview/report.md`, section 6). It lives in the private `automation` repository, which this session cannot reach. I worked from the backlog goal text alone: deleted test files, deleted `Test*` functions and a bare early return. Each of the three is reproduced below.
- **"The review prompt" is my guess.** No prompt in this repository mentions the weakening guard. I read the two candidates: the finder system prompt `FINDER` in `run/review.py:134`, and the "Tests removed or weakened" section of `.github/PULL_REQUEST_TEMPLATE.md:17`. If section 6 names a different prompt (for example one in `automation`), the "state them out" option below should go there.

## Summary

All three gaps are real. At `main`, `run/weakening.py` exits 0 with `weakening: no weakening` on each of these changes:

1. a deleted test file, including a test file renamed out of the `--tests` globs;
2. a deleted Go `func TestX` (or a deleted TS `test(...)` call) in a file that remains;
3. a bare `return` added at the top of a test body in Go, TypeScript or Python.

I also found a fourth gap with the same effect. `run/owner-verify.py` runs the guard **with no `--tests` argument**, so the `test-exemption` kind never fires inside the owner-verify judge, whatever the change does to tests.

For Go callers nothing else closes these gaps. `run/mutation.py`'s delete/weaken detection, which the README presents as the backstop, covers only Rust and TypeScript.

I recommend fixing the gaps in code (option A), not only stating them in the docs. The details are under "Recommendation".

## What the guard reads today

`run/weakening.py`:

- `exemptions()` (`run/weakening.py:183-205`) reads only paths from `git diff --name-only --no-renames --diff-filter=AMT`. **Deleted files (`D`) are never read.** A rename becomes D+A under `--no-renames`, so the D half is dropped too.
- For a file it does read, it judges only **added** lines that match `EXEMPT`. Removed lines are used only to cancel a moved line (`gone` Counter, `:194-204`). **A removed test function is never a finding.**
- `EXEMPT` (`run/weakening.py:47-50`) matches `continue`, `.skip/.skipIf/.todo`, `skip:`/`todo:` options, `pytest.mark.skip`, `unittest.skip`, `skipTest(`, `#[ignore`, `t.Skip/SkipNow/Skipf(`. **It has no `return` pattern**, so `return` at the top of a test passes while `t.SkipNow()`, which has the same effect, is refused.
- With no `--tests`, `tests` is `[]`, `exemptions()` matches no path and the kind is inert (`:186-187`).

`run/owner-verify.py:482-502` builds the command as `weakening.py --base B --head H --repo R`. **It passes no `--tests`.** `README.md:310` nonetheless lists "any path, when `run/weakening.py` finds a weakening no record covers" as a refusal for every path.

README, "The weakening guard" (`README.md:216-261`), defines `test-exemption` as "a line added to a file `--tests` names that skips, ignores or marks a test todo, or a `continue`". That definition is accurate, but nothing says that deletion and early return are out of scope. `README.md:29` describes the guard as refusing "a change that relaxes its tests", which reads as wider than the code.

`run/mutation.py` ("Deleting or weakening a test", `README.md:1178-1192`) counts tests, assertions and skips per file, and a deleted file counts as zero. That detection covers Rust and TypeScript only: `grep -ciE '\bgo\b|golang|_test\.go' run/mutation.py` returns `0`. The owner-verify example tier list (`verified = ["internal/domain/permit/"]`, `README.md:300`) and the `t.Skip` pattern show that Go callers are in scope.

## Evidence

The reproduction script (kept below) builds a fixture repository per case with `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null` and `GATES_DIR` set to this checkout. It then runs `python3 run/weakening.py --base base --repo Abhijeet34/fixture --tests <glob>`.

```text
== 1 deleted Go test file: exit 0
   weakening: no weakening
== 1b deleted TS test file: exit 0
   weakening: no weakening
== 1c renamed test file out of --tests: exit 0
   weakening: no weakening
== 2 deleted Test* function: exit 0
   weakening: no weakening
== 2b deleted TS test() call: exit 0
   weakening: no weakening
== 3 bare early return (Go): exit 0
   weakening: no weakening
== 3b bare early return (TS): exit 0
   weakening: no weakening
== 3c early return (Python): exit 0
   weakening: no weakening
== control: t.Skip added: exit 1
   weakening: test-exemption at pkg/a_test.go:12: t.Skip("later")
   weakening: refused: 1 weakening(s) match no recorded policy in policy/relaxations.toml
== control: continue added: exit 1
   weakening: test-exemption at pkg/a_test.go:12: for { continue }
   weakening: refused: 1 weakening(s) match no recorded policy in policy/relaxations.toml
== 4 no --tests (as owner-verify calls it): t.Skip added: exit 0
   weakening: no weakening
```

Two results matter most:

- The controls show that the fixture and glob are right. The same file and change with `t.Skip` instead of `return` is refused.
- Case 4 is the owner-verify invocation shape.

The existing suite is green at `main`:

```text
$ python3 .ci/test-weakening.py
test-weakening: 43 of 43 cases passed
```

Reproduction script (bash; the cases are in its last lines):

```bash
set -u
G=/path/to/gates
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GATES_DIR=$G
export GIT_AUTHOR_NAME=f GIT_AUTHOR_EMAIL=f@f GIT_COMMITTER_NAME=f GIT_COMMITTER_EMAIL=f@f
run() { # name, base setup, change, tests glob ('' for none)
  d=$(mktemp -d); cd "$d"; git init -q -b main
  eval "$2"; git add -A; git commit -qm base; git tag base
  eval "$3"; git add -A; git commit -qm change
  out=$(python3 $G/run/weakening.py --base base --repo Abhijeet34/fixture ${4:+--tests "$4"} 2>&1); rc=$?
  echo "== $1: exit $rc"; echo "$out" | sed 's/^/   /'; cd /; rm -rf "$d"
}
GOBASE='mkdir -p pkg; printf "package pkg\n\nimport \"testing\"\n\nfunc TestA(t *testing.T) {\n\tif A() != 1 {\n\t\tt.Fatal(\"A\")\n\t}\n}\n\nfunc TestB(t *testing.T) {\n\tif B() != 2 {\n\t\tt.Fatal(\"B\")\n\t}\n}\n" > pkg/a_test.go'
run "1 deleted Go test file" "$GOBASE" 'git rm -q pkg/a_test.go' 'pkg/**'
run "2 deleted Test* function" "$GOBASE" 'python3 -c "p=\"pkg/a_test.go\";s=open(p).read();open(p,\"w\").write(s[:s.index(\"func TestB\")].rstrip()+\"\n\")"' 'pkg/**'
run "3 bare early return (Go)" "$GOBASE" 'sed -i "s/^func TestB(t \*testing.T) {/&\n\treturn/" pkg/a_test.go' 'pkg/**'
run "4 no --tests: t.Skip added" "$GOBASE" 'sed -i "s/^func TestB(t \*testing.T) {/&\n\tt.Skip(\"later\")/" pkg/a_test.go' ''
```

## Recommendation

The goal allows either fixing the guard or stating the gaps out. I recommend **A, fix the guard**, plus B for whatever A deliberately leaves out. Stating the gaps out alone would leave a Go caller with no gate against the cheapest way to weaken a security test, which is to delete it. P3a-M6 is about to put these rows in shadow, so the guard should first match what README.md:29 already claims.

### A. Code changes in `run/weakening.py`

1. **New kind `test-removed`** for deleted test files and deleted test declarations. Count test declarations per file at the merge base and at HEAD, the way `run/mutation.py` counts. Read every path under `--tests` at both revisions (`files(old, tests)` / `files(head, tests)`), not just the `AMT` diff.
   - A file present at the base and absent at HEAD counts as zero.
   - Report a finding for each file whose count drops: `test-removed at <path>: <n> of <m> test(s) gone`.
   - Declarations to count: Go `^func (Test|Fuzz|Example)\w*\(`; TS/JS `\b(it|test)(\.\w+)*\(`; Rust `#[test]`, `#[tokio::test]`, `#[rstest]`; Python `^\s*(async\s+)?def test_\w*\(`. That set covers the patterns `EXEMPT` already names.
   - Comparing counts rather than removed lines ignores a reformat or a move within the file. Counting per file is the same rule `README.md:1188` uses: a deletion cannot hide behind a test added elsewhere. A test moved between files is refused, which fails closed, and a record or owner approval answers it.
   - Matcher: `path`, a pattern the path must fullmatch. This lets a ratified retirement of one test file be recorded without covering others. It adds `test-removed` to `KINDS`, `path` to `MATCHERS`, and a row to the README table.
2. **Extend `test-exemption` to a bare early return.** Report an added line that is a bare `return` / `return;` / `return None` when the next non-blank, non-comment line at HEAD sits at the same or deeper indent and is not a closing `}` / `})` / `});` or a dedent. In other words, report the return when unreachable test code follows it.
   - This catches cases 3, 3b and 3c and spares the ordinary guard `if err != nil {\n\treturn\n}`.
   - It needs the HEAD text of the file, which `exemptions()` can get with `git show head:path`.
   - Existing records still match it by `ref` literal, but a bare `return` holds no literal, so no record covers it. That is the intended outcome.
3. **Never leave `test-exemption` inert.** When `--tests` is absent, default to the test-file globs that match the declarations above: `**/*_test.go`, `**/*.test.*`, `**/*.spec.*`, `**/test_*.py`, `**/*_test.py`, `**/tests/**`, `**/test/**`. `run/owner-verify.py` then reads tests with no change to its call.
   - The alternative is a `tests = [...]` key in `.github/owner-verify.toml` that owner-verify passes through. A default is safer, because a missing key would otherwise mean "no tests".

Each change needs, in the same commit (AGENTS.md):

- cases in `.ci/test-weakening.py`: each reproduction above red, its guard-shaped neighbour clean, and an owner-verify case in `.ci/test-owner-verify.py` that refuses an added `t.Skip` with no `--tests`;
- new `.ci/gate-registry.toml` rows (`weakening-test-removed`, `weakening-early-return`), each with `red`/`clean`/`run`/`finding`;
- an update to the README table and to the "runs …" paragraph at `README.md:259`.

`run/weakening.py`, `run/owner-verify.py` and `policy/**` are on the `run/permissions-diff.py` surface (`run/permissions-diff.py:474-478`), and the new rows are `surface = "Security"`. **This is therefore a class-2 decision** once a caller promotes permissions-diff out of shadow, and it should land before P3a-M6, as the backlog item says.

### B. State out what remains (needed with A, and the whole fix if A is declined)

- Add a "What it does not read" paragraph to `README.md` "The weakening guard". If A lands, it lists:
  - an assertion replaced by a weaker one (`t.Fatal` → `t.Log`, `assert_eq!(x, 5)` → `assert!(true)`);
  - a test body emptied but kept;
  - a `return` behind a condition;
  - a test tagged out by a build constraint (`//go:build ignore`) or excluded by test-runner config (`vitest.config` `exclude`, `go test -run`);
  - a test moved between files, which reads as removal.

  If A does not land, the paragraph must also list deleted test files, deleted test declarations, a bare early return, and the fact that owner-verify passes no `--tests`. In that case, also qualify `README.md:29` and `README.md:310`.
- Add one sentence to the `FINDER` prompt (`run/review.py:134`), the review prompt. It should say that a change which deletes a test, returns early from one, or weakens an assertion relaxes a check, and that the finder reports it as an error with a reproducing command that runs the removed test at the base. Pair it with a `.ci/test-review.py` case.
- `.github/PULL_REQUEST_TEMPLATE.md:19` already asks the author to list each test "this change removes, skips or relaxes". It is a self-declaration, not a check, and needs no change.

## Files read

`run/weakening.py`, `run/owner-verify.py:478-502`, `run/review.py:130-200,374-406`, `run/mutation.py` (grep), `run/permissions-diff.py:338-362,474-486`, `README.md:24-29,216-261,286-312,1170-1192`, `.ci/test-weakening.py:1-90`, `.ci/gate-registry.toml:740-830`, `.github/PULL_REQUEST_TEMPLATE.md`, `policy/relaxations.toml` (grep).
