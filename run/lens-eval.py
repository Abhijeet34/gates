#!/usr/bin/env python3
"""The role lenses' ablation eval: each lens on specs with one seeded gap, with and without it.

Usage: lens-eval.py --corpus DIR --store DIR [--case NAME]... [--record DIR]

DIR holds `gates.toml`, the lens rows in run/gate.py's registry shape, and
`cases/NAME/`, each a `spec.md` and a `case.toml`: a seeded case gives `lens`
(the lens whose class its gap is), `defect`, `seed = [first, last]`, the lines
of spec.md where the gap sits, and `terms`, words one of which a finding's
claim must name; a clean case gives `clean` and no seed.
README.md, "The role lenses", owns the corpus and the bars.

Each spec is shown to a finder seat as a change adding spec.md, through
run/review.py's own seat, login check, budgets and finder prompt, once per arm:

  checklist   the lens as review.py gives it to a finder, role line included
  bare        the same without the role line
  persona     the role line alone, as a persona
  none        no lens at all

A seeded case sits its own lens in the three lens arms and once with none; a
clean case sits every measured lens (one owning a seeded case) as checklist,
and once with none. An `error` or `warning` finding inside the seed's lines
whose claim names one of its terms catches, so a finding there about something
else does not; any `error` or `warning` finding flags a clean case; a seat that
could not decide is `undecided`, never a catch or a clean pass.
GATES_CORPUS_CASE=lens/NAME is in each seat's environment, so a recorded CLI
can answer for the case; --record DIR copies each seat's envelope and the CLI
version to DIR/NAME/, as run/corpus.py does.

Per measured lens it prints the counts, the bar (at least 80% of its seeds
caught as checklist, at most 10% of clean specs flagged), the ablation verdict
(`keep` when checklist recall beats none by more than the 95% binomial
half-width of the difference, else `cut`) and the role line's (`keep` when
checklist beats bare, else `remove`). The verdicts are reported only; the rows
stay as they are until a larger set decides them.

Exit status: 0 every lens that ran met its bar, 1 a bar missed, else 2 when a
seat was undecided or the corpus is malformed.
"""

import argparse
import math
import os
import sys
import tomllib
from pathlib import Path

sys.dont_write_bytecode = True
import gate  # noqa: E402
import review  # noqa: E402

FINDER = "claude-sonnet-5-5"  # the review rows' finder for Opus-written work
AUTHOR = "claude-opus-5-5"
LENS = "spec"  # the finder's own lens, in every arm
ARMS = ("checklist", "bare", "persona")
FLAGGED = ("error", "warning")
CATCH_BAR, FLAG_BAR, Z = 0.8, 0.1, 1.96
NO_CHECKS = "(no check recorded for this head)"


class Malformed(Exception):
    pass


def read_toml(path):
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Malformed(f"{path.name}: {e}") from e


def corpus_of(root):
    """({lens id: lens}, [(name, case)]), each checked against its shape."""
    rows = read_toml(root / "gates.toml").get("gate", [])
    lenses = {}
    for row in rows if isinstance(rows, list) else [None]:
        if not (isinstance(row, dict) and row.get("kind") == "lens" and row.get("id")):
            raise Malformed(
                'gates.toml: every row must be a `kind = "lens"` row with an id'
            )
        try:
            lenses[row["id"]] = review.lens_of(row, "gates.toml")
        except gate.Undecided as e:
            raise Malformed(str(e)) from e
    if not lenses:
        raise Malformed("gates.toml: no lens row")
    cases = []
    for d in sorted(p for p in (root / "cases").iterdir() if p.is_dir()):
        case = read_toml(d / "case.toml")
        seed = case.get("seed")
        terms = case.get("terms")
        seeded = (
            set(case) == {"lens", "defect", "seed", "terms"}
            and case["lens"] in lenses
            and isinstance(case["defect"], str)
            and isinstance(terms, list)
            and terms
            and all(isinstance(t, str) and t.strip() for t in terms)
            and isinstance(seed, list)
            and len(seed) == 2
            and all(type(n) is int for n in seed)
            and 1 <= seed[0] <= seed[1]
        )
        clean = set(case) == {"clean"} and isinstance(case["clean"], str)
        if not (seeded or clean):
            raise Malformed(
                f"cases/{d.name}/case.toml: lens (a row of gates.toml), defect,"
                " seed = [first, last] and terms, or clean alone"
            )
        try:
            spec = (d / "spec.md").read_text()
        except OSError as e:
            raise Malformed(f"cases/{d.name}: no spec.md") from e
        cases.append((d.name, {**case, "spec": spec}))
    if not cases:
        raise Malformed(f"{root.name}: no case under cases/")
    return lenses, cases


