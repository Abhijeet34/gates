#!/usr/bin/env python3
"""Regression tests for run/critic.py: the critic seat on a decision request.

    .ci/test-critic.py

Each case runs run/critic.py on a request of corpus/critic through the recorded
`claude` of .ci/fixtures/review/claude-recorded.py, answering from the real seat
replies of the eval's live run under .ci/fixtures/review/corpus/critic, and
returns why it failed, or None. No case calls a model; a case that needs an
answer the live run did not give writes it into a real envelope. Every case must
pass against run/critic.py, then fail against a copy with the one control it
exists for removed.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRITIC = ROOT / "run/critic.py"
CORPUS = ROOT / "corpus/critic"
FIXTURES = ROOT / ".ci/fixtures/review"
RECORDED = FIXTURES / "corpus/critic"
SONNET, OPUS = "claude-sonnet-5-5", "claude-opus-5-5"
ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "PYTHONDONTWRITEBYTECODE": "1",
}
WRONG = (
    "mac-runner-wrong"  # 184.00 USD stated at line 7; 1,840 minutes at 0.08 is 147.20
)
CLEAN = "mac-runner-clean"  # the same lines, 147.20 USD at line 7
ONE_SIDED = "raise-timeout-one-sided"  # the integration job's timeouts at line 13

workdir = Path(tempfile.mkdtemp())


def lines_of(case):
    return (CORPUS / "cases" / case / "request.md").read_text().splitlines()


def num(case, line, stated, expression, operands):
    """A number item quoting its lines whole, as a seat would."""
    text = lines_of(case)
    return {
        "line": line,
        "quote": text[line - 1],
        "stated": stated,
        "expression": expression,
        "operands": [
            {"value": v, "line": n, "quote": text[n - 1]} for v, n in operands
        ],
    }


def mac(case=CLEAN, stated=147.2):
    """An answer on the mac-runner pair: its two numbers, the cost stated as `stated`."""
    text = lines_of(case)
    return {
        "counter_case": "Stay on hosted runners: one Mac is a single point of failure.",
        "quotes": [{"line": 12, "quote": text[11]}],
        "omitted": [],
        "numbers": [
            num(case, 7, stated, "1840 * 0.08", [(1840, 12), (0.08, 13)]),
            num(case, 6, 80, "1840 / 2300 * 100", [(1840, 12), (2300, 12)]),
        ],
    }


class Replay:
    """Recorded cases in a fresh directory, with any seat's answer rewritten."""

    def __init__(self, case):
        self.dir = Path(tempfile.mkdtemp(dir=workdir))
        self.stub = self.dir / ".stub"
        self.stub.mkdir()
        shutil.copy(FIXTURES / "claude-recorded.py", self.stub / "claude")
        (self.stub / "claude").chmod(0o755)
        self.replies = self.dir / "replies"
        shutil.copytree(RECORDED / case, self.replies / "critic" / case)
        self.case = case
        self.store = self.dir / "store"

    def answer(self, model, reply):
        path = self.replies / "critic" / self.case / model / "critic.json"
        envelope = json.loads(path.read_text())
        envelope["result"] = json.dumps(reply)
        path.write_text(json.dumps(envelope))

    def run(self, script, author=OPUS, seat=SONNET, registry=None):
        env = {
            **ENV,
            "PATH": f"{self.stub}:{ENV['PATH']}",
            "CLAUDE_RECORDED_REPLIES": str(self.replies),
            "GATES_CORPUS_CASE": f"critic/{self.case}/{seat}",
        }
        argv = [
            sys.executable, str(script),
            "--request", str(CORPUS / "cases" / self.case / "request.md"),
            "--registry", str(registry or CORPUS / "gates.toml"),
            "--author", author, "--store", str(self.store),
        ]  # fmt: skip
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

    def records(self):
        path = self.store / "records.jsonl"
        return (
            [json.loads(x) for x in path.read_text().splitlines()]
            if path.exists()
            else []
        )


