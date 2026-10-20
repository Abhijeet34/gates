#!/usr/bin/env python3
"""Regression tests for run/lens-eval.py: the role lenses' ablation eval.

    .ci/test-lens-eval.py

Each case replays spec cases of corpus/lens through the recorded `claude` of
.ci/fixtures/review/claude-recorded.py, answering from the real seat replies of
the live run under .ci/fixtures/review/corpus/lens, and returns why it failed,
or None. No case calls a model; a case that needs an outcome the live run did
not give writes a seat's answer into its real envelope. Every case must pass
against run/lens-eval.py, then fail against a copy with the one control it
exists for removed. The first case pins the measured counts the README quotes.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "run/lens-eval.py"
CORPUS = ROOT / "corpus/lens"
FIXTURES = ROOT / ".ci/fixtures/review"
RECORDED = FIXTURES / "corpus/lens"
ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "PYTHONDONTWRITEBYTECODE": "1",
}
# The second live run, 2026-10-20, through the subscription login, finder Sonnet,
# and the open-alternative lens's run of 2026-10-21 (CLI 2.1.304); README.md,
# "The role lenses".
MEASURED = [
    "lens-eval: product-stakeholder: seeded caught: checklist 3 of 3, bare 3, persona 1, none 0; clean flagged: checklist 4 of 22, none 10",
    "lens-eval: product-stakeholder: bar missed: flags 4 of 22, over 10%",
    "lens-eval: product-stakeholder: ablation keep: checklist beats none by 1.00, half-width 0.80",
    "lens-eval: product-stakeholder: role line remove: checklist 3 of 3 against bare 3",
    "lens-eval: architect: seeded caught: checklist 2 of 3, bare 0, persona 1, none 1; clean flagged: checklist 12 of 22, none 10",
    "lens-eval: architect: bar missed: catches 2 of 3, under 80%; flags 12 of 22, over 10%",
    "lens-eval: architect: ablation cut: checklist beats none by 0.33, half-width 0.80",
    "lens-eval: architect: role line keep: checklist 2 of 3 against bare 0",
    "lens-eval: security: seeded caught: checklist 3 of 3, bare 2, persona 1, none 0; clean flagged: checklist 5 of 22, none 10",
    "lens-eval: security: bar missed: flags 5 of 22, over 10%",
    "lens-eval: security: ablation keep: checklist beats none by 1.00, half-width 0.80",
    "lens-eval: security: role line keep: checklist 3 of 3 against bare 2",
    "lens-eval: tester: seeded caught: checklist 3 of 3, bare 3, persona 0, none 0; clean flagged: checklist 21 of 22, none 10",
    "lens-eval: tester: bar missed: flags 21 of 22, over 10%",
    "lens-eval: tester: ablation keep: checklist beats none by 1.00, half-width 0.80",
    "lens-eval: tester: role line remove: checklist 3 of 3 against bare 3",
    "lens-eval: operations: seeded caught: checklist 5 of 6, bare 5, persona 5, none 1; clean flagged: checklist 7 of 22, none 10",
    "lens-eval: operations: bar missed: flags 7 of 22, over 10%",
    "lens-eval: operations: ablation keep: checklist beats none by 0.67, half-width 0.57",
    "lens-eval: operations: role line remove: checklist 5 of 6 against bare 5",
    "lens-eval: designer: seeded caught: checklist 2 of 2, bare 2, persona 1, none 0; clean flagged: checklist 5 of 22, none 10",
    "lens-eval: designer: bar missed: flags 5 of 22, over 10%",
    "lens-eval: designer: ablation keep: checklist beats none by 1.00, half-width 0.98",
    "lens-eval: designer: role line remove: checklist 2 of 2 against bare 2",
    "lens-eval: open-alternative: seeded caught: checklist 6 of 6, bare 4, persona 5, none 5; clean flagged: checklist 3 of 22, none 10",
    "lens-eval: open-alternative: bar missed: flags 3 of 22, over 10%",
    "lens-eval: open-alternative: ablation cut: checklist beats none by 0.17, half-width 0.31",
    "lens-eval: open-alternative: role line keep: checklist 6 of 6 against bare 4",
    "lens-eval: 48 cases, 0 undecided seats",
]

workdir = Path(tempfile.mkdtemp())


def answer(severity, line, claim="a planted finding: no backup before the drop"):
    return {
        "findings": [
            {
                "severity": severity,
                "path": "spec.md",
                "line": line,
                "quote": "",
                "claim": claim,
            }
        ]
    }


class Replay:
    """Recorded cases in a fresh directory, with any seat's answer rewritten."""

    def __init__(self, *cases, root=RECORDED):
        self.dir = Path(tempfile.mkdtemp(dir=workdir))
        self.stub = self.dir / ".stub"
        self.stub.mkdir()
        shutil.copy(FIXTURES / "claude-recorded.py", self.stub / "claude")
        (self.stub / "claude").chmod(0o755)
        self.replies = self.dir / "replies"
        for case in cases:
            shutil.copytree(root / case, self.replies / "lens" / case)
        self.cases = list(cases)

    def answer(self, case, seat, reply):
        path = self.replies / "lens" / case / f"{seat}.json"
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
        lines = log.read_text().splitlines() if log.exists() else []
        return {c["seat"]: c for c in map(json.loads, lines)}


