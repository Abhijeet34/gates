#!/usr/bin/env python3
"""Regression tests for run/critic-eval.py: the critic lens's eval.

    .ci/test-critic-eval.py

Each case replays requests of corpus/critic through the recorded `claude` of
.ci/fixtures/review/claude-recorded.py, answering from the real seat replies of
the live run under .ci/fixtures/review/corpus/critic, and returns why it
failed, or None. No case calls a model; a case that needs an outcome the live
run did not give writes a seat's answer into its real envelope. Every case must
pass against run/critic-eval.py, then fail against a copy with the one control
it exists for removed. The first case pins the measured counts the README quotes.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "run/critic-eval.py"
CORPUS = ROOT / "corpus/critic"
FIXTURES = ROOT / ".ci/fixtures/review"
RECORDED = FIXTURES / "corpus/critic"
SONNET, OPUS = "claude-sonnet-5-5", "claude-opus-5-5"
ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "PYTHONDONTWRITEBYTECODE": "1",
}
# The live run, 2026-10-20, through the subscription login; README.md, "The critic lens".
MEASURED = [
    "critic-eval: claude-sonnet-5-5: wrong numbers held 6 of 6, one-sided caught 6 of 6, false holds 0 of 18, false one-sided flags 4 of 12",
    "critic-eval: claude-sonnet-5-5: bar missed: false one-sided flags 4 of 12, over 10%",
    "critic-eval: claude-opus-5-5: wrong numbers held 6 of 6, one-sided caught 6 of 6, false holds 0 of 18, false one-sided flags 1 of 12",
    "critic-eval: claude-opus-5-5: bar met",
    "critic-eval: 24 cases, 48 seats, 0 undecided",
]

workdir = Path(tempfile.mkdtemp())


def lines_of(case):
    return (CORPUS / "cases" / case / "request.md").read_text().splitlines()


def answer(case, numbers, omitted=()):
    """A seat's answer on `case`: numbers as (line, stated, expression, [(value, line)])."""
    text = lines_of(case)
    return {
        "counter_case": "Choose otherwise, on the request's own evidence.",
        "quotes": [{"line": n, "quote": text[n - 1]} for n in (numbers[0][0],)],
        "omitted": [{"line": n, "quote": text[n - 1]} for n in omitted],
        "numbers": [
            {
                "line": line,
                "quote": text[line - 1],
                "stated": stated,
                "expression": expression,
                "operands": [
                    {"value": v, "line": n, "quote": text[n - 1]} for v, n in ops
                ],
            }
            for line, stated, expression, ops in numbers
        ],
    }


# mac-runner: the cost at line 7 (184.00 planted, 147.20 right), the share at line 6.
SHARE = (6, 80, "1840 / 2300 * 100", [(1840, 12), (2300, 12)])
WRONG_COST = (7, 184.0, "1840 * 0.08", [(1840, 12), (0.08, 13)])
# raise-timeout: the share at line 6, the margin at line 7; its integration line is 13
# in the one-sided request and 14 in its clean twin.
TIMEOUTS = [
    (6, 6, "24 / 400 * 100", [(24, 11), (400, 11)]),
    (7, 2, "30 - 28", [(30, 5), (28, 12)]),
]
TWIN_TIMEOUTS = [
    (6, 6, "24 / 400 * 100", [(24, 12), (400, 12)]),
    (7, 2, "30 - 28", [(30, 5), (28, 13)]),
]


class Replay:
    """Recorded cases in a fresh directory, with any seat's answer rewritten."""

    def __init__(self, *cases):
        self.dir = Path(tempfile.mkdtemp(dir=workdir))
        self.stub = self.dir / ".stub"
        self.stub.mkdir()
        shutil.copy(FIXTURES / "claude-recorded.py", self.stub / "claude")
        (self.stub / "claude").chmod(0o755)
        self.replies = self.dir / "replies"
        for case in cases:
            shutil.copytree(RECORDED / case, self.replies / "critic" / case)
        self.cases = list(cases)

    def answer(self, case, model, reply):
        path = self.replies / "critic" / case / model / "critic.json"
        envelope = json.loads(path.read_text())
        envelope["result"] = json.dumps(reply)
        path.write_text(json.dumps(envelope))

    def run(self, script, *extra, cases=None, corpus=None):
        env = {
            **ENV,
            "PATH": f"{self.stub}:{ENV['PATH']}",
            "CLAUDE_RECORDED_REPLIES": str(self.replies),
        }
        names = self.cases if cases is None else cases
        argv = [sys.executable, str(script), "--corpus", str(corpus or CORPUS)]
        argv += [a for n in names for a in ("--case", n)]
        argv += ["--store", str(Path(tempfile.mkdtemp(dir=workdir))), *extra]
        proc = subprocess.run(
            argv, cwd=self.dir, env=env, capture_output=True, text=True
        )
        return proc.returncode, proc.stdout + proc.stderr

    def calls(self):
        log = self.stub / "calls.jsonl"
        return (
            [json.loads(x) for x in log.read_text().splitlines()]
            if log.exists()
            else []
        )


