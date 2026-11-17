# Gates lens eval: larger seed set scout

Task: `gates-lens-eval-larger-seeds-rl2`
Run: `r20261117T002821-50792`
Branch: `fac-remote/gates-issue-8`

## Summary

The 13 role lenses shipped in shadow are scored on 2-6 seeded specs per class (5 product lenses are entirely unmeasured). At these sample sizes the two-proportion 95% half-width is too wide for the ablation verdict to separate checklist from none for any realistic gain below 0.80. Growing the seed set to n=12 per lens would narrow the half-width to ~0.40, enough to detect a recall gain of 0.40 or more. A separate structural problem—the finder's baseline false-positive rate of 45%—makes the 10% flag bar unpassable by every lens regardless of seed count, and must be addressed independently.

## What I did

1. Read `run/lens-eval.py`, `corpus/lens/gates.toml`, every `corpus/lens/cases/*/case.toml`, the README's "The role lenses" section (lines 1466-1583), and `.ci/test-lens-eval.py` to understand the eval's shape, scoring, bars, and ablation formula.
2. Tallied seeded and clean cases per lens from the 48 existing cases.
3. Computed the half-width `Z * sqrt(2p(1-p)/n)` (Z=1.96) at various n values and realistic observed rates to determine the minimum n for a meaningful ablation verdict.
4. Analyzed the product-stakeholder false-positive flip and the tester lens structural mismatch.
5. Estimated the number of new specs and evaluation seats needed at target n values.

## Findings

### 1. Current seed counts

| Lens | Seeds | Clean | Gap class |
|------|-------|-------|-----------|
| `operations` | 6 | 22 | no rollback (3), no cleanup step (3) |
| `open-alternative` | 6 | 22 | name/mark (2), screen/layout (2), icons (1), interop (1) |
| `product-stakeholder` | 3 | 22 | no out-of-scope list (3) |
| `architect` | 3 | 22 | irreversible migration, no dry run (3) |
| `security` | 3 | 22 | no auth check (3) |
| `tester` | 3 | 22 | no acceptance command (3) |
| `designer` | 2 | 22 | no empty/loading/error state (2) |
| `product-analyst` | 0 | — | unmeasured |
| `product-developer` | 0 | — | unmeasured |
| `product-designer` | 0 | — | unmeasured |
| `product-customer` | 0 | — | unmeasured |
| `product-tester` | 0 | — | unmeasured |
| `product-support` | 0 | — | unmeasured |

Source: `corpus/lens/cases/*/case.toml`, each `lens` field mapped against `corpus/lens/gates.toml`.

### 2. The half-width is too wide at current n

The ablation verdict is `keep` when gain > hw, where:
```
hw = 1.96 * sqrt(2 * p * (1-p) / n)    (p = pooled rate)
```

At worst-case p=0.5 (maximum variance):

| n | Half-width | Can detect gain >= |
|---|------------|--------------------|
| 2 | 0.98 | nothing useful |
| 3 | 0.80 | only 100% vs 0% |
| 6 | 0.57 | only gains >= 0.60 |
| 8 | 0.49 | only gains >= 0.50 |
| 10 | 0.44 | gains >= 0.45 |
| 12 | 0.40 | gains >= 0.40 |
| 15 | 0.36 | gains >= 0.36 |
| 22 | 0.30 | gains >= 0.30 |

Evidence: `run/lens-eval.py:209-212` (the `half_width` function), computed directly:
```
n= 3: 1.96 * sqrt(0.5/3) = 0.800
n=12: 1.96 * sqrt(0.5/12) = 0.400
n=22: 1.96 * sqrt(0.5/22) = 0.295
```

At n=3, even the strongest possible result (checklist catches 3/3, none catches 0/3, gain=1.00) barely exceeds hw=0.80. The designer lens at n=2 has hw=0.98—its "keep" verdict is essentially noise. The README acknowledges this: "at 2 to 6 seeds a lens they are noise, so all thirteen rows stay in shadow and nothing is cut until a larger set decides" (line 1531).

### 3. Minimum n for meaningful verdicts

For the half-width to separate a gain of d (at worst-case p=0.5):
```
n >= 0.5 / (d / 1.96)^2
```

| Detectable gain | Minimum n |
|-----------------|-----------|
| 0.50 | 8 |
| 0.40 | 13 |
| 0.35 | 16 |
| 0.30 | 22 |
| 0.25 | 31 |
| 0.20 | 49 |

Using the observed rates from the measured run (`run/lens-eval.py` 2026-10-20/21, README line 1542):