def expect(what, got, status, *texts):
    rc, out = got
    if rc != status:
        return f"{what}: exit {rc}, not {status}: {out.strip()}"
    missing = [t for t in texts if t not in out]
    return f"{what}: no {missing[0]!r} in: {out.strip()}" if missing else None


def system_prompt(call):
    argv = call["argv"]
    return argv[argv.index("--system-prompt") + 1]


def measured_counts(script):
    """Every case, replayed, gives the counts of the live run."""
    replay = Replay(*sorted(os.listdir(RECORDED)))
    rc, out = replay.run(script, cases=[])
    missing = [line for line in MEASURED if line not in out]
    return f"no {missing[0]!r} in: {out.strip()}" if missing else None


SEEDED = "drop-column-gap"  # architect, seed spec.md:14-17
CLEAN = "team-invites-clean"


def seed_finding_catches(script):
    replay = Replay(SEEDED)
    replay.answer(SEEDED, "lens-architect-checklist", answer("warning", 15))
    return expect(
        "a warning inside the seed", replay.run(script), 0,
        f"lens-eval: {SEEDED}: architect checklist: caught: F1 warning spec.md:15: a planted finding: no backup before the drop",
    )  # fmt: skip


def finding_off_seed_misses(script):
    replay = Replay(SEEDED)
    replay.answer(SEEDED, "lens-architect-checklist", answer("warning", 4))
    return expect(
        "a warning off the seed", replay.run(script), 1,
        f"lens-eval: {SEEDED}: architect checklist: missed: nothing at spec.md:14-17 naming its gap",
        "lens-eval: architect: bar missed: catches 0 of 1, under 80%",
    )  # fmt: skip


def unrelated_finding_misses(script):
    replay = Replay(SEEDED)
    replay.answer(
        SEEDED,
        "lens-architect-checklist",
        answer("warning", 15, "the step names no owner"),
    )
    return expect(
        "a warning inside the seed about something else", replay.run(script), 1,
        f"lens-eval: {SEEDED}: architect checklist: missed: nothing at spec.md:14-17 naming its gap",
    )  # fmt: skip


def nit_does_not_catch(script):
    replay = Replay(SEEDED)
    replay.answer(SEEDED, "lens-architect-checklist", answer("nit", 15))
    return expect(
        "a nit inside the seed", replay.run(script), 1,
        f"lens-eval: {SEEDED}: architect checklist: missed: nothing at spec.md:14-17 naming its gap (no finding)",
    )  # fmt: skip


def clean_case_flagged(script):
    replay = Replay(CLEAN)
    replay.answer(CLEAN, "lens-operations-checklist", answer("warning", 3))
    return expect(
        "a warning on a clean spec", replay.run(script), 1,
        f"lens-eval: {CLEAN}: operations checklist: flagged: F1 warning spec.md:3: a planted finding: no backup before the drop",
        "lens-eval: operations: bar missed: flags 1 of 1, over 10%",
    )  # fmt: skip


