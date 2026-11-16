# Scout report — `gates-review-row-l8b`: the factory review row in gates' own registry

**Task:** `gates-issue-14` (P6, second half). Establish whether `gates`' own
`run/gate.py` supports a `kind = "review"` row, and determine how to add the
factory's review row to gates' own registry (`.github/gates.toml`).

**Deliverable:** this report. No registry change is included — the task is a
scout, and a scout branch's only read artefact is `report.md`.

**Base:** `main` at `1739f455` (`feat(weakening): add workflow-condition records
for factory's ledger (#123)`).

---

## TL;DR

1. **Yes — `run/gate.py` fully supports a review row.** `review` is a first-class
   `kind`; the loader validates it, accepts the `lens`/`reviewers`/`budget` keys
   on it (and only on it), the runner skips its `run` and records nothing, and
   the fold reads its verdict record like any other and never folds an absent or
   `error` review to a pass. This is not just code-present but **test-proven**:
   `.ci/test-gate-runner.py` has four review-row cases and passes.

2. **The factory review row is the canonical `code-review` row** shipped verbatim
   in the README example (`README.md:1293`) and the review fixture
   (`.ci/fixtures/review/build.sh:22`):

   ```toml
   [[gate]]
   id = "code-review"
   kind = "review"
   inputs = ["**"]
   lens = "code"
   reviewers = ["claude-sonnet-5-5", "claude-opus-5-5"]
   shadow_since = <adoption date>
   ```

3. **Adding it to gates' own `.github/gates.toml` is safe and mechanically
   trivial** — append the four-field row. It trips no other gate (permissions-diff
   lets *new* rows pass; nothing pins the real registry's row set).

4. **But it is inert until the maintainer's weekly batch wires `run/review.py`
   into a workflow.** Nothing today produces a review verdict record for gates.
   With `shadow_since` set, the row sits in shadow reporting `no verdict for this
   head` on every PR (a non-gating warning). The row cannot ever block or ever
   pass on its own until review.py runs somewhere and lands ~50 fresh records
   over 14 days. **`shadow_since` is mandatory**: without it the row is
   "established" and the fold *refuses* for want of a verdict (demonstrated below).

**Recommendation:** add the canonical row with `shadow_since` set to the adoption
date (I suggest `2026-11-16`, matching how gates dated its own three rows to the
day they were added, `.github/gates.toml:10,16,22`). Treat it as inert-by-design:
it exercises the registry/selector/fold path for a review row but produces no
verdict until review.py is wired. Do **not** omit `shadow_since`, and do not
expect any signal beyond a shadow warning until the weekly batch.

---

## What "gates' own registry" is

Two registries exist and must not be confused (`AGENTS.md`, the `run/gate.py`
entry):

- **`.github/gates.toml`** — the *gate runner's* registry, "gates' own", run in
  shadow by `gate-shadow.yml`. **This is the target of this task.** It currently
  holds three `check` rows only — `workflow-policy`, `checks-verdict`,
  `gate-runner` (`.github/gates.toml:6-22`) — described in its header as "the
  slice of ci.yml's `shell` job that needs only what a stock runner holds (bash,
  git, python3); the rest waits for a way to install tools."