def expect(what, got, status, *texts):
    rc, out = got
    if rc != status:
        return f"{what}: exit {rc}, not {status}: {out.strip()}"
    missing = [t for t in texts if t not in out]
    return f"{what}: no {missing[0]!r} in: {out.strip()}" if missing else None


def planted_number_held(script):
    return expect(
        "the real Sonnet critic on the planted cost", Replay(WRONG).run(script), 1,
        "critic: number line 7: stated 184.0, re-derived 147.2 from 1840 * 0.08: disagrees beyond 1.84, holds",
        "critic: fail: held: 1 re-derived number disagrees beyond tolerance (line 7)",
    )  # fmt: skip


def clean_twin_passes(script):
    replay = Replay(CLEAN)
    return expect(
        "the real Sonnet critic on the clean twin", replay.run(script), 0,
        "critic: pass: ",
    )  # fmt: skip


def gate_evaluates_the_expression(script):
    replay = Replay(WRONG)
    answer = mac(WRONG, 184.0)
    # The seat claims agreement; only the gate's own arithmetic may decide.
    answer["numbers"][0]["value"] = 184.0
    answer["numbers"][0]["agrees"] = True
    replay.answer(SONNET, answer)
    return expect(
        "a seat claiming its number agrees", replay.run(script), 1,
        "critic: number line 7: stated 184.0, re-derived 147.2 from 1840 * 0.08: disagrees beyond 1.84, holds",
    )  # fmt: skip


def last_digit_rounding_agrees(script):
    replay = Replay("security-seat-clean")
    text = lines_of("security-seat-clean")
    replay.answer(
        SONNET,
        {
            "counter_case": "Keep the seat everywhere: it caught what nothing else did.",
            "quotes": [{"line": 7, "quote": text[6]}],
            "omitted": [],
            "numbers": [
                num(
                    "security-seat-clean", 7, 1.3, "4 / 300 * 100", [(4, 13), (300, 12)]
                ),
                num("security-seat-clean", 6, 900, "3.0 * 300", [(3.0, 12), (300, 12)]),
            ],
        },
    )
    return expect(
        "1.33 stated as about 1.3", replay.run(script), 0,
        "critic: number line 7: stated 1.3, re-derived 1.33333 from 4 / 300 * 100: agrees within 0.05",
    )  # fmt: skip


def expression_uses_only_operands(script):
    replay = Replay(WRONG)
    answer = mac(WRONG, 184.0)
    answer["numbers"][0] = num(WRONG, 7, 184.0, "2300 * 0.08", [(0.08, 13)])
    replay.answer(SONNET, answer)
    return expect(
        "an expression over a figure no operand quotes", replay.run(script), 2,
        "critic: number line 7: stated 184.0: not re-derived: its expression uses 2300, which is no operand's value",
        "critic: error: 1 of the numbers the request rests on re-derived, not two",
    )  # fmt: skip


def number_not_derived_from_itself(script):
    replay = Replay(WRONG)
    answer = mac(WRONG, 184.0)
    answer["numbers"][0] = num(WRONG, 7, 184.0, "184.00", [(184.0, 7)])
    replay.answer(SONNET, answer)
    return expect(
        "a number derived from itself", replay.run(script), 2,
        "critic: number line 7: stated 184.0: not re-derived: operand 1: it is the stated value itself",
    )  # fmt: skip


def stated_value_on_its_line(script):
    replay = Replay(WRONG)
    answer = mac(WRONG, 184.0)
    answer["numbers"][0]["stated"] = 150
    replay.answer(SONNET, answer)
    return expect(
        "a stated value its line does not carry", replay.run(script), 2,
        "critic: number line 7: stated 150: not re-derived: its stated value is no number on line 7",
    )  # fmt: skip


def counter_case_must_quote(script):
    replay = Replay(CLEAN)
    answer = mac()
    answer["quotes"] = [
        {"line": 12, "quote": "CI used about two thousand minutes on macOS"}
    ]
    replay.answer(SONNET, answer)
    return expect(
        "a paraphrased counter-case", replay.run(script), 2,
        "critic: quote dropped: its quote is not on line 12",
        "critic: error: no quote of the counter-case stands on its line, so it rests on nothing",
    )  # fmt: skip