def clean_sits_every_measured_lens(script):
    replay = Replay(CLEAN)
    replay.run(script)
    seats = sorted(replay.calls())
    want = sorted(
        ["lens-none"]
        + [
            f"lens-{lid}-checklist"
            for lid in ("architect", "designer", "open-alternative", "operations", "product-stakeholder", "security", "tester")
        ]
    )  # fmt: skip
    return None if seats == want else f"a clean spec sat {seats}, not {want}"


def arms_read_their_own_lens(script):
    replay = Replay(SEEDED)
    replay.run(script)
    calls = replay.calls()
    checklist, bare, persona, none = (
        system_prompt(calls[f"lens-{s}"])
        for s in ("architect-checklist", "architect-bare", "architect-persona", "none")
    )
    question = (
        "- Can every step be undone, and is a step that cannot be undone preceded"
    )
    if not (
        "Role, for tone only: software architect." in checklist
        and question in checklist
    ):
        return (
            f"the checklist arm did not read the row with its role line:\n{checklist}"
        )
    if "Role, for tone only" in bare or question not in bare:
        return f"the bare arm did not read the checklist alone:\n{bare}"
    if "You are a seasoned software architect." not in persona or question in persona:
        return f"the persona arm did not read the role line alone:\n{persona}"
    if "### Lens:" in none:
        return f"the none arm read a lens:\n{none}"
    return None


def bar_miss_fails_the_run(script):
    replay = Replay(SEEDED)
    replay.answer(SEEDED, "lens-architect-checklist", {"findings": []})
    return expect(
        "a lens that misses its seed", replay.run(script), 1,
        "lens-eval: architect: bar missed: catches 0 of 1, under 80%",
    )  # fmt: skip


ARCHITECT = ["drop-column-gap", "merge-tables-gap", "rekey-orders-gap"]


def ablation_keeps_a_clear_gain(script):
    replay = Replay(*ARCHITECT)
    for case in ARCHITECT:
        replay.answer(case, "lens-architect-checklist", answer("error", 15))
        replay.answer(case, "lens-none", {"findings": []})
    return expect(
        "a checklist catching 3 of 3 where none catches 0", replay.run(script), 0,
        "lens-eval: architect: ablation keep: checklist beats none by 1.00, half-width 0.80",
    )  # fmt: skip


def ablation_cuts_no_gain(script):
    replay = Replay(SEEDED)
    replay.answer(SEEDED, "lens-architect-checklist", answer("error", 15))
    replay.answer(SEEDED, "lens-none", answer("error", 16))
    return expect(
        "a checklist catching no more than none", replay.run(script), 0,
        "lens-eval: architect: ablation cut: checklist beats none by 0.00, half-width 0.00",
    )  # fmt: skip


def role_line_removed_without_gain(script):
    replay = Replay(SEEDED)
    replay.answer(SEEDED, "lens-architect-checklist", answer("error", 15))
    replay.answer(SEEDED, "lens-architect-bare", answer("error", 15))
    return expect(
        "a role line adding nothing over its checklist", replay.run(script), 0,
        "lens-eval: architect: role line remove: checklist 1 of 1 against bare 1",
    )  # fmt: skip


def unmeasured_lens_reported(script):
    return expect(
        "a lens owning no seeded case", Replay(SEEDED).run(script), 0,
        "lens-eval: product-analyst: unmeasured: no seeded case in this set",
    )  # fmt: skip


def undecided_never_counts(script):
    replay = Replay(SEEDED)
    (replay.replies / "lens" / SEEDED / "lens-architect-persona.json").unlink()
    return expect(
        "a seat with no reply", replay.run(script), 2,
        f"lens-eval: {SEEDED}: architect persona: undecided:",
        "lens-eval: 1 cases, 1 undecided seats",
    )  # fmt: skip


GOLDEN = [
    "open-alt-name-mark-gap",  # a name containing the incumbent's mark
    "open-alt-name-variant-gap",  # the mark differing by case, spacing and a diacritic
    "open-alt-screen-replica-gap",  # a request for a screen replica
    "open-alt-same-layout-gap",  # "the same layout", without the word replica
    "open-alt-copied-icons-gap",
    "open-alt-interop-unmarked-gap",
]


