#!/usr/bin/env python3
"""Regression tests for run/corpus.py: a review lens measured on its seeded corpus.

    .ci/test-corpus.py

Each case replays corpus cases through `.ci/fixtures/review/corpus-case.sh`,
whose recorded `claude` answers from the real seat replies captured on
2026-10-19 under .ci/fixtures/review/corpus, and returns why it failed, or None.
No case calls a model; a case that needs an outcome the live run did not give
writes the finder's answer into its real envelope. Every case must pass against
run/corpus.py, then fail against a copy with the one control it exists for
removed. The first case pins the measured counts the README quotes.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_PY = ROOT / "run/corpus.py"
FIXTURES = ROOT / ".ci/fixtures/review"
ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "PYTHONDONTWRITEBYTECODE": "1",
}
# The live run of 2026-10-19 through the subscription login, finder Sonnet, verifier Opus.
MEASURED = {
    "design": "corpus: design: caught 5 of 6 seeded, 0 false alarms on 6 clean, 0 undecided",
    "licence": "corpus: licence: caught 3 of 4 seeded, 0 false alarms on 4 clean, 0 undecided",
    "release": "corpus: release: caught 0 of 4 seeded, 0 false alarms on 4 clean, 0 undecided",
}

workdir = Path(tempfile.mkdtemp())


def answer(severity, line, path="styles/button.css", claim="a planted finding"):
    return json.dumps(
        {
            "findings": [
                {
                    "severity": severity,
                    "path": path,
                    "line": line,
                    "quote": "",
                    "claim": claim,
                }
            ]
        }
    )


class Replay:
    """One case set up by corpus-case.sh in a fresh directory."""

    def __init__(self, lens, case, finder=None):
        self.dir = Path(tempfile.mkdtemp(dir=workdir))
        argv = ["bash", str(FIXTURES / "corpus-case.sh"), lens, case]
        subprocess.run(
            [*argv, *([finder] if finder else [])], cwd=self.dir, env=ENV, check=True
        )
        self.lens, self.case = lens, case

    def run(self, script, *extra, cases=None, corpus=None, env=None):
        env = {
            **ENV,
            **(env or {}),
            "PATH": f"{self.dir / '.stub'}:{ENV['PATH']}",
            "CLAUDE_RECORDED_REPLIES": str(self.dir / "replies"),
        }
        names = [self.case] if cases is None else cases
        argv = [
            sys.executable,
            str(script),
            "--corpus",
            str(corpus or ROOT / "corpus" / self.lens),
        ]
        argv += [a for n in names for a in ("--case", n)]
        argv += ["--store", str(Path(tempfile.mkdtemp(dir=workdir))), *extra]
        proc = subprocess.run(
            argv, cwd=self.dir, env=env, capture_output=True, text=True
        )
        return proc.returncode, proc.stdout + proc.stderr


def expect(what, got, status, *texts):
    rc, out = got
    if rc != status:
        return f"{what}: exit {rc}, not {status}: {out.strip()}"
    missing = [t for t in texts if t not in out]
    return f"{what}: no {missing[0]!r} in: {out.strip()}" if missing else None


def measured_counts(script):
    """Every case the live run recorded, replayed, gives the counts of that run."""
    for lens, want in MEASURED.items():
        # A case added since, such as licence's gpl-crate and gpl-pnpm, has no reply to replay.
        recorded = sorted(os.listdir(FIXTURES / "corpus" / lens))
        replay = Replay(lens, recorded[0])
        shutil.rmtree(replay.dir / "replies" / lens)
        shutil.copytree(FIXTURES / "corpus" / lens, replay.dir / "replies" / lens)
        rc, out = replay.run(script, cases=recorded)
        if want not in out:
            return f"{lens}: no {want!r} in: {out.strip()}"
    return None


def verified_block_catches(script):
    got = Replay("licence", "gpl-dependency").run(script)
    return expect(
        "a GPL dependency the verifier upheld", got, 0,
        "corpus: licence/gpl-dependency: caught: F1 error package.json:9:",
        "corpus: licence: caught 1 of 1 seeded",
    )  # fmt: skip


def verifier_clearance_misses(script):
    got = Replay("release", "sign-unapproved").run(script)
    return expect(
        "an approval bypass the verifier cleared", got, 1,
        "corpus: release/sign-unapproved: missed: no verified blocking finding at release/sign.sh:10-13"
        " (F1 error release/sign.sh:10:",
    )  # fmt: skip


def warning_catches_where_findings_fix(script):
    got = Replay("design", "row-overlap").run(script)
    return expect(
        "a design warning at the seed",
        got,
        0,
        "corpus: design/row-overlap: caught: F1 warning styles/row.css:3:",
    )


def nit_does_not_catch(script):
    got = Replay("design", "colour-literal", answer("nit", 19)).run(script)
    return expect(
        "a nit at the seed", got, 1,
        "corpus: design/colour-literal: missed: no error or warning finding at styles/button.css:19-19"
        " (F1 nit styles/button.css:19: a planted finding)",
    )  # fmt: skip


def finding_off_seed_misses(script):
    got = Replay("design", "colour-literal", answer("warning", 2)).run(script)
    return expect(
        "a warning off the seed", got, 1, "corpus: design/colour-literal: missed:"
    )


def clean_case_flagged(script):
    got = Replay("design", "colour-token", answer("warning", 19)).run(script)
    return expect(
        "a warning on a clean case", got, 1,
        "corpus: design/colour-token: flagged: F1 warning styles/button.css:19: a planted finding",
        "1 false alarm on 1 clean",
    )  # fmt: skip


def miss_fails_the_run(script):
    replay = Replay("design", "padding-off-scale")
    shutil.copytree(
        FIXTURES / "corpus/design/label-kept", replay.dir / "replies/design/label-kept"
    )
    got = replay.run(script, cases=["padding-off-scale", "label-kept"])
    return expect(
        "a real miss beside a clean case", got, 1,
        "corpus: design/padding-off-scale: missed: no error or warning finding at styles/button.css:5-5 (no finding)",
        "corpus: design: caught 0 of 1 seeded, 0 false alarms on 1 clean",
    )  # fmt: skip


def undecided_never_counts(script):
    replay = Replay("licence", "gpl-dependency")
    (replay.dir / "replies/licence/gpl-dependency/verifier-F1.json").unlink()
    got = replay.run(script)
    return expect(
        "a verifier with no reply", got, 2,
        "corpus: licence/gpl-dependency: undecided:",
        "caught 0 of 1 seeded, 0 false alarms on 0 clean, 1 undecided",
    )  # fmt: skip


def malformed_case_refused(script):
    corpus = Path(tempfile.mkdtemp(dir=workdir), "design")
    shutil.copytree(ROOT / "corpus/design", corpus)
    case = corpus / "cases/label-kept/case.toml"
    case.write_text(
        case.read_text() + '[[seed]]\npath = "components/dialog.html"\nlines = [4, 4]\n'
    )
    got = Replay("design", "label-kept").run(script, corpus=corpus)
    return expect(
        "a clean case with a seed", got, 2,
        "corpus: undecided: cases/label-kept/case.toml: a defect with [[seed]]",
    )  # fmt: skip


def record_copies_replies(script):
    replay = Replay("licence", "gpl-dependency")
    out = Path(tempfile.mkdtemp(dir=workdir))
    rc, text = replay.run(script, "--record", str(out))
    source = FIXTURES / "corpus/licence/gpl-dependency"
    for name in ("finder.json", "verifier-F1.json", "version.txt"):
        kept = out / "licence/gpl-dependency" / name
        if not kept.exists() or kept.read_bytes() != (source / name).read_bytes():
            return (
                f"--record did not keep {name} as recorded (exit {rc}): {text.strip()}"
            )
    return None


def same_size_change_reviewed(script):
    """A change the same size as its base file still reaches the finder."""
    corpus = Path(tempfile.mkdtemp(dir=workdir), "release")
    shutil.copytree(ROOT / "corpus/release", corpus)
    # Equal size, equal mtime and an untrusted ctime: the index's stat check alone sees no change.
    for tree in ("base", "cases/version-bump/change"):
        os.utime(corpus / tree / "client/package.json", (1_000_000_000, 1_000_000_000))
    replay = Replay("release", "version-bump")
    untrusted = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.trustctime",
        "GIT_CONFIG_VALUE_0": "false",
    }
    got = replay.run(script, corpus=corpus, env=untrusted)
    log = replay.dir / ".stub/calls.jsonl"
    calls = log.read_text().splitlines() if log.exists() else []
    seen = [json.loads(c)["prompt"] for c in calls if json.loads(c)["seat"] == "finder"]
    if not any('+  "version": "2.4.0",' in p for p in seen):
        return (
            f"the finder never saw the version bump (exit {got[0]}): {got[1].strip()}"
        )
    return expect(
        "a same-size version bump", got, 0, "corpus: release/version-bump: clean:"
    )


# (case, control it proves, text in run/corpus.py, the text with the control removed)
CASES = [
    (measured_counts, "the case named to the CLI", '"GATES_CORPUS_CASE": f"{conf[\'lens\']}/{name}"', '"GATES_CORPUS_CASE": name'),
    (verified_block_catches, "a finding at the seed catching", 'caught = [f for f in hits if at_seed(f, case["seed"])]', "caught = []"),
    (verifier_clearance_misses, "only a verified block catching a blocking lens", 'hits = [f for f in findings if f.get("blocks")]', 'hits = [f for f in findings if f.get("severity") == "error"]'),
    (warning_catches_where_findings_fix, "a warning catching where findings fix", 'FLAGGED = ("error", "warning")', 'FLAGGED = ("error",)'),
    (nit_does_not_catch, "a nit never catching", 'FLAGGED = ("error", "warning")', 'FLAGGED = ("error", "warning", "nit")'),
    (finding_off_seed_misses, "the seed's lines", 'and s["lines"][0] <= finding.get("line", 0) <= s["lines"][1]', "and True"),
    (clean_case_flagged, "a flagged clean case", '        if hits:\n            return "flagged"', '        if False:\n            return "flagged"'),
    (miss_fails_the_run, "a miss failing the run", 'if count["missed"] or count["flagged"]:', 'if count["flagged"]:'),
    (undecided_never_counts, "an error verdict undecided", 'if rec["verdict"] == "error":', "if False:"),
    (malformed_case_refused, "the case's shape checked", 'if set(case) - {"defect", "seed", "clean"} or not (seeded or clean):', "if False:"),
    (record_copies_replies, "the replies recorded", "(out / f\"{seat['seat'].replace(' ', '-')}.json\").write_bytes(data)", "pass"),
    (same_size_change_reviewed, "the index re-hashed before the change commits", '    git(repo, "rm", "-r", "-q", "--cached", ".")\n', ""),
]  # fmt: skip


def main():
    failed = 0
    source = CORPUS_PY.read_text()
    for case, control, old, new in CASES:
        why = case(CORPUS_PY)
        if why:
            failed = 1
            print(f"FAIL {case.__name__}: {why}")
            continue
        print(f"ok   {case.__name__}")
        if source.count(old) != 1:
            failed = 1
            print(
                f"FAIL without {control}: the mutation matches {source.count(old)} places in run/corpus.py, not 1"
            )
            continue
        # corpus.py runs review.py from its own directory, which imports gate.py from there.
        mutant_dir = Path(tempfile.mkdtemp(dir=workdir))
        for sibling in ("review.py", "gate.py"):
            shutil.copy(ROOT / "run" / sibling, mutant_dir / sibling)
        mutant = mutant_dir / "corpus.py"
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
    print("\ncorpus tests " + ("FAILED" if failed else "passed"))
    return failed


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
