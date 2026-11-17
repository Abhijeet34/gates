# Gates critic: a critic seat inside routed review

**Task:** `gates-critic-routed-seat-crit2`
**Branch:** `fac-remote/gates-issue-9`
**Base:** `main` at `1739f45`

## Goal

Add a critic seat inside routed review so a change that commits a decision
request or study sits it, and its hold folds as a fail.

## What I did

Analysed the existing architecture across `run/critic.py`, `run/review.py`,
`run/gate.py`, the critic lens in `corpus/critic/gates.toml`, the README's
"The critic lens" section, the gate registry `.ci/gate-registry.toml`, and the
test suites `.ci/test-critic.py` and `.ci/test-review.py`.

## Current state

### The standalone critic (`run/critic.py`)

Ships as of the commit the README describes. It:
- reads a decision request file and a TOML registry
- loads the `kind = "lens"` row `critic` from that registry
- seats one reviewer (Sonnet on Opus work, Opus on Sonnet work) via
  `review.seats()`, in a fresh `claude -p` with no tools
- the **gate** (not the seat) re-derives two numbers the request rests on
  and holds when any disagrees beyond tolerance
- records to `records.jsonl` with `gate = "critic"` and
  `head = "sha256:DIGEST"` (a digest of the request file, not a git commit)
- exit: 0 pass, 1 held (fail), 2 error

### The critic as a routed-review lens

The critic is registered as a `kind = "lens"` row in
`corpus/critic/gates.toml`:

```toml
id = "critic"
kind = "lens"
inputs = ["decisions/**", "studies/**"]
```

When a caller's `.github/gates.toml` carries this row, `review.py --class`
already assigns it by rule (via `assign()` at `review.py:362`) when:
- the change touches a path under `decisions/**` or `studies/**`, AND
- the final class is `medium` or `large` (the default `LENS_CLASSES`)

Once assigned, the lens reaches every finder of every review row that sits
(`review.py:1142-1143`), as a yes/no checklist with its signature question,
evidence and stop rule. This is the standard lens mechanism—it adds questions
to the finder's prompt. It does **not** run the critic's own seat with its
number re-derivation logic.

### The gap (README.md:1661)

> No workflow and no firstmate step calls `run/critic.py` yet; a critic seat
> inside the routed review, whose hold would fold as a `fail` on a change
> that commits a request, is the follow-up.

## The design tension

Adding a critic seat inside routed review requires solving one central
constraint: `gate.py`'s docstring (line 29-32) explicitly declares:

> A `lens` row is no gate: it carries a review lens ... that run/review.py
> assigns to the review rows that sit, so `load` returns it only when asked
> for lenses, and it never claims a path, is selected, run or folded.

The `fold()` function (`gate.py:465-502`) iterates over `rows` from
`load(head)`, which explicitly excludes lens rows (`gate.py:186`):
```python
return [r for r in rows if (r["kind"] == "lens") == lenses]
```

`select()` (`gate.py:197-227`) also operates only on non-lens rows. For a
critic hold to fold as `FAIL`, the fold must see the critic's record. Today it
cannot, because lens rows are invisible to fold.

## Implementation paths

### Path A: Register the critic as a `kind = "review"` row in the caller's `.github/gates.toml`

A new row `id = "critic"`, `kind = "review"`, `inputs = ["decisions/**", "studies/**"]` would make fold see it like any other review row. The floor rule at `review.py:505` would seat it when `decisions/**` or `studies/**` are touched.

**Problem:** a `kind = "review"` row is expected to carry `lens`, `reviewers`, `budget` and run the standard finder/verifier/adjudicator pipeline (`review.py:979`). The critic has its own seat logic (one seat, no verifier, number re-derivation by the gate). A review row that calls `review()` would run the wrong pipeline. This path would require either:
- a second review function in `review.py` for the critic, dispatched by some marker on the row, or
- the `sitting()` function to detect a critic row and delegate to `critic.py`'s `critique()`.

**Complexity:** Medium. Requires changes to `review.py` (critic dispatch in `sitting()` or `routed()`), the caller's `.github/gates.toml` (a new review row), and no change to `gate.py`. Fold already handles review rows with records it didn't select (line 477).

### Path B: Extend `fold()` to also fold lens rows that have records