def expect(what, got, status, *texts):
    rc, out = got
    if rc != status:
        return f"{what}: exit {rc}, not {status}: {out.strip()}"
    missing = [t for t in texts if t not in out]
    return f"{what}: no {missing[0]!r} in: {out.strip()}" if missing else None


def measured_counts(script):
    """Every case, replayed, gives the counts of the live run."""
    rc, out = Replay(*sorted(os.listdir(RECORDED))).run(script, cases=[])
    missing = [line for line in MEASURED if line not in out]
    return f"no {missing[0]!r} in: {out.strip()}" if missing else None


def hold_inside_seed_catches(script):
    replay = Replay("mac-runner-wrong")
    for model in (SONNET, OPUS):
        replay.answer(
            "mac-runner-wrong", model, answer("mac-runner-wrong", [WRONG_COST, SHARE])
        )
    return expect(
        "a hold on the planted cost", replay.run(script), 0,
        "critic-eval: mac-runner-wrong: claude-sonnet-5-5: caught: held line 7: stated 184.0, re-derived 147.2",
        "critic-eval: claude-sonnet-5-5: wrong numbers held 1 of 1",
    )  # fmt: skip


def hold_off_seed_misses(script):
    replay = Replay("mac-runner-wrong")
    # A hold on line 6 is a hold for the wrong reason: the planted number is line 7's.
    off = (6, 80, "1840 / 2300 * 100 - 30", [(1840, 12), (2300, 12), (30, 6)])
    for model in (SONNET, OPUS):
        replay.answer("mac-runner-wrong", model, answer("mac-runner-wrong", [off]))
    return expect(
        "a hold off the seed", replay.run(script), 1,
        "critic-eval: mac-runner-wrong: claude-sonnet-5-5: missed: nothing at request.md:7-7",
        "critic-eval: claude-sonnet-5-5: bar missed: holds 0 of 1, under 80%",
    )  # fmt: skip


def omitted_inside_seed_catches(script):
    case = "raise-timeout-one-sided"
    replay = Replay(case)
    for model in (SONNET, OPUS):
        replay.answer(case, model, answer(case, TIMEOUTS, omitted=[13]))
    return expect(
        "an omitted-evidence quote on the planted line", replay.run(script), 0,
        f"critic-eval: {case}: claude-opus-5-5: caught: omitted line 13",
        "critic-eval: claude-opus-5-5: wrong numbers held 0 of 0, one-sided caught 1 of 1",
    )  # fmt: skip


def hold_on_right_numbers_is_false(script):
    case = "mac-runner-clean"
    replay = Replay(case)
    for model in (SONNET, OPUS):
        replay.answer(
            case,
            model,
            answer(
                case,
                [
                    (7, 147.2, "1840 * 0.08 + 6", [(1840, 12), (0.08, 13), (6, 14)]),
                    SHARE,
                ],
            ),
        )
    return expect(
        "a hold on a clean twin", replay.run(script), 1,
        f"critic-eval: {case}: claude-sonnet-5-5: held: held line 7",
        "critic-eval: claude-sonnet-5-5: bar missed: false holds 1 of 1, over 10%",
    )  # fmt: skip


def omitted_on_clean_twin_is_false(script):
    case = "raise-timeout-clean"
    replay = Replay(case)
    for model in (SONNET, OPUS):
        replay.answer(case, model, answer(case, TWIN_TIMEOUTS, omitted=[14]))
    return expect(
        "an omitted-evidence quote on a clean twin", replay.run(script), 1,
        f"critic-eval: {case}: claude-opus-5-5: one-sided: omitted line 14",
        "critic-eval: claude-opus-5-5: bar missed: false one-sided flags 1 of 1, over 10%",
    )  # fmt: skip


def each_seat_on_the_other_model(script):
    case = "mac-runner-clean"
    replay = Replay(case)
    replay.run(script)
    sat = sorted(c["argv"][c["argv"].index("--model") + 1] for c in replay.calls())
    return (
        None
        if sat == sorted([SONNET, OPUS])
        else f"the case sat {sat}, not one Sonnet and one Opus critic"
    )