def short_quote_refused(script):
    replay = Replay(CLEAN)
    answer = mac()
    answer["quotes"] = [{"line": 12, "quote": "1,840 of them"}]
    replay.answer(SONNET, answer)
    return expect(
        "a counter-case quoting a fragment", replay.run(script), 2,
        "critic: quote dropped: its quote is under 20 characters and not the whole line",
    )  # fmt: skip


def two_numbers_needed(script):
    replay = Replay(CLEAN)
    answer = mac()
    answer["numbers"] = answer["numbers"][:1]
    replay.answer(SONNET, answer)
    return expect(
        "a critic re-deriving one number", replay.run(script), 2,
        "critic: error: 1 of the numbers the request rests on re-derived, not two",
    )  # fmt: skip


def one_sided_recorded_not_held(script):
    replay = Replay(ONE_SIDED)
    return expect(
        "the real Opus critic on the one-sided timeout request",
        replay.run(script, author=SONNET, seat=OPUS), 0,
        "critic: omitted line 13: - 18 of the 24 timeouts were in one job",
        "critic: one-sided: the recommendation leaves out 1 evidence line; recorded for the captain, does not hold",
    )  # fmt: skip


def critic_on_another_model(script):
    replay = Replay(ONE_SIDED)
    got = replay.run(script, author=SONNET, seat=OPUS)
    models = [c["argv"][c["argv"].index("--model") + 1] for c in replay.calls()]
    if models != [OPUS]:
        return f"Sonnet's request sat {models}, not Opus: {got[1].strip()}"
    return expect(
        "Opus critiquing Sonnet's request", got, 0,
        "critic: critic claude-opus-5-5 (cross-model from the author claude-sonnet-5-5)",
        "critic: independence: cross-model",
    )  # fmt: skip


def lens_reaches_the_seat(script):
    replay = Replay(WRONG)
    replay.run(script)
    calls = replay.calls()
    if not calls:
        return "no seat sat"
    argv = calls[0]["argv"]
    system = argv[argv.index("--system-prompt") + 1]
    want = (
        "### Lens: critic",
        "Role, for tone only: critic.",
        "- Does each number the recommendation rests on follow from the figures it cites?",
        "Stop rule: Report only what changes the decision",
    )
    missing = [w for w in want if w not in system]
    return f"the seat's prompt has no {missing[0]!r}:\n{system}" if missing else None


def malformed_lens_refused(script):
    registry = Path(tempfile.mkdtemp(dir=workdir), "gates.toml")
    registry.write_text(
        (CORPUS / "gates.toml")
        .read_text()
        .replace(
            '"Is each share stated with the count it is a share of?"',
            '"Each share is stated with its count."',
        )
    )
    replay = Replay(WRONG)
    got = replay.run(script, registry=registry)
    if replay.calls():
        return "a seat sat on a malformed lens"
    return expect(
        "a lens whose checklist holds a statement", got, 2,
        "critic: error: gates.toml: lens 'critic': checklist must be a non-empty list of yes or no questions",
    )  # fmt: skip


def api_login_refused(script):
    replay = Replay(WRONG)
    (replay.replies / "critic" / WRONG / SONNET / "auth.json").write_text(
        json.dumps(
            {"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"}
        )
    )
    got = replay.run(script)
    if replay.calls():
        return "a seat sat on an API key login"
    return expect(
        "an API key login",
        got,
        2,
        "critic: error: refused: claude is logged in with 'api_key'",
    )


def verdict_recorded(script):
    replay = Replay(WRONG)
    replay.run(script)
    recs = replay.records()
    digest = hashlib.sha256(
        (CORPUS / "cases" / WRONG / "request.md").read_bytes()
    ).hexdigest()
    if len(recs) != 1:
        return f"{len(recs)} records, not 1"
    rec, crit = recs[0], recs[0].get("critic", {})
    got = (
        rec.get("gate"), rec.get("head"), rec.get("verdict"),
        [s.get("model") for s in crit.get("seats", [])], crit.get("independence"),
        crit.get("tolerance"), [n.get("agrees") for n in crit.get("numbers", [])],
    )  # fmt: skip
    want = ("critic", f"sha256:{digest}", "fail", [SONNET], "cross-model", 0.01)
    if got[:6] != want or False not in got[6]:
        return f"recorded {got}, not {want} with a disagreeing number"
    return None