Add the critic seat as a step in `routed()` after the review-row loop. When `"critic" in plan["lenses"]`, run `critic.py`'s `critique()` on each file under `decisions/**` or `studies/**` that the change commits. Record with `gate = "critic"` and `head = git_commit_sha` (not `sha256:DIGEST`).

Then extend fold to include lens rows that have a record for `head`:
```python
# In fold(), replace the skip condition:
if gid not in chosen and not (row["kind"] in ("review", "lens") and gid in latest):
    continue
```

And have `load()` return lens rows alongside gate rows to `fold()` (or call it twice).

**Problem:** This breaks the documented contract that a lens row "never claims a path, is selected, run or folded." It requires changes to `gate.py`'s docstring, `load()`'s usage in `fold()`, and fold's skip condition.

**Complexity:** Medium-high. Touches `gate.py`'s design contract, `review.py`'s `routed()`, and `fold()`.

### Path C: A new `kind` for the critic

Add `kind = "critic"` to `KINDS` and `EXTRA_KEYS` in `gate.py`, with keys from both review and lens: `CRITIC_KEYS = REVIEW_KEYS | LENS_KEYS`. The critic row in the caller's `.github/gates.toml` uses `kind = "critic"`, `inputs = ["decisions/**", "studies/**"]`. `load()` returns critic rows alongside gate rows (like review rows, not like lens rows). `fold()` sees them. `routed()` dispatches the critic pipeline when it encounters a critic-kind row that sits.

**Problem:** A new kind is a schema change that ripples through `gate.py`'s key validation, `load()`'s filter, `review.py`'s routing, `fold()`'s handling, and every test that checks kinds. It is the most invasive option.

**Complexity:** High.

### Path D (recommended): The critic seat as a step in `routed()`, recording under a review row

Keep the critic as a lens for assignment. In `routed()`, after the review-row loop, when the critic lens is assigned:
1. Identify each file under `decisions/**` or `studies/**` that the change adds or modifies (from the `touched()` entries).
2. For each such file, run `critic.py`'s `critique()` logic.
3. Record the verdict under a dedicated `kind = "review"` row `id = "critic-review"` (or simply `id = "critic"` with `kind = "review"`) that the caller registers, with `inputs = ["decisions/**", "studies/**"]`.
4. This row enters fold naturally as a review row. Its `sitting()` call in `routed()` delegates to the critic logic rather than the standard finder/verifier pipeline.

The dispatch would be in `routed()`:
```python
# After the review-row loop, or inside it for the critic row:
if row["id"] == "critic" or lens_id == "critic":
    # Run critique() instead of sitting()
```

Or, more cleanly, the `sitting()` function checks whether the row is the critic and calls `critique()` instead of `review()`.

**Why this is cleanest:**
- No change to `gate.py`'s contract or fold logic.
- The critic's hold folds as `FAIL` through the standard review-row path.
- The critic row is selected by `select()` when `decisions/**` or `studies/**` change.
- `routed()`'s floor rule seats it when those paths are touched.
- The critic-as-lens still assigns its checklist to other finders (dual use: lens for the finder, trigger for the critic seat).

## Specific changes required

### 1. The caller's `.github/gates.toml` (not in this repository)

```toml
[[gate]]
id = "critic"
kind = "review"
lens = "critic"
inputs = ["decisions/**", "studies/**"]
reviewers = ["claude-sonnet-5-5", "claude-opus-5-5"]
```

### 2. `run/review.py`

In `sitting()` (line 1107) or `routed()` (line 1129), detect when the row being run is the critic review row and dispatch to the critic's own logic rather than the standard finder/verifier pipeline.

Concretely, in the `review()` function (line 979) or `sitting()`:
- Check if the row's `lens` is `"critic"`.
- If so, identify the request files the change commits under `decisions/**` or `studies/**`.
- For each, call `critic.critique()` (imported from `run/critic.py`).
- Aggregate: a `fail` from any file is `fail` for the row; `error` if any is error and none is fail; `pass` otherwise.

The record shape stays the same as any review row (`gate = row["id"]`, `head = git_commit`, carrying `review` state), so fold sees it.

### 3. `run/critic.py`

