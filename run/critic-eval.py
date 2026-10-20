#!/usr/bin/env python3
"""The critic lens's eval: decision requests with a planted wrong number or a planted
one-sided case, and their clean twins, each sat by both critic seats.

Usage: critic-eval.py --corpus DIR --store DIR [--case NAME]... [--record DIR]

DIR holds `gates.toml`, with the `critic` lens row run/critic.py reads, and
`cases/NAME/`, each a `request.md` and a `case.toml`: a planted case gives
`plant` (`number` or `one-sided`), `defect`, and `seed = [first, last]`, the
lines of request.md where the wrong number is stated or the evidence the
recommendation leaves out sits; a clean case gives `clean` and no seed.
README.md, "The critic lens", owns the corpus and the bars.

Each case runs through run/critic.py once per seat in SEATS, written as the
other model, so each critic sits on a model other than the author's.
GATES_CORPUS_CASE=critic/NAME/MODEL is in its environment, so a recorded CLI
can answer for the case and seat; --record DIR copies each seat's envelope and
the CLI version to DIR/NAME/MODEL/.

A `number` case is caught when the request is held on a number stated inside
the seed; a `one-sided` case when an omitted-evidence quote that stands sits
inside the seed. Any hold on a case whose numbers are right (a one-sided case
or a clean twin) is a false hold, and an omitted-evidence quote on a clean twin
is a false one-sided flag. A seat that could not decide is `undecided`, never a
catch or a clean pass.

Per seat it prints the counts and the bar: at least 80% of the planted numbers
held and of the one-sided cases caught, at most 10% false holds and at most 10%
false one-sided flags.

Exit status: 0 every seat met its bar, 1 a bar missed, else 2 when a seat was
undecided or the corpus is malformed.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
# (author, critic): each critic reads requests written by the other model.
SEATS = (
    ("claude-opus-5-5", "claude-sonnet-5-5"),
    ("claude-sonnet-5-5", "claude-opus-5-5"),
)
PLANTS = ("number", "one-sided")
CATCH_BAR, FALSE_BAR = 0.8, 0.1
# critic.py's own budget bounds a seat near 5 minutes; this only stops a hang.
CASE_SECONDS = 600


class Malformed(Exception):
    pass


def read_toml(path):
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Malformed(f"{path.parent.name}/{path.name}: {e}") from e


def corpus_of(root):
    """[(name, case)], each checked against its shape."""
    if not (root / "gates.toml").is_file():
        raise Malformed(f"{root.name}: no gates.toml")
    cases = []
    for d in sorted(p for p in (root / "cases").iterdir() if p.is_dir()):
        case = read_toml(d / "case.toml")
        seed = case.get("seed")
        planted = (
            set(case) == {"plant", "defect", "seed"}
            and case["plant"] in PLANTS
            and isinstance(case["defect"], str)
            and isinstance(seed, list)
            and len(seed) == 2
            and all(type(n) is int for n in seed)
            and 1 <= seed[0] <= seed[1]
        )
        clean = set(case) == {"clean"} and isinstance(case["clean"], str)
        if not (planted or clean):
            raise Malformed(
                f"cases/{d.name}/case.toml: plant (number or one-sided), defect"
                " and seed = [first, last], or clean alone"
            )
        if not (d / "request.md").is_file():
            raise Malformed(f"cases/{d.name}: no request.md")
        cases.append((d.name, case))
    if not cases:
        raise Malformed(f"{root.name}: no case under cases/")
    return cases


def latest(store, head):
    path = store / "records.jsonl"
    recs = (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )
    return next((r for r in reversed(recs) if r.get("head") == head), None)


def score(case, rec):
    """(outcome, detail): caught, missed, clean, held (a false hold) or one-sided (a false flag)."""
    crit = rec["critic"]
    if rec["verdict"] == "error":
        return "undecided", rec["detail"]
    held = [n for n in crit.get("numbers", []) if n.get("agrees") is False]
    omitted = [o for o in crit.get("omitted", []) if "dropped" not in o]
    shown = (
        "; ".join(
            [
                f"held line {n['line']}: stated {n['stated']}, re-derived {n['derived']:g}"
                for n in held
            ]
            + [f"omitted line {o['line']}" for o in omitted]
        )
        or "nothing held or omitted"
    )
    if "clean" in case:
        if held:
            return "held", shown
        return ("one-sided", shown) if omitted else ("clean", shown)
    first, last = case["seed"]
    inside = held if case["plant"] == "number" else omitted
    if any(first <= item["line"] <= last for item in inside):
        return "caught", shown
    if case["plant"] == "one-sided" and held:
        return "held", shown
    return "missed", f"nothing at request.md:{first}-{last} ({shown})"


def keep(rec, store, out):
    """The seat's reply envelope and the CLI version into `out`, as claude-recorded.py reads them."""
    crit = rec["critic"]
    out.mkdir(parents=True, exist_ok=True)
    if crit.get("cli"):
        (out / "version.txt").write_text(crit["cli"] + "\n")
    for seat in crit.get("seats", []):
        # A seat that timed out kept no reply; its replay is undecided, as it was live.
        if str(seat.get("reply", "")).startswith("sha256:"):
            data = (store / "logs" / f"{seat['reply'][7:]}.log").read_bytes()
            (out / f"{seat['seat']}.json").write_bytes(data)