def open_alternative_golden_cases(script):
    replay = Replay(*GOLDEN, "open-alt-complete-clean")
    # Exit 1: the clean case also sits the other lenses, whose own bars the live run missed.
    return expect(
        "the open-alternative golden cases", replay.run(script), 1,
        *[f"lens-eval: {c}: open-alternative checklist: caught:" for c in GOLDEN],
        "lens-eval: open-alt-complete-clean: open-alternative checklist: clean: no error or warning",
        "lens-eval: open-alternative: seeded caught: checklist 6 of 6, bare 4, persona 5, none 5; clean flagged: checklist 0 of 1, none 1",
        "lens-eval: open-alternative: bar met",
    )  # fmt: skip


def open_alternative_checks():
    """The open-alternative row's checklist lines in the shipped gates.toml."""
    text = (CORPUS / "gates.toml").read_text()
    row = text[text.index('id = "open-alternative"') :]
    return re.findall(r'^  ".*\?",\n', row, re.M)


# Checklist item removed -> the cases it answers for. The live run of 2026-10-21
# found only the interop item load-bearing: the finder alone still names a name,
# an icon or a copied layout, so those stay caught without their item.
REMOVED = {
    0: ["open-alt-name-mark-gap", "open-alt-name-variant-gap"],
    1: ["open-alt-name-mark-gap", "open-alt-name-variant-gap"],
    2: [
        "open-alt-screen-replica-gap",
        "open-alt-same-layout-gap",
        "open-alt-copied-icons-gap",
    ],
    3: ["open-alt-interop-unmarked-gap"],
}


def open_alternative_check_removed(script):
    for item, cases in REMOVED.items():
        corpus = Path(tempfile.mkdtemp(dir=workdir), "lens")
        shutil.copytree(CORPUS, corpus)
        rows = corpus / "gates.toml"
        rows.write_text(
            rows.read_text().replace(open_alternative_checks()[item], "", 1)
        )
        replay = Replay(*cases, root=FIXTURES / f"corpus/lens-without-{item}")
        got = replay.run(script, corpus=corpus)
        if item == 3:
            why = expect(
                "the interop check removed", got, 1,
                "lens-eval: open-alt-interop-unmarked-gap: open-alternative checklist: missed: nothing at spec.md:15-17 naming its gap (no finding)",
                "lens-eval: open-alternative: bar missed: catches 0 of 1, under 80%",
            )  # fmt: skip
        else:
            why = expect(
                f"check {item} removed", got, 0,
                *[f"lens-eval: {c}: open-alternative checklist: caught:" for c in cases],
            )  # fmt: skip
        if why:
            return why
    return None


def malformed_case_refused(script):
    corpus = Path(tempfile.mkdtemp(dir=workdir), "lens")
    shutil.copytree(CORPUS, corpus)
    case = corpus / "cases" / CLEAN / "case.toml"
    case.write_text(case.read_text() + "seed = [3, 4]\n")
    return expect(
        "a clean case with a seed", Replay(CLEAN).run(script, corpus=corpus), 2,
        f"lens-eval: undecided: cases/{CLEAN}/case.toml: lens (a row of gates.toml), defect,",
    )  # fmt: skip


def malformed_lens_refused(script):
    corpus = Path(tempfile.mkdtemp(dir=workdir), "lens")
    shutil.copytree(CORPUS, corpus)
    rows = corpus / "gates.toml"
    rows.write_text(
        rows.read_text().replace(
            '"Was the codebase checked for something that already does this?"',
            '"The codebase was checked."',
        )
    )
    return expect(
        "a lens whose checklist holds a statement", Replay(SEEDED).run(script, corpus=corpus), 2,
        "lens-eval: undecided: gates.toml: lens 'architect': checklist must be a"
        " non-empty list of yes or no questions",
    )  # fmt: skip


def record_copies_replies(script):
    replay = Replay(SEEDED)
    out = Path(tempfile.mkdtemp(dir=workdir))
    rc, text = replay.run(script, "--record", str(out))
    for kept in sorted((RECORDED / SEEDED).iterdir()):
        copy = out / SEEDED / kept.name
        if not copy.exists() or copy.read_bytes() != kept.read_bytes():
            return f"--record did not keep {kept.name} as recorded (exit {rc}): {text.strip()}"
    return None


