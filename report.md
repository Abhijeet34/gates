# Report: Sonnet critic seat false one-sided flags (gates-issue-10)

## Summary

The Sonnet critic seat flags 4 of 12 clean twins as one-sided, above the 10 % bar (at most 1 of 12). The Opus seat flags 1, meeting the bar. All 4 Sonnet false flags are standing omitted-evidence quotes on evidence lines the clean twin's recommendation already addresses. The root cause is a prompt-level ambiguity in what "the recommendation neither mentions nor answers" means: Sonnet reads it too strictly and misses paraphrases.

Two approaches could fix it. A prompt tightening (approach A) is the simplest and addresses the root cause directly. A gate-level overlap check (approach B) is a fallback that works without re-recording but changes the gate's rules.

## What was investigated

1. The critic system: `run/critic.py`, `run/critic-eval.py`, the 24-case corpus under `corpus/critic/cases/`, and their recorded seats under `.ci/fixtures/review/corpus/critic/`.
2. The eval's regression tests: `.ci/test-critic.py` and `.ci/test-critic-eval.py`. Both pass on the current commit (`1739f45`).
3. Each of the 12 Sonnet-on-clean-twin recorded replies, extracting the `omitted` array from each `critic.json` and checking each quote against its `request.md`.
4. The 4 Opus-on-clean-twin recorded replies for the same 4 cases (all have empty `omitted` arrays).
5. The README's measured counts and characterisation (lines 1638-1655).

## The 4 false flags

Each entry gives the case, the line Sonnet claims is omitted, and where the recommendation addresses it.

### 1. finder-model-clean (line 15)

**Omitted quote:** "of 14 seeded cases, the Sonnet and the Opus finder each caught the same 8, case for case."

**Recommendation (line 8):** "On the 14 seeded corpus cases each caught the same 8 seeds, so the cheaper model loses nothing on that set."

**Analysis:** The recommendation directly paraphrases line 15. Moreover, this evidence *supports* the recommendation (switch to Sonnet); it does not cut against it. Sonnet conflates "evidence I used in my counter-case" with "evidence the recommendation omits."

### 2. merge-repos-clean (lines 14, 15)

**Omitted quotes:**
- Line 14: "- A trial merge raised the full CI time from 12 to 31 minutes, because every change built all three clients."
- Line 15: "- With path-scoped builds, the same trial ran a one-client change in 13 minutes."

**Recommendation (line 8):** "The trial merge raised full CI time from 12 to 31 minutes, so the merge ships only with path-scoped builds, which ran a one-client change in 13 minutes in the same trial."

**Analysis:** Line 8 restates both evidence lines almost verbatim: the CI increase (12 to 31 minutes) and the path-scoped mitigation (13 minutes). Both data points appear in the recommendation.

### 3. security-seat-clean (line 14)

**Omitted quote:** "no other seat reported them."

**Recommendation (line 8):** "Those 4 were the month's two credential leaks and two authentication bypasses, which no other seat caught, so the seat stays on sensitive paths and is dropped only on the rest."

**Analysis:** "which no other seat caught" is a direct paraphrase of "no other seat reported them." The recommendation both mentions and addresses this evidence (the seat stays on sensitive paths).

### 4. sharding-study-clean (line 14)

**Omitted quote:** "- Runs that failed or were cancelled were not measured: 4 before the change and 19 after."

**Recommendation (line 8):** "Failed and cancelled runs were not measured and rose from 4 to 19, so the speedup holds for runs that finish, and the failures get their own look before the sharding is called a win."

**Analysis:** The recommendation repeats both numbers (4 and 19) and explicitly plans to address the failures. This is answered, not omitted.

## Root cause

The CRITIC prompt (`run/critic.py:86-87`) instructs:

> omitted: each line of evidence that cuts against the recommendation and that the recommendation neither mentions nor answers; [] when it answers all of them.

Sonnet applies a stricter-than-intended reading:

1. **Paraphrase blindness** (3 of 4 flags: merge-repos, security-seat, sharding-study). The recommendation restates the evidence in one sentence using different words, but Sonnet does not count a paraphrase as "mentions."
2. **Mislabelling supportive evidence** (1 of 4 flags: finder-model). The evidence line supports the recommendation; it does not cut against it. Sonnet conflates evidence it *used* in its counter-case with evidence the recommendation *omitted*.

Opus avoids both errors on 11 of 12 clean twins; its single false flag (`cache-key-study-clean`, line 14: "0 stale restores") is a class-2 error (the evidence is neutral, not contrary).

The README (line 1645) independently confirms the same two classes:

> Of the five false flags, three are Sonnet quoting a contrary line that the clean twin's recommendation answers in one sentence ... and two quote an evidence line no recommendation needs to answer.

## Recommended approaches

### A. Prompt tightening (preferred)

Replace `run/critic.py:86-87` with wording that closes both failure modes:

```
- omitted: each line of evidence that cuts against the recommendation and that the
recommendation neither mentions, paraphrases, summarises, nor answers in any way;
[] when every contrary evidence line is addressed. A line whose data point, number
or concern appears anywhere in the recommendation, even in different words, is not
omitted. An evidence line that supports or is neutral toward the recommendation is
never omitted, even if the counter-case draws on it.
```