def run_seat(root, name, author, model, store, record):
    request = root / "cases" / name / "request.md"
    argv = [
        sys.executable, str(HERE / "critic.py"), "--request", str(request),
        "--registry", str(root / "gates.toml"), "--author", author, "--store", str(store),
    ]  # fmt: skip
    env = {**os.environ, "GATES_CORPUS_CASE": f"critic/{name}/{model}"}
    try:
        done = subprocess.run(
            argv, env=env, capture_output=True, text=True, timeout=CASE_SECONDS
        )
    except subprocess.TimeoutExpired:
        return None, f"critic.py ran past {CASE_SECONDS}s"
    rec = latest(store, "sha256:" + hashlib.sha256(request.read_bytes()).hexdigest())
    sat = (rec or {}).get("critic", {}).get("seats") or [{}]
    # A run that wrote nothing leaves the other seat's record as the latest.
    if rec is None or sat[0].get("model", model) != model:
        last = (done.stdout + done.stderr).strip().splitlines()[-1:] or ["no output"]
        return (
            None,
            f"critic.py recorded no verdict from {model} (exit {done.returncode}): {last[0]}",
        )
    if record:
        keep(rec, store, record / name / model)
    return rec, None


def main():
    ap = argparse.ArgumentParser(
        description="Run the critic lens's eval on its planted decision requests."
    )
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--store", required=True, type=Path)
    ap.add_argument("--case", action="append", help="run only this case; repeatable")
    ap.add_argument("--record", type=Path, help="copy each seat's reply here, per case")
    args = ap.parse_args()
    root, store = args.corpus.resolve(), args.store.resolve()
    try:
        cases = corpus_of(root)
        if args.case:
            unknown = sorted(set(args.case) - {n for n, _ in cases})
            if unknown:
                raise Malformed(f"no case {', '.join(unknown)}")
            cases = [(n, c) for n, c in cases if n in args.case]
    except Malformed as e:
        print(f"critic-eval: undecided: {e}")
        return 2
    results = {}
    for name, case in cases:
        for author, model in SEATS:
            rec, why = run_seat(
                root, name, author, model, store, args.record and args.record.resolve()
            )
            outcome, detail = ("undecided", why) if rec is None else score(case, rec)
            results[(name, model)] = outcome
            print(f"critic-eval: {name}: {model}: {outcome}: {detail}", flush=True)
    met = True
    for _, model in SEATS:

        def count(kind, outcome, model=model):
            return sum(
                results[(n, model)] == outcome
                for n, c in cases
                if c.get("plant", "clean") == kind
            )

        def total(kind):
            return sum(c.get("plant", "clean") == kind for _, c in cases)

        right = total("one-sided") + total("clean")
        held, caught = count("number", "caught"), count("one-sided", "caught")
        false_holds = count("one-sided", "held") + count("clean", "held")
        flags = count("clean", "one-sided")
        print(
            f"critic-eval: {model}: wrong numbers held {held} of {total('number')},"
            f" one-sided caught {caught} of {total('one-sided')},"
            f" false holds {false_holds} of {right}, false one-sided flags {flags} of {total('clean')}"
        )
        missed = []
        for what, got, n in (
            ("holds", held, total("number")),
            ("catches", caught, total("one-sided")),
        ):
            if n and got < CATCH_BAR * n:
                missed.append(f"{what} {got} of {n}, under {CATCH_BAR:.0%}")
        for what, got, n in (
            ("false holds", false_holds, right),
            ("false one-sided flags", flags, total("clean")),
        ):
            if n and got > FALSE_BAR * n:
                missed.append(f"{what} {got} of {n}, over {FALSE_BAR:.0%}")
        met &= not missed
        print(
            f"critic-eval: {model}: bar {'missed: ' + '; '.join(missed) if missed else 'met'}"
        )
    undecided = sum(o == "undecided" for o in results.values())
    print(
        f"critic-eval: {len(cases)} cases, {len(results)} seats, {undecided} undecided"
    )
    if not met:
        return 1
    return 2 if undecided else 0


if __name__ == "__main__":
    sys.exit(main())