# (case, control it proves, text in run/critic.py, the text with the control removed)
CASES = [
    (planted_number_held, "a disagreeing number holding", '"agrees": abs(derived - item["stated"]) <= tol,', '"agrees": True,'),
    (clean_twin_passes, "the seat's answer read", "    state[\"numbers\"] = [rederive(lines, item) for item in found]\n", "    state[\"numbers\"] = []\n"),
    (gate_evaluates_the_expression, "the gate's own arithmetic", "        derived = evaluate(item.get(\"expression\"), allowed)\n", "        derived = item.get(\"value\", item[\"stated\"])\n"),
    (last_digit_rounding_agrees, "half a unit in the last stated digit", "return max(TOLERANCE * abs(stated), 0.5 * 10**-places)", "return TOLERANCE * abs(stated)"),
    (expression_uses_only_operands, "only operands in the expression", "            if not any(abs(node.value - a) < 1e-9 for a in allowed):\n", "            if False:\n"),
    (number_not_derived_from_itself, "no number derived from itself", '            and op["line"] == item["line"]\n', '            and False\n'),
    (stated_value_on_its_line, "the stated value read off its line", "    if as_written is None:\n        return {\n", '    as_written = as_written or "0"\n    if False:\n        return {\n'),
    (counter_case_must_quote, "a counter-case resting on a quote that stands", '    if not any("dropped" not in q for q in state["quotes"]):\n', "    if False:\n"),
    (short_quote_refused, "the shortest counter-case quote", "if not short and q != line and len(q) < MIN_QUOTE:", "if False:"),
    (two_numbers_needed, "two numbers re-derived", "    if len(rederived) < 2:\n", "    if len(rederived) < 1:\n"),
    (one_sided_recorded_not_held, "omitted evidence never holding", '    wrong = [n for n in rederived if not n["agrees"]]\n', '    wrong = [n for n in rederived if not n["agrees"]] + [o for o in state["omitted"] if "dropped" not in o]\n'),
    (critic_on_another_model, "the critic seated off the author's model", "    model = review.seats(REVIEWERS, author)[0]\n", "    model = REVIEWERS[0]\n"),
    (lens_reaches_the_seat, "the lens in the seat's prompt", 'system = "\\n\\n".join([CRITIC, review.lens_text(lens, "checklist")])', "system = CRITIC"),
    (malformed_lens_refused, "the lens row checked", "        return review.lens_of(row, registry.name)\n", "        return row\n"),
    (api_login_refused, "the subscription login", '        state["cli"] = review.harness()\n', '        state["cli"] = "unchecked"\n'),
    (verdict_recorded, "the request's digest as the record's head", '"head": "sha256:" + hashlib.sha256(data).hexdigest(),', '"head": args.request.name,'),
]  # fmt: skip


def main():
    failed = 0
    source = CRITIC.read_text()
    for case, control, old, new in CASES:
        why = case(CRITIC)
        if why:
            failed = 1
            print(f"FAIL {case.__name__}: {why}")
            continue
        print(f"ok   {case.__name__}")
        if source.count(old) != 1:
            failed = 1
            print(
                f"FAIL without {control}: the mutation matches {source.count(old)} places in run/critic.py, not 1"
            )
            continue
        # critic.py imports review.py and gate.py from its own directory.
        mutant_dir = Path(tempfile.mkdtemp(dir=workdir))
        for sibling in ("review.py", "gate.py"):
            shutil.copy(ROOT / "run" / sibling, mutant_dir / sibling)
        mutant = mutant_dir / "critic.py"
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
    print("\ncritic tests " + ("FAILED" if failed else "passed"))
    return failed


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