Changes from the current text:
- "mentions" expanded to "mentions, paraphrases, summarises, nor answers in any way" (addresses class 1)
- "A line whose data point, number or concern appears anywhere in the recommendation, even in different words, is not omitted" (reinforces class 1)
- "An evidence line that supports or is neutral toward the recommendation is never omitted, even if the counter-case draws on it" (addresses class 2)

**Why it should work:** Opus already applies these rules correctly, so the semantics are within the range a Sonnet-class model can follow. The extra specificity closes the gap Sonnet's reading exploits.

**What it costs:** A re-recorded eval (all 48 seats) to confirm the counts, since the existing recorded replies were produced under the old prompt.

**What it risks:** A prompt that is too restrictive might suppress real one-sided detections (the eval's 6 one-sided cases must still be caught at >= 80 %). The clean twins' recommendations explicitly address their evidence, while the planted one-sided twins' recommendations do not, so the distinction should survive.

### B. Gate-level overlap check (fallback)

If a prompt change alone does not bring Sonnet under the bar, the gate could add a structural check in `run/critic.py` before counting an omitted quote:

```python
def recommendation_overlap(lines, item, rec_end):
    """True when the recommendation section shares a number with the omitted quote."""
    q_nums = {v for v, _ in numbers_on(flat(item.get("quote", "")))}
    rec_nums = set()
    for i in range(rec_end):
        rec_nums.update(v for v, _ in numbers_on(flat(lines[i])))
    return bool(q_nums & rec_nums)
```

Then at `critic.py:320`, filter omitted quotes whose key numbers also appear in the recommendation:

```python
for o in state["omitted"]:
    if "dropped" not in o and recommendation_overlap(lines, o, rec_end):
        o["dropped"] = "its numbers also appear in the recommendation"
```

This requires finding the boundary between the Recommendation and Evidence sections, which is fragile (the requests don't have a stable delimiter beyond the `## Evidence` heading). It also changes what the gate considers one-sided, which is a design decision.

Of the 4 Sonnet false flags, 3 share numbers with the recommendation (merge-repos shares 12, 31, 13; security-seat shares none; sharding-study shares 4, 19). The finder-model flag (line 15) quotes "14 seeded cases" and "8", both of which appear in the recommendation's line 8. So this heuristic would catch 3 of 4, the same 3 the README calls class 1.

**What it costs:** A change to the gate's rules, a new test case in `.ci/test-critic.py` and `.ci/test-critic-eval.py`, and the `MEASURED` counts in the test updated.

**What it risks:** False negatives on real one-sided cases where the recommendation casually mentions a number from the omitted evidence without actually addressing the concern. The planted one-sided cases omit their evidence entirely from the recommendation, so the overlap check should not suppress them, but future corpus cases need to respect this invariant.

### C. Combined (belt and suspenders)

Apply A first, re-record, and if the count is still above 1 of 12, add B. The prompt change is cheap (one re-record); the gate change is a code diff and a test update.

## What could not be done

1. **Re-running the eval with a modified prompt.** This session has no model access (`claude` is not installed, and the eval needs a live Sonnet seat). A re-recorded eval is needed to confirm that any prompt change meets the bar.
2. **Re-running the eval with a gate-level change.** The existing recorded replies would need to be re-scored, but the recorded `critic.json` envelopes contain the seat's raw answer, not the gate's scoring. The gate re-scores on replay, so approach B *can* be tested against the existing recordings by modifying `critic.py` and running `.ci/test-critic-eval.py`. However, the first test (`measured_counts`) pins the current counts, so the pinned counts and the bar expectation would need updating in that test before it can pass.
3. **Verifying the one-sided catch rate after a change.** The 6 planted one-sided cases need a live re-run under the new prompt to confirm they are still caught at >= 80 %.

## Evidence commands

```
# Tests pass on the current commit
cd /home/user/gates && python3 .ci/test-critic-eval.py
# → critic eval tests passed (exit 0)

cd /home/user/gates && python3 .ci/test-critic.py
# → critic tests passed (exit 0)
```

The 4 false-flagged Sonnet responses were read from:
- `.ci/fixtures/review/corpus/critic/finder-model-clean/claude-sonnet-5-5/critic.json`
- `.ci/fixtures/review/corpus/critic/merge-repos-clean/claude-sonnet-5-5/critic.json`
- `.ci/fixtures/review/corpus/critic/security-seat-clean/claude-sonnet-5-5/critic.json`
- `.ci/fixtures/review/corpus/critic/sharding-study-clean/claude-sonnet-5-5/critic.json`

The corresponding `request.md` files show the recommendation addressing the flagged lines:
- `corpus/critic/cases/finder-model-clean/request.md:8` paraphrases line 15
- `corpus/critic/cases/merge-repos-clean/request.md:8` restates lines 14-15
- `corpus/critic/cases/security-seat-clean/request.md:8` paraphrases line 14
- `corpus/critic/cases/sharding-study-clean/request.md:8` restates line 14

The Opus responses on the same 4 cases all have `"omitted": []`.