- **`.ci/gate-registry.toml`** — the *proof* registry that proves the gate files
  this repo ships (gates-proof). A review row in `.github/gates.toml` is config
  data, not a shipped gate file, so **no `.ci/gate-registry.toml` row is
  required** for it (`AGENTS.md`: that rule is for "a workflow, hook, `run/` file
  or `.ci` script").

---

## Finding 1 — `run/gate.py` supports review rows (established)

### Static evidence (`run/gate.py`)

- `KINDS = {"check", "review", "docs", "lens"}` — `run/gate.py:89`.
- `REVIEW_KEYS = {"lens", "reviewers", "budget"}` and
  `EXTRA_KEYS = {"review": REVIEW_KEYS, "lens": LENS_KEYS}` — `run/gate.py:86,88`.
  The loader allows these keys on a `review` row and refuses them as "unknown
  key" on any other row (`run/gate.py:146-149`).
- A non-`check` row must have **no** `run`: `run/gate.py:164-166` —
  `"a {kind} row's verdict is recorded, so it has no run"`.
- The runner **skips** a review row's execution and writes no record —
  `run/gate.py:566-567`: `await {row['id']}: its {row['kind']} verdict is
  recorded`.
- The fold reads a review verdict like any other and **folds a review row that
  sat even when selection left it out** — `run/gate.py:476-477` — so a block the
  router convened is never dropped. An absent/`fail`/`error` record never folds
  to pass (`run/gate.py:481-499`).
- The module docstring documents all of the above explicitly —
  `run/gate.py:22-27`.

### Test evidence — `.ci/test-gate-runner.py` (the `gate-runner` gate's own suite)

Ran the suite on this base:

```
$ python3 .ci/test-gate-runner.py
...
ok   absent_review_refuses
ok   errored_doc_refuses
ok   sat_review_folds_unselected
ok   review_keys_only_on_review_rows
...
gate runner tests passed
EXIT=0
```

Those four cases are exactly the review-row contract: a review row with no record
refuses the fold; an `error` review record refuses; a review that sat folds
though unselected; and `lens`/`reviewers`/`budget` are accepted on a review row
and refused on a check row. The fixture these cases use (`.ci/test-gate-runner.py:535-548`)
is itself a review row, and `review_keys_only_on_review_rows`
(`.ci/test-gate-runner.py:623`) uses the exact `lens = "code"` / `reviewers` /
`budget` keys.

### Loader accepts the proposed combined registry

I validated the current three rows **plus** the factory review row against
`run/gate.py`'s per-row rules (`ROW_KEYS`, `EXTRA_KEYS`, kind/run checks):

```
workflow-policy  kind=check   problems=[]
checks-verdict   kind=check   problems=[]
gate-runner      kind=check   problems=[]
code-review      kind=review  problems=[]
VALIDATION_OK
```

**Conclusion:** the first half of the task is settled — review rows are supported,
documented and tested.

---

## Finding 2 — what the factory review row is

The factory's review row is the `code-review` row, reproduced identically in two
authoritative places:

- `README.md:1293-1301` ("The review rows").
- `.ci/fixtures/review/build.sh:22-30` (the review fixture's committed registry).

```toml
[[gate]]
id = "code-review"
kind = "review"
inputs = ["**"]
lens = "code"
reviewers = ["claude-sonnet-5-5", "claude-opus-5-5"]
shadow_since = 2026-10-19
```

Notes on the fields (sources: `run/review.py:1-74`, `README.md:1285-1402`):

- **`lens = "code"` needs no lens row.** `run/review.py`'s `config()` only
  requires `lens` to be a non-empty string (`run/review.py:313-315`); it is used
  as a label in the finder's system prompt (`"You are the {lens} reviewer"`,
  `run/review.py:134`). It is **not** a reference to a `kind = "lens"` row — those
  are a separate mechanism assigned by `--class` routing (`run/review.py:58-63`,
  `README.md:1491` "a lens row is no gate"). So gates does **not** need to also
  ship a `code` lens row.
- **`reviewers`** are model ids in preference order; the finder is the first of
  another family than the change's author, the verifier the first of another
  family than the finder (`README.md:1305-1308`).
- **`budget`** is optional and omitted by the canonical row (defaults in
  `run/review.py`, `README.md:1319-1324`).
- **`inputs = ["**"]`** means the row is selected by every change.

---

## Finding 3 — adding the row to gates' own registry is safe

### It trips no other gate

- **permissions-diff** watches `.github/gates.toml` as "the gate registry" with
  all rows protected (`run/permissions-diff.py:468`, `rows(lambda row: True)`),
  **but only for rows deleted or edited** — its `rows()` picker iterates the
  *old* version's rows (`run/permissions-diff.py:106-118`). A **new** row produces
  no finding; `README.md:180` states this explicitly: "an existing row deleted or
  any of its fields changed; **a new row passes**." Appending `code-review`
  touches none of the three existing rows, so permissions-diff is silent.
  (Separately, permissions-diff is not even in gates' own CI today — there is no
  `permissions-diff` row in `.github/gates.toml` and no workflow runs it against
  gates itself; it is proven only via gates-proof fixtures.)
- **Nothing pins the real repo-root `.github/gates.toml` row set.** Every
  `.github/gates.toml` reference under `.ci/` is a fixture/in-test construction
  (e.g. `.ci/test-gate-runner.py:50`, `.ci/test-permissions-diff.py:188`,
  `.ci/test-lens-eval.py:327` which reads `corpus/lens/gates.toml`, not the
  runner registry). `run/docs-truth.py` and `run/instruction-truth/` make no
  assertion about the runner registry's contents. So adding a row breaks no
  content-pinning test.
- **gates-proof** (`.ci/check.sh gates-proof`) proves gate *files* against
  `.ci/gate-registry.toml`; a data row in `.github/gates.toml` adds no file and
  needs no proof row.

### Behaviour in the shadow job (the real consequence)

gates runs its own registry through `shared-gate-shadow.yml` →
`run/gate-shadow.sh`, which is **non-gating** three ways over
(`README.md:1727-1731`): the script exits 0 regardless, the runner step is
`continue-on-error`, and the shadow job is kept out of `checks.needs`.

With the row added (and `shadow_since` set to today), I reproduced the exact
shadow behaviour in a throwaway repo (a `selfcheck` check row + the `code-review`
review row):

```
=== select ===
selfcheck	file.txt
code-review	file.txt
=== run ===
pass selfcheck
await code-review: its review verdict is recorded      # <- no record written
=== records written ===
selfcheck pass                                          # <- only the check
=== fold ===
pass selfcheck
shadow code-review: no verdict for this head (in shadow since 2026-11-16: 0 of 50 runs, 0 of 14 days)
fold: 2 selected at f4635cf5…, every blocking gate passed
fold exit=0
```

So `run/gate-shadow.sh` would classify this as `verdict=shadow-red` (its rule:
a `shadow ` line in `fold.txt`, `run/gate-shadow.sh:35-41`) and emit a non-gating
`::warning title=gate shadow::verdict=shadow-red`. **Every gates PR would carry
that warning** until review.py is wired. It cannot turn `checks` red.

---

## Finding 4 — the blocker: review.py is not wired, and `shadow_since` is mandatory

### No verdict is produced today

`run/review.py` is the only thing that writes a review verdict record, and
**no workflow calls it** — stated twice: `README.md:1402` ("No workflow calls
`run/review.py`; wiring it beside the runner waits for the maintainer's weekly
batch") and `README.md:1583`. The shadow job only runs `gate.py run` (which
`await`s the review row) and `gate.py fold` (which finds no record). Therefore:

- the row can **never pass** on its own (no record → `no verdict for this head`);
- the row can **never accumulate** the 50 fresh `pass`/`fail` records over 14
  days that promotion needs (`run/gate.py:92,354-381`), because those come from
  real review.py runs — so it is **permanently in shadow** until review.py is
  wired. This is safe, but it means the row does no review work yet.

### `shadow_since` must be present

A review row with **no** `shadow_since` and no standing event is "established"
and **blocks** (`run/gate.py:365-366` returns `(True, None)` when there is no
window). Since nothing writes its verdict, the fold then *refuses*. Reproduced:

```
=== fold with NO shadow_since on the review row ===
pass selfcheck
FAIL code-review: no verdict for this head
fold: 2 selected at 0a91ca8f…, refused
fold exit=1
```

In the shadow job this is only a `verdict=refused` warning (still non-gating),
but if `gate.py fold` is ever used as a *blocking* gate with this registry, an
un-dated review row would hard-block every change for want of a verdict pipeline.
So the row **must** carry `shadow_since`.

---

## Recommendation

Add the factory review row to `.github/gates.toml`, appended after the existing
three rows, with `shadow_since` set to the adoption date:

```toml
[[gate]]
id = "code-review"
kind = "review"
inputs = ["**"]
lens = "code"
reviewers = ["claude-sonnet-5-5", "claude-opus-5-5"]
shadow_since = 2026-11-16
```

Rationale for the fields:

- Keep `id`, `kind`, `inputs`, `lens`, `reviewers` **verbatim** from the factory
  canonical row (`README.md:1293`, `.ci/fixtures/review/build.sh:22`) — that is
  "the factory review row".
- Set `shadow_since` to the day the row is added, matching gates' own convention
  (`.github/gates.toml:10,16,22` all date their row to adoption day), rather than
  copying the factory's `2026-10-19`. It is mandatory (Finding 4) and the exact
  date only affects when the 14-day clock *starts*; promotion is still gated on
  50 fresh records that will not exist until review.py is wired, so any date is
  safe. If the maintainer prefers to align the clock with the weekly-batch
  wiring, set it to the batch date instead.
- Omit `budget` (the canonical row does; defaults apply).

This is a one-row append. It passes the loader, passes `select`, is `await`ed by
`run`, and folds as `shadow` — exactly as demonstrated. No companion change is
needed (no lens row, no `.ci/gate-registry.toml` row, no workflow edit for the
row itself to be valid).

### What this does and does not achieve

- **Does:** registers the review row so the runner exercises the review-row
  selection/`await`/fold path on gates' own changes, and makes gates "the first
  caller" carry the review row the other repos will copy.
- **Does not:** run any review. Until the maintainer's weekly batch wires
  `run/review.py` into a workflow that writes verdict records into the shadow
  store, the row sits at `shadow … no verdict for this head` and emits a
  non-gating warning on every PR.

### Open items I could not do (scout scope)

- **Wiring `run/review.py` into a workflow** is out of scope here and is
  explicitly reserved for "the maintainer's weekly batch" (`README.md:1402,
  1747-1748`). It also needs the review harness preconditions — `claude -p`
  logged in on `firstParty` subscription, a sandbox (`bwrap`) for reproductions
  (`README.md:1342-1345, 1339-1340`) — none of which the stock shadow runner
  provides (`README.md:1738-1739`). Recommend a separate ship task for that,
  sequenced after this row lands.
- **Choice of `shadow_since` date** is a maintainer call (see above); I recommend
  the adoption date but flag the batch-date alternative.
- I did not modify `.github/gates.toml` on this branch, by design: a scout branch
  discards every change but `report.md`. The exact row to add is given above.

---

## Evidence index (file:line)

- Review kind/keys/validation: `run/gate.py:85-89, 146-166, 476-477, 566-567`;
  docstring `run/gate.py:22-27`.
- Promotion / `shadow_since` logic: `run/gate.py:92, 354-381`.
- Review pipeline contract: `run/review.py:1-74`; `lens` is a label
  `run/review.py:313-315, 134`; lens rows separate `run/review.py:58-63`.
- Factory review row (canonical): `README.md:1293-1301`;
  `.ci/fixtures/review/build.sh:22-30`.
- Review-row tests: `.ci/test-gate-runner.py:535-548` (fixture), and the four
  cases at `557` (`absent_review_refuses`), `574` (`errored_doc_refuses`), `595`
  (`sat_review_folds_unselected`), `615` (`review_keys_only_on_review_rows`),
  with the `lens="code"` keys at `617`.
- gates' current registry: `.github/gates.toml:1-23`.
- Shadow job (non-gating) + verdict classing: `run/gate-shadow.sh:24-52`;
  `.github/workflows/gate-shadow.yml`; `.github/workflows/shared-gate-shadow.yml`;
  `README.md:1724-1748`.
- permissions-diff lets new rows pass: `run/permissions-diff.py:94-120, 468`;
  `README.md:180, 192`.
- review.py not wired: `README.md:1402, 1583, 1747-1748`.