def undecided_never_counts(script):
    case = "mac-runner-clean"
    replay = Replay(case)
    (replay.replies / "critic" / case / OPUS / "critic.json").unlink()
    return expect(
        "a seat with no reply", replay.run(script), 2,
        f"critic-eval: {case}: claude-opus-5-5: undecided:",
        "critic-eval: 1 cases, 2 seats, 1 undecided",
    )  # fmt: skip


def malformed_case_refused(script):
    corpus = Path(tempfile.mkdtemp(dir=workdir), "critic")
    shutil.copytree(CORPUS, corpus)
    case = corpus / "cases" / "mac-runner-clean" / "case.toml"
    case.write_text(case.read_text() + "seed = [7, 7]\n")
    return expect(
        "a clean case with a seed", Replay("mac-runner-clean").run(script, corpus=corpus), 2,
        "critic-eval: undecided: cases/mac-runner-clean/case.toml: plant (number or one-sided), defect",
    )  # fmt: skip


def record_copies_replies(script):
    case = "mac-runner-wrong"
    replay = Replay(case)
    out = Path(tempfile.mkdtemp(dir=workdir))
    rc, text = replay.run(script, "--record", str(out))
    for model in (SONNET, OPUS):
        for kept in sorted((RECORDED / case / model).iterdir()):
            copy = out / case / model / kept.name
            if not copy.exists() or copy.read_bytes() != kept.read_bytes():
                return f"--record did not keep {model}/{kept.name} as recorded (exit {rc}): {text.strip()}"
    return None


# (case, control it proves, text in run/critic-eval.py, the text with the control removed)
CASES = [
    (measured_counts, "the case and seat named to the CLI", 'env = {**os.environ, "GATES_CORPUS_CASE": f"critic/{name}/{model}"}', 'env = {**os.environ, "GATES_CORPUS_CASE": f"critic/{name}"}'),
    (hold_inside_seed_catches, "a hold catching", '    inside = held if case["plant"] == "number" else omitted\n', "    inside = []\n"),
    (hold_off_seed_misses, "the seed's lines", 'if any(first <= item["line"] <= last for item in inside):', "if inside:"),
    (omitted_inside_seed_catches, "an omitted quote catching a one-sided case", '    inside = held if case["plant"] == "number" else omitted\n', "    inside = held\n"),
    (hold_on_right_numbers_is_false, "a false hold counted", '        if held:\n            return "held", shown\n', ""),
    (omitted_on_clean_twin_is_false, "a false one-sided flag counted", 'return ("one-sided", shown) if omitted else ("clean", shown)', 'return ("clean", shown)'),
    (each_seat_on_the_other_model, "both seats", '    ("claude-sonnet-5-5", "claude-opus-5-5"),\n)', ")"),
    (undecided_never_counts, "an undecided seat", '        return "undecided", rec["detail"]\n', '        return "caught", rec["detail"]\n'),
    (malformed_case_refused, "the case's shape checked", "        if not (planted or clean):\n", "        if False:\n"),
    (record_copies_replies, "the replies recorded", "(out / f\"{seat['seat']}.json\").write_bytes(data)", "pass"),
]  # fmt: skip


def main():
    failed = 0
    source = EVAL.read_text()
    for case, control, old, new in CASES:
        why = case(EVAL)
        if why:
            failed = 1
            print(f"FAIL {case.__name__}: {why}")
            continue
        print(f"ok   {case.__name__}")
        if source.count(old) != 1:
            failed = 1
            print(
                f"FAIL without {control}: the mutation matches {source.count(old)} places in run/critic-eval.py, not 1"
            )
            continue
        # critic-eval.py runs critic.py beside it, which imports review.py and gate.py.
        mutant_dir = Path(tempfile.mkdtemp(dir=workdir))
        for sibling in ("critic.py", "review.py", "gate.py"):
            shutil.copy(ROOT / "run" / sibling, mutant_dir / sibling)
        mutant = mutant_dir / "critic-eval.py"
        mutant.write_text(source.replace(old, new))
        why = case(mutant)
        if why:
            quoted = " | ".join(why.splitlines()[:3])
            print(f"ok   without {control}, {case.__name__} goes red: {quoted}")
        else:
            failed = 1
            print(
                f"FAIL without {control}, {case.__name__} still passes, so the case does not prove it"
            )
    print("\ncritic eval tests " + ("FAILED" if failed else "passed"))
    return failed


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