| Lens | Observed gain | n needed at observed rates |
|------|---------------|---------------------------|
| product-stakeholder | 1.00 | 2 |
| security | 1.00 | 2 |
| tester | 1.00 | 2 |
| designer | 1.00 | 2 |
| operations | 0.67 | 5 |
| architect | 0.33 | 18 |
| open-alternative | 0.17 | 22 (at p=0.5) |

But these "observed" rates are themselves unreliable at n=2-6. The safe target is n=12 (hw<0.40) for all measured lenses, which catches any gain above 0.40 regardless of the true rates.

### 4. product-stakeholder's false-positive rate

The task evidence says adding 2 clean twins moved product-stakeholder from "bar met" to "missed". The data confirms this:

- **Before** (20 clean specs): 2/20 flagged = 10.0% — exactly at the bar, met
- **After** (22 clean specs): 4/22 flagged = 18.2% — over the bar, missed

Source: README lines 1554-1567, `.ci/test-lens-eval.py` MEASURED lines 37-38.

The flip came from two clean specs alone (`open-alt-complete-clean`, `open-alt-spreadsheet-clean`), not from any lens change. This shows the flag bar is fragile at current clean-spec counts: a 10% rate at n=20 is 2 flags; at n=22, the same 2 flags is 9.1% but the actual count jumped to 4, suggesting the true false-positive rate was always higher than the 2/20 reading indicated.

At the target n=12 seeds per lens, with ~one clean twin per new seed, the clean set would grow from 22 to roughly 100+. At that scale, the flag bar's integer-threshold problem vanishes (10% of 100 = 10 allowed flags), but the absolute flag rate matters more:
- If product-stakeholder's true flag rate is ~18%, it fails 10% at any clean-set size.
- If the true rate is ~10%, it passes at large n but is borderline.

The first thing the larger eval should measure is product-stakeholder's checklist-arm flag rate on a bigger clean set, as the task requires.

### 5. The flag bar is structurally unpassable

No lens meets the 10% flag bar now. The root cause is the finder itself: **with no lens at all**, it flags 10 of 22 clean specs (45%). Adding a lens on top can only add flags, never remove them (the lens is additional instructions, not a filter).

Current flag rates (checklist arm / no-lens baseline):

| Lens | Checklist flags | None flags | Clean count |
|------|----------------|------------|-------------|
| `tester` | 21 (95%) | 10 (45%) | 22 |
| `architect` | 12 (55%) | 10 (45%) | 22 |
| `operations` | 7 (32%) | 10 (45%) | 22 |
| `security` | 5 (23%) | 10 (45%) | 22 |
| `designer` | 5 (23%) | 10 (45%) | 22 |
| `product-stakeholder` | 4 (18%) | 10 (45%) | 22 |
| `open-alternative` | 3 (14%) | 10 (45%) | 22 |

Source: README line 1544-1552, `.ci/test-lens-eval.py` MEASURED lines 37-64.

Growing the seed set addresses the ablation half-width but does not fix the flag bar. To make the flag bar achievable, one of these must change:
- **Raise the bar**: e.g. 25% or 30%, reflecting the finder's inherent false-alarm rate.
- **Measure the flag rate relative to baseline**: e.g. the lens must not flag more than the finder alone would (a delta bar).
- **Tune the finder's stop rule**: the `stop` field on each lens already says "Report only gaps that affect correctness or the stated requirements; a preference is not a finding" but the finder still flags clean specs.

### 6. The tester lens is structurally mismatched

The `tester` lens flags 21 of 22 clean specs (95%). The README explains why (line 1572):

> `tester` flags 21 of 22 because its second question, whether a check fails before the change and passes after, cannot be answered on a spec whose tests do not exist yet; it fits a code change, not a spec.

This is not a sample-size problem. The tester lens's checklist item "Would that check fail before the change and pass after it?" is unanswerable on a spec, so the finder flags every spec that describes acceptance criteria. More seeds will not change this. The tester lens either needs its checklist rewritten for specs, or it should only be assigned to code changes (not specs), or it should be cut from the spec eval entirely.

### 7. Unmeasured product lenses

Five product lenses have zero seeded cases: `product-analyst`, `product-developer`, `product-designer`, `product-customer`, `product-support`. They are reported as "unmeasured" by `lens-eval.py` (line 248-249) and confirmed at README line 1532.

Each lens has 2-3 checklist items (source: `corpus/lens/gates.toml` lines 10-107), suggesting 2-3 distinct gap classes per lens:

| Lens | Checklist items | Potential gap classes |
|------|-----------------|---------------------|
| `product-analyst` | 3 | missing problem owner, missing current-cost analysis, missing outcome description |
| `product-developer` | 3 | unnamed dependency, unstated foreclosed choices, infeasible plan |
| `product-designer` | 3 | no approved design reference, no first-run description, unspecified gaps unnamed |
| `product-customer` | 3 | no falsifiable observation, no churn signal, judgement-based pricing claim |
| `product-support` | 3 | no bug-reporting route, no actionable artifact, no retirement plan |

Writing 12 seeded specs per unmeasured lens (across its gap classes) requires 60 new specs + clean twins, the largest single block of work.

### 8. What needs to happen (and what I could not do)

**What I could not do**: Run `lens-eval.py`. It calls `review.sit()` which invokes the Claude CLI through a subscription login with finder model `claude-sonnet-5-5` (`run/lens-eval.py:53,193-197`). This requires the CLI binary, the subscription, and API access, none of which are available in this session. The existing measured counts come from 2026-10-20/21 live runs replayed through `.ci/fixtures/review/corpus/lens/`.

**What needs to happen, in order:**

1. **Write the seeded specs.** For each of the 13 lenses, write gap specs (a `spec.md` with one planted defect and a `case.toml` naming the lens, defect, seed lines and terms) and their clean twins. Follow the existing pattern: each gap spec is a realistic product or engineering spec with exactly one gap in the lens's domain, and the clean twin adds the missing section. Target n=12 per lens minimum. This is ~130 new seeded specs + ~130 new clean twins. See `corpus/lens/cases/drop-column-gap/` and its twin for the established pattern.

2. **Re-run `lens-eval.py` on the full set.** With ~260 new cases, this means ~2750 finder seats at ~$0.03-0.10 each (Sonnet 5.5 on a ~50-line spec), so roughly $80-275 for a full run. Use `--record DIR` to capture seat replies for replay.

3. **Read the ablation verdicts.** At n=12, the half-width is ~0.40, so:
   - A lens whose checklist catches ≥70% and none catches ≤30% (gain ≥0.40) → `keep`
   - A lens whose gain is below 0.40 → `cut`
   - `open-alternative` at observed gain 0.17 → still `cut` unless a larger set shows a bigger gain

4. **Address the flag bar separately.** Growing seeds does not fix the flag bar. Options: raise the bar, measure delta over baseline, or tune the finder. This is a design decision, not a seed-count problem.

5. **Address tester separately.** Its 95% false-positive rate on specs is structural. Either rewrite its checklist for spec review or restrict it to code-change evaluation.

## Recommendation

**Target n=12 seeded specs per lens** (hw<0.40, detects gains ≥0.40). This requires 130 new gap specs + clean twins, costing ~2750 seats to evaluate. Write the five unmeasured product lenses' specs first (60 new), since they have zero data today. Then grow the seven measured lenses from their current 2-6 to 12 each (70 new).

**Measure product-stakeholder's flag rate first.** Its flip from met to missed on 2 added clean specs is the evidence the task cites. A larger clean set (say 50+ clean specs, available once the first batch of new seeds has twins) will show whether its true rate is ~10% (borderline pass) or ~18% (clear fail). That reading gates whether to keep or cut it.

**Do not cut or promote on the current data.** At n=2-6 the ablation verdicts are noise, as the README states. The only confident current readings are:
- `open-alternative`'s gain (0.17 at n=6) is likely too small to survive a larger set → probable cut, but the unmarked-interop check is load-bearing (the one gap the finder misses, per the check-removal experiment at README line 1539).
- `tester`'s 95% false-positive rate is structural, not statistical → needs redesign, not more seeds.

**Decouple the flag bar from the seed set.** The flag bar's failure is about the finder's baseline, not the lenses. Growing seeds makes the ablation tighter but cannot fix a 45% baseline false-positive rate. Address this as a separate design decision (raise bar, delta measurement, or finder tuning) before promoting any lens out of shadow.

## What is blocking

- **No CLI / subscription access** in this session to run `lens-eval.py`. The eval requires the Claude CLI with a subscription login (`run/lens-eval.py:174`, `run/review.py`'s `harness()` and `sit()` functions).
- **Writing 130+ realistic specs** is the main labor. Each needs a plausible product or engineering scenario, one precisely planted gap, correct seed line numbers, and specific match terms. This is not automatable without review—a seeded spec whose gap is too obvious or too subtle skews the eval.
- **The flag bar design decision** is upstream of any lens promotion. Without resolving whether the bar is 10%, a delta, or something else, no lens can pass even at n=1000.