def as_change(spec):
    """The spec as the diff of a change that adds it, which is what a finder reads."""
    lines = spec.splitlines()
    head = f"diff --git a/spec.md b/spec.md\nnew file mode 100644\n--- /dev/null\n+++ b/spec.md\n@@ -0,0 +1,{len(lines)} @@\n"
    return head + "".join(f"+{line}\n" for line in lines)


def seats_of(case, measured):
    """[(lens id or None, arm)] that sit on the case."""
    if "lens" in case:
        return [(case["lens"], arm) for arm in ARMS] + [(None, "none")]
    return [(lid, "checklist") for lid in measured] + [(None, "none")]


def score(case, findings):
    """(outcome, detail): caught, missed, clean or flagged."""
    hits = [f for f in findings if f["severity"] in FLAGGED]
    shown = "; ".join(
        f"{f['id']} {f['severity']} {f['path']}:{f['line']}: {review.printable(f['claim'], 160)}"
        for f in hits
    )
    if "clean" in case:
        return ("flagged", shown) if hits else ("clean", "no error or warning")
    first, last = case["seed"]
    at = [f for f in hits if f["path"] == "spec.md" and first <= f["line"] <= last]
    named = [
        f for f in at if any(t.lower() in f["claim"].lower() for t in case["terms"])
    ]
    if named:
        return "caught", shown
    return (
        "missed",
        f"nothing at spec.md:{first}-{last} naming its gap ({shown or 'no finding'})",
    )


def keep(seats, store, out):
    """Each seat's reply envelope into `out`, as claude-recorded.py reads them."""
    out.mkdir(parents=True, exist_ok=True)
    for seat in seats:
        # A seat that timed out kept no reply; its replay is undecided, as it was live.
        if not str(seat.get("reply", "")).startswith("sha256:"):
            continue
        data = (store / "logs" / f"{seat['reply'][7:]}.log").read_bytes()
        (out / f"{seat['seat'].replace(' ', '-')}.json").write_bytes(data)


def run_case(name, case, lenses, measured, store, record):
    """{(lens id or None, arm): outcome}, printing one line per seat."""
    os.environ["GATES_CORPUS_CASE"] = f"lens/{name}"
    budget = review.BUDGET
    diff, results, sat = as_change(case["spec"]), {}, []
    try:
        version = review.harness()
    except review.Undecided as e:
        version = None
        refusal = str(e)
    for lid, arm in seats_of(case, measured):
        label = f"lens {lid} {arm}" if lid else "lens none"
        if version is None:
            outcome, detail = "undecided", refusal
        else:
            blocks = [review.lens_text(lenses[lid], arm)] if lid else []
            seat = {
                "seat": label,
                "model": FINDER,
                "independence": {"author": review.independence(FINDER, AUTHOR)},
            }
            prompt = review.finder_prompt(
                NO_CHECKS, "The change (git diff base...head)", diff, seat=label
            )
            try:
                answer = review.sit(
                    seat, review.finder_system(LENS, blocks), prompt,
                    budget["finder_seconds"], f"its {budget['finder_seconds']}s budget",
                    budget, store, sat,
                )  # fmt: skip
                outcome, detail = score(case, review.findings_of(answer))
            except review.Undecided as e:
                outcome, detail = "undecided", str(e)
        results[(lid, arm)] = outcome
        print(f"lens-eval: {name}: {label[5:]}: {outcome}: {detail}", flush=True)
    if record and version is not None:
        keep(sat, store, record / name)
        (record / name / "version.txt").write_text(version + "\n")
    return results