Factor `critique()` to accept either a file path (standalone) or file content extracted from a git diff (routed). The standalone `main()` stays for direct invocation; `critique()` is the importable entry point (it already is, at line 249).

The only change needed: when called from routed review, the record uses the git commit SHA as `head` (not `sha256:DIGEST`), and `gate = row["id"]` rather than `gate = "critic"`. This can be handled by having the caller (`review.py`) write the record with the right fields.

### 4. Tests

- **`.ci/test-review.py`**: Add a case that commits a file under `decisions/` in the fixture, registers the critic review row, and verifies:
  - The critic seat runs (not the standard finder/verifier).
  - A hold from the critic records `fail`.
  - `fold()` sees the critic's fail as `FAIL`.
  - A pass from the critic records `pass` and fold passes.
- **Control:** The mutation target is the dispatch condition (e.g., `if row.get("lens") == "critic":` → `if False:`), which must let the standard pipeline run instead, failing or erroring because the critic row has no standard review config.
- **Gate registry:** A new row in `.ci/gate-registry.toml` for the critic-review gate proof.

### 5. README.md

Update "The critic lens" section to describe the critic seat inside routed review, its record shape and how fold reads it. Remove the sentence at line 1661 about the follow-up.

## What I could not do

This task is a scout delivering a report. I did not implement the changes because:

1. **The dispatch design requires a maintainer decision.** Path D is recommended, but it means a `kind = "review"` row in the caller's `.github/gates.toml` whose `sitting()` call dispatches to `critique()` rather than the standard finder/verifier pipeline. The alternative (Path B: extending fold to handle lens rows) breaks gate.py's documented contract. The maintainer should decide which contract to change.

2. **No recorded critic seat replies exist for the routed path.** The test suite (`test-critic.py`, `test-review.py`) replays real recorded model replies through `claude-recorded.py`. A critic seat inside a routed review would need new recorded fixtures showing the critic being called through the review pipeline, with the git commit as HEAD and the request file extracted from the diff. These require a live run to capture.

3. **The caller's `.github/gates.toml` is not in this repository.** The critic review row would live in each caller repository, not here. This repository ships the scripts and the lens; the row is registered by the caller.

## Recommendation

**Use Path D.** Register the critic as a `kind = "review"` row in the caller, and dispatch to `critique()` from `review.py` when the row's `lens` is `"critic"`. This is the smallest change that makes the hold fold as `FAIL` with no change to `gate.py`'s contract. The critic-as-lens continues to assign its checklist to other finders, so both uses coexist.

The key implementation steps:
1. Add critic dispatch in `review.py`'s `sitting()` or `review()` (5–20 lines).
2. Have `critique()` accept the diff's request files rather than a standalone `--request` path.
3. Write the record under the review row's id with the git commit as head.
4. Test with a fixture that commits a `decisions/` file, registers the critic review row, and replays a recorded critic seat.
5. Update the README and gate registry.

## Evidence

| Finding | Location | Evidence |
|---------|----------|----------|
| Lens rows excluded from fold | `gate.py:186` | `return [r for r in rows if (r["kind"] == "lens") == lenses]` |
| Fold iterates non-lens rows | `gate.py:465-502` | `rows = load(head)` without `lenses=True` |
| Lens contract | `gate.py:29-32` | "A `lens` row is no gate... it never claims a path, is selected, run or folded" |
| Review exception in fold | `gate.py:477` | `gid not in chosen and not (row["kind"] == "review" and gid in latest)` |
| Critic assigns by rule | `review.py:362-371` | `assign()` matches path and class |
| Assigned lenses reach finder | `review.py:1134,1142-1143` | `assigned = [lr for lr in lenses if lr["id"] in plan["lenses"]]` |
| The gap statement | `README.md:1661` | "No workflow and no firstmate step calls `run/critic.py` yet; a critic seat inside the routed review, whose hold would fold as a `fail` on a change that commits a request, is the follow-up." |
| Standalone critic records | `critic.py:386-399` | `gate = LENS`, `head = "sha256:" + hashlib.sha256(data).hexdigest()` |
| Critique entry point | `critic.py:249` | `def critique(text, name, lens, author, store, state, say)` |
| Critic REVIEWERS | `critic.py:57` | `["claude-sonnet-5-5", "claude-opus-5-5"]` |