# (case, control it proves, text in run/lens-eval.py, the text with the control removed)
CASES = [
    (measured_counts, "the case named to the CLI", 'os.environ["GATES_CORPUS_CASE"] = f"lens/{name}"', 'os.environ["GATES_CORPUS_CASE"] = name'),
    (seed_finding_catches, "a finding inside the seed catching", 'at = [f for f in hits if f["path"] == "spec.md" and first <= f["line"] <= last]', "at = []"),
    (finding_off_seed_misses, "the seed's lines", 'f["path"] == "spec.md" and first <= f["line"] <= last', 'f["path"] == "spec.md"'),
    (unrelated_finding_misses, "a catch naming its gap", 'any(t.lower() in f["claim"].lower() for t in case["terms"])', "True"),
    (nit_does_not_catch, "a nit never catching", 'FLAGGED = ("error", "warning")', 'FLAGGED = ("error", "warning", "nit")'),
    (clean_case_flagged, "a flagged clean spec", 'return ("flagged", shown) if hits else', 'return ("clean", shown) if hits else'),
    (clean_sits_every_measured_lens, "every measured lens on a clean spec", 'return [(lid, "checklist") for lid in measured] + [(None, "none")]', 'return [(None, "none")]'),
    (arms_read_their_own_lens, "each arm reading its own form of the lens", "blocks = [review.lens_text(lenses[lid], arm)] if lid else []", 'blocks = [review.lens_text(lenses[lid], "checklist")] if lid else []'),
    (bar_miss_fails_the_run, "a missed bar failing the run", "    if not met:\n        return 1\n", "    if False:\n        return 1\n"),
    (ablation_keeps_a_clear_gain, "the half-width of the difference", "return Z * math.sqrt(2 * p * (1 - p) / n)", "return Z * math.sqrt(2 * p * (1 - p))"),
    (ablation_cuts_no_gain, "a lens cut unless it beats none", "'keep' if gain > hw else 'cut'", "'keep' if gain >= hw else 'cut'"),
    (role_line_removed_without_gain, "a role line kept only when it beats bare", 'role = "keep" if arms["checklist"] > arms["bare"] else "remove"', 'role = "keep" if arms["checklist"] >= arms["bare"] else "remove"'),
    (unmeasured_lens_reported, "an unmeasured lens named", "        if not seeded and not clean:\n", "        if False:\n"),
    (undecided_never_counts, "an undecided seat", '                outcome, detail = "undecided", str(e)\n', '                outcome, detail = "caught", str(e)\n'),
    (open_alternative_golden_cases, "a finding inside the seed catching", 'at = [f for f in hits if f["path"] == "spec.md" and first <= f["line"] <= last]', "at = []"),
    (open_alternative_check_removed, "a finding inside the seed catching", 'at = [f for f in hits if f["path"] == "spec.md" and first <= f["line"] <= last]', "at = []"),
    (malformed_case_refused, "the case's shape checked", "        if not (seeded or clean):\n", "        if False:\n"),
    (malformed_lens_refused, "each lens row checked", 'lenses[row["id"]] = review.lens_of(row, "gates.toml")', 'lenses[row["id"]] = {**row, "classes": ["medium"]}'),
    (record_copies_replies, "the replies recorded", "(out / f\"{seat['seat'].replace(' ', '-')}.json\").write_bytes(data)", "pass"),
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
                f"FAIL without {control}: the mutation matches {source.count(old)} places in run/lens-eval.py, not 1"
            )
            continue
        # lens-eval.py imports review.py and gate.py from its own directory.
        mutant_dir = Path(tempfile.mkdtemp(dir=workdir))
        for sibling in ("review.py", "gate.py"):
            shutil.copy(ROOT / "run" / sibling, mutant_dir / sibling)
        mutant = mutant_dir / "lens-eval.py"
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
    print("\nlens eval tests " + ("FAILED" if failed else "passed"))
    return failed


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