def half_width(a, b, n):
    """The 95% normal half-width of a difference of two recalls of n, from their pooled rate."""
    p = (a + b) / (2 * n)
    return Z * math.sqrt(2 * p * (1 - p) / n)


def verdicts(lenses, cases, results):
    """Print each lens's counts, bar and ablation verdicts; True when every bar ran is met."""
    met = True
    for lid in lenses:
        seeded = [n for n, c in cases if c.get("lens") == lid]
        clean = [
            n for n, c in cases if "clean" in c and (lid, "checklist") in results[n]
        ]
        if not seeded and not clean:
            print(f"lens-eval: {lid}: unmeasured: no seeded case in this set")
            continue

        def count(names, key, outcome):
            return sum(results[n].get(key) == outcome for n in names)

        arms = {arm: count(seeded, (lid, arm), "caught") for arm in ARMS}
        arms["none"] = count(seeded, (None, "none"), "caught")
        flags = count(clean, (lid, "checklist"), "flagged")
        base = count(clean, (None, "none"), "flagged")
        k, m = len(seeded), len(clean)
        print(
            f"lens-eval: {lid}: seeded caught: checklist {arms['checklist']} of {k},"
            f" bare {arms['bare']}, persona {arms['persona']}, none {arms['none']};"
            f" clean flagged: checklist {flags} of {m}, none {base}"
        )
        missed = []
        if k and arms["checklist"] < CATCH_BAR * k:
            missed.append(f"catches {arms['checklist']} of {k}, under {CATCH_BAR:.0%}")
        if m and flags > FLAG_BAR * m:
            missed.append(f"flags {flags} of {m}, over {FLAG_BAR:.0%}")
        met &= not missed
        print(
            f"lens-eval: {lid}: bar {'missed: ' + '; '.join(missed) if missed else 'met'}"
        )
        if not k:
            continue
        gain = (arms["checklist"] - arms["none"]) / k
        hw = half_width(arms["checklist"], arms["none"], k)
        print(
            f"lens-eval: {lid}: ablation {'keep' if gain > hw else 'cut'}:"
            f" checklist beats none by {gain:.2f}, half-width {hw:.2f}"
        )
        role = "keep" if arms["checklist"] > arms["bare"] else "remove"
        print(
            f"lens-eval: {lid}: role line {role}: checklist {arms['checklist']} of {k}"
            f" against bare {arms['bare']}"
        )
    return met


def main():
    ap = argparse.ArgumentParser(
        description="Run the role lenses' ablation eval on its seeded specs."
    )
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--store", required=True, type=Path)
    ap.add_argument("--case", action="append", help="run only this case; repeatable")
    ap.add_argument("--record", type=Path, help="copy each seat's reply here, per case")
    args = ap.parse_args()
    store = args.store.resolve()
    try:
        lenses, cases = corpus_of(args.corpus.resolve())
        measured = [
            lid for lid in lenses if any(c.get("lens") == lid for _, c in cases)
        ]
        if args.case:
            unknown = sorted(set(args.case) - {n for n, _ in cases})
            if unknown:
                raise Malformed(f"no case {', '.join(unknown)}")
            cases = [(n, c) for n, c in cases if n in args.case]
        results = {
            name: run_case(
                name,
                case,
                lenses,
                measured,
                store,
                args.record and args.record.resolve(),
            )
            for name, case in cases
        }
    except Malformed as e:
        print(f"lens-eval: undecided: {e}")
        return 2
    met = verdicts(lenses, cases, results)
    undecided = sum(o == "undecided" for r in results.values() for o in r.values())
    print(f"lens-eval: {len(cases)} cases, {undecided} undecided seats")
    if not met:
        return 1
    return 2 if undecided else 0


if __name__ == "__main__":
    sys.exit(main())
