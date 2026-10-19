#!/usr/bin/env python3
"""Regression tests for run/review.py: the review row's seats, budgets and verifier pass.

    .ci/test-review.py

Each case builds a repository from .ci/fixtures/review, puts its recorded-reply
`claude` first on PATH, runs one review and returns why it failed, or None. No
case calls a model: the reproduced case's replies are real captures, and a case
that needs another answer writes it into the same captured envelope. Every case
must pass against run/review.py, then fail against a copy with the one control
it exists for removed, so a case that would pass without its control is refused.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REVIEW = ROOT / "run/review.py"
RUNNER = ROOT / "run/gate.py"
FIXTURES = ROOT / ".ci/fixtures/review"
# Planted so a case can show no seat and no reproducing command receives them.
PLANTED = "planted-by-test-review"
ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@invalid",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@invalid",
    "PYTHONDONTWRITEBYTECODE": "1",
    "ANTHROPIC_API_KEY": PLANTED,
    "GH_TOKEN": PLANTED,
    "SSH_AUTH_SOCK": PLANTED,
    "PLANTED_ARBITRARY": PLANTED,
}
FLOOR = "    return part // whole * 100"
GUARD = "    return part * 100 / whole"
REPRODUCE = 'python3 -c "import calc; assert calc.percent(1, 4) == 25"'

workdir = Path(tempfile.mkdtemp())


def finding(**over):
    base = {
        "severity": "error",
        "path": "calc.py",
        "line": 5,
        "quote": FLOOR,
        "claim": "floor division truncates every share below 100% to 0",
        "reproduce": REPRODUCE,
    }
    return {**base, **over}


class Fixture:
    def __init__(self, script, step="floor", replies="reproduced"):
        self.script = script
        self.dir = Path(tempfile.mkdtemp(dir=workdir))
        self.repo, self.store = self.dir / "repo", self.dir / "store"
        self.repo.mkdir()
        self.run("bash", str(FIXTURES / "build.sh"), "--replies", replies, step)
        self.stub = self.repo / ".stub"

    def run(self, *argv, env=None):
        proc = subprocess.run(
            argv, cwd=self.repo, env=env or ENV, capture_output=True, text=True
        )
        if proc.returncode:
            raise RuntimeError(f"{argv}: {proc.stdout}{proc.stderr}")
        return proc.stdout

    def review(self, author="claude-opus-5-5", gate="code-review"):
        env = {**ENV, "PATH": f"{self.stub}:{ENV['PATH']}"}
        proc = subprocess.run(
            [sys.executable, str(self.script), "--gate", gate, "--base", "base",
             "--author", author, "--store", str(self.store)],
            cwd=self.repo, env=env, capture_output=True, text=True,
        )  # fmt: skip
        return proc.returncode, proc.stdout + proc.stderr

    def reply(self, seat, answer, model="claude-sonnet-5-5", **usage):
        """A seat's reply: the captured finder envelope around `answer`."""
        env = json.loads((FIXTURES / "replies/reproduced/finder.json").read_text())
        env["result"] = answer if isinstance(answer, str) else json.dumps(answer)
        env["usage"].update(usage)
        env["modelUsage"] = {model: env["modelUsage"]["claude-sonnet-5-5"]}
        (self.stub / "replies" / f"{seat}.json").write_text(json.dumps(env))

    def sleep(self, seat, seconds):
        (self.stub / "replies" / f"{seat}.sleep").write_text(str(seconds))

    def registry(self, text):
        (self.repo / ".github/gates.toml").write_text(text)
        self.run("git", "commit", "-qam", "registry")

    def row(self, extra):
        """The fixture's review row with `extra` lines appended, committed at the head."""
        path = self.repo / ".github/gates.toml"
        self.registry(path.read_text() + extra + "\n")

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


def expect(name, result, want, *needles):
    rc, out = result
    missing = [n for n in needles if n not in out]
    if rc != want or missing:
        return f"{name}: wanted exit {want} with {missing}, got {rc}:\n{out}"
    return None


# --- the verifier pass -----------------------------------------------------------------


def reproduced_finding_blocks(script):
    f = Fixture(script)
    why = expect(
        "a reproduced finding", f.review(), 1,
        "F1 error calc.py:5: Floor division before multiplying truncates the ratio",
        ": verified, blocks", "review: fail: 1 verified blocking finding",
    )  # fmt: skip
    if why:
        return why
    rec = f.records()[-1]
    seen = [(s["seat"], s["model"]) for s in rec["review"]["seats"]]
    if rec["verdict"] != "fail" or rec["review"]["findings"][0]["blocks"] is not True:
        return f"the record does not say the finding blocks: {rec}"
    if seen != [("finder", "claude-sonnet-5-5"), ("verifier F1", "claude-opus-5-5")]:
        return f"the seats were {seen}"
    return None


def unreproduced_finding_downgraded(script):
    f = Fixture(script, "guard", "unreproduced")
    why = expect(
        "a finding whose check passes at the head", f.review(), 0,
        "F1 error calc.py:5: percent(1, 0) divides by zero",
        "downgraded: its reproducing check passed at the head (exit 0)",
        "review: pass: no verified blocking finding (1 downgraded)",
    )  # fmt: skip
    if why:
        return why
    kept = f.records()[-1]["review"]["findings"]
    if len(kept) != 1 or kept[0]["blocks"] is not False or "downgraded" not in kept[0]:
        return f"the downgraded finding was not kept in the record: {kept}"
    if [c["seat"] for c in f.calls()] != ["finder"]:
        return f"a verifier sat on a finding that reproduction already refuted: {f.calls()}"
    return None


def misquoted_finding_downgraded(script):
    f = Fixture(script)
    f.reply("finder", {"findings": [finding(line=4)]})
    return expect(
        "a finding quoting another line", f.review(), 0,
        "downgraded: its quote is not line 4 of calc.py at the head",
    )  # fmt: skip


def unrunnable_check_downgraded(script):
    f = Fixture(script)
    f.reply("finder", {"findings": [finding(reproduce="no-such-checker calc.py")]})
    return expect(
        "a reproducing check that cannot run", f.review(), 0,
        "downgraded: its reproducing check could not run (exit 127)",
    )  # fmt: skip


def verifier_dissent_downgrades(script):
    f = Fixture(script)
    no = {
        "reproduced": False,
        "reason": "the assertion fails on import, not on the claim",
    }
    f.reply("verifier-F1", no, model="claude-opus-5-5")
    return expect(
        "a finding the verifier does not reproduce", f.review(), 0,
        "downgraded: the verifier did not reproduce it: the assertion fails on import",
    )  # fmt: skip


def warning_does_not_block(script):
    f = Fixture(script)
    f.reply("finder", {"findings": [finding(severity="warning")]})
    why = expect(
        "a warning", f.review(), 0, "F1 warning calc.py:5:", ": does not block"
    )
    if not why and [c["seat"] for c in f.calls()] != ["finder"]:
        why = f"a verifier sat on a warning: {f.calls()}"
    return why


# --- budgets ----------------------------------------------------------------------------


def over_token_budget_errors(script):
    f = Fixture(script)
    f.reply("finder", {"findings": []}, cache_creation_input_tokens=70_000)
    why = expect(
        "a finder over its token budget", f.review(), 2,
        "review: error: the finder on claude-sonnet-5-5 broke its budget:"
        " 70,002 input tokens of 60,000",
    )  # fmt: skip
    rec = (f.records() or [{}])[-1]
    if not why and (rec.get("verdict") != "error" or not rec["review"]["seats"]):
        why = f"the breach was not recorded as an error naming its seat: {rec}"
    return why


def finder_timeout_errors(script):
    f = Fixture(script)
    f.row("budget = { finder_seconds = 1 }")
    f.sleep("finder", 4)
    return expect(
        "a finder past its time", f.review(), 2,
        "review: error: the finder on claude-sonnet-5-5 ran past its 1s budget",
    )  # fmt: skip


def verifier_timeout_errors(script):
    f = Fixture(script)
    f.row("budget = { verifier_seconds = 1 }")
    f.sleep("verifier-F1", 4)
    return expect(
        "a verifier pass past its time", f.review(), 2,
        "review: error: the verifier F1 on claude-opus-5-5 ran past the verifier pass's 1s budget",
    )  # fmt: skip


# --- the harness and the seats' context ----------------------------------------------------


def api_key_login_refused(script):
    f = Fixture(script)
    login = {"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"}
    (f.stub / "replies/auth.json").write_text(json.dumps(login))
    why = expect(
        "an API-key login", f.review(), 2,
        "review: error: refused: claude is logged in with 'api_key' on 'firstParty'",
    )  # fmt: skip
    if not why and f.calls():
        why = f"a seat sat on an API-key login: {f.calls()}"
    return why


def seats_get_no_credentials(script):
    f = Fixture(script)
    probe = 'echo "gh=${GH_TOKEN:-unset} key=${ANTHROPIC_API_KEY:-unset}"; exit 1'
    f.reply("finder", {"findings": [finding(reproduce=probe)]})
    f.reply(
        "verifier-F1", {"reproduced": False, "reason": "no"}, model="claude-opus-5-5"
    )
    why = expect("a credential probe", f.review(), 0, "downgraded")
    calls = f.calls()
    leaked = [c["seat"] for c in calls if c["credentials"]]
    if not why and leaked:
        why = f"seats {leaked} received a planted credential"
    if not why and "gh=unset key=unset" not in calls[-1]["prompt"]:
        why = f"the reproducing command saw a credential: {calls[-1]['prompt'][-300:]}"
    return why


def reproduction_gets_allowlisted_env(script):
    f = Fixture(script)
    probe = 'echo "ssh=${SSH_AUTH_SOCK:-unset} other=${PLANTED_ARBITRARY:-unset} path=${PATH:+set}"; exit 1'
    f.reply("finder", {"findings": [finding(reproduce=probe)]})
    f.reply(
        "verifier-F1", {"reproduced": False, "reason": "no"}, model="claude-opus-5-5"
    )
    why = expect("an environment probe", f.review(), 0, "downgraded")
    seen = f.calls()[-1]["prompt"]
    if not why and "ssh=unset other=unset path=set" not in seen:
        why = f"the reproducing command saw {seen[-300:]}"
    return why


def seats_sit_fresh_and_bare(script):
    f = Fixture(script)
    f.reply("finder", {"notes": "SENTINEL-REASONING", "findings": [
        finding(),
        finding(line=3, quote="SENTINEL-OTHER", claim="SENTINEL-OTHER-FINDING"),
    ]})  # fmt: skip
    why = expect("two findings", f.review(), 1, "F2 error calc.py:3:", "downgraded")
    calls = f.calls()
    if why:
        return why
    if [c["seat"] for c in calls] != ["finder", "verifier-F1"]:
        return f"the seats were {[c['seat'] for c in calls]}"
    for c in calls:
        argv = c["argv"]

        def value(flag, argv=argv):
            return argv[argv.index(flag) + 1] if flag in argv else None

        bare = (
            all(a in argv for a in ("--no-session-persistence", "--strict-mcp-config"))
            and value("--tools") == value("--setting-sources") == ""
        )
        if not bare or {"--resume", "--continue", "-c", "-r"} & set(argv):
            return (
                f"{c['seat']} did not sit fresh, toolless and without settings: {argv}"
            )
        if c["cwd"]:
            return f"{c['seat']} sat in a directory holding {c['cwd']}"
    verifier = calls[1]["prompt"]
    if "SENTINEL" in verifier or FLOOR.strip() not in verifier:
        return f"the verifier saw more than the change and its one finding:\n{verifier}"
    return None


def independence_labels(script):
    def seats(author, reviewers=None, finder_model="claude-sonnet-5-5"):
        f = Fixture(script)
        if reviewers:
            text = (f.repo / ".github/gates.toml").read_text()
            old = 'reviewers = ["claude-sonnet-5-5", "claude-opus-5-5"]'
            f.registry(text.replace(old, f"reviewers = {json.dumps(reviewers)}"))
        f.reply("finder", {"findings": []}, model=finder_model)
        rc, out = f.review(author=author)
        recs = f.records()
        return rc, out, recs[-1]["review"]["seats"] if recs else []

    cases = [
        ("claude-opus-5-5", None, "claude-sonnet-5-5", {"author": "cross-model"}),
        ("claude-sonnet-5-5", None, "claude-opus-5-5", {"author": "cross-model"}),
        ("claude-fable-5-1", None, "claude-sonnet-5-5", {"author": "cross-model"}),
        ("unknown", None, "claude-sonnet-5-5", {"author": "context-only"}),
        ("claude-opus-5-5", ["claude-opus-5-5"], "claude-opus-5-5", {"author": "context-only"}),
    ]  # fmt: skip
    for author, reviewers, model, label in cases:
        rc, out, sat = seats(author, reviewers, model)
        got = [(s["model"], s["independence"]) for s in sat]
        if rc != 0 or got != [(model, label)]:
            return f"author {author}, reviewers {reviewers}: wanted {[(model, label)]}, got {got} (exit {rc}):\n{out}"
    return None


def fallback_model_errors(script):
    f = Fixture(script)
    f.reply("finder", {"findings": []}, model="claude-haiku-5-5")
    return expect(
        "a finder answered by another model", f.review(), 2,
        "asked claude-sonnet-5-5 but was answered by claude-haiku-5-5",
    )  # fmt: skip


def unreadable_finding_errors(script):
    f = Fixture(script)
    f.reply("finder", {"findings": [finding(severity="critical")]})
    why = expect(
        "a finding of no known severity", f.review(), 2, "finding 1 is not a finding"
    )
    if why:
        return why
    f.reply("finder", "Looks fine to me.")
    return expect("a reply with no JSON", f.review(), 2, "gave no readable answer")


# --- the row and the runner -----------------------------------------------------------------


def bad_row_errors(script):
    f = Fixture(script)
    f.row('budget = { finder_seconds = "5" }')
    why = expect("a budget of text", f.review(), 2, "budget may set only")
    if why:
        return why
    text = (f.repo / ".github/gates.toml").read_text()
    f.registry(
        text.replace(
            'reviewers = ["claude-sonnet-5-5", "claude-opus-5-5"]', "reviewers = []"
        )
    )
    why = expect("no reviewers", f.review(), 2, "reviewers must be a non-empty list")
    if not why and [r["verdict"] for r in f.records()] != ["error", "error"]:
        why = f"a bad row was not recorded as an error: {f.records()}"
    return why


CHECK_ROW = """
[[gate]]
id = "py"
inputs = ["**/*.py"]
run = "true"
"""


def check_row_never_reviewed(script):
    f = Fixture(script)
    f.row(CHECK_ROW)
    why = expect(
        "a check row named as a review", f.review(gate="py"), 2, "no review row 'py'"
    )
    if not why and f.records():
        why = f"an error was recorded against a check row: {f.records()}"
    return why


def change_another_row_claims(script):
    f = Fixture(script)
    text = (
        (f.repo / ".github/gates.toml")
        .read_text()
        .replace('inputs = ["**"]', 'inputs = ["docs/**"]')
    )
    f.registry(text + CHECK_ROW)
    f.run("git", "tag", "-f", "base")
    (f.repo / "calc.py").write_text("def percent(part, whole):\n    return 0\n")
    f.run("git", "commit", "-qam", "claimed")
    why = expect(
        "a change only a check row claims", f.review(), 0,
        "review: pass: nothing under its inputs changed, so no seat sat",
    )  # fmt: skip
    if not why and f.calls():
        why = f"a seat sat on a change outside the row's inputs: {f.calls()}"
    return why


def review_row_folds_in_shadow(script):
    def fold(f):
        f.run(
            sys.executable,
            str(RUNNER),
            "run",
            "--base",
            "base",
            "--store",
            str(f.store),
        )
        f.review()
        proc = subprocess.run(
            [sys.executable, str(RUNNER), "fold", "--base", "base", "--store", str(f.store)],
            cwd=f.repo, env=ENV, capture_output=True, text=True,
        )  # fmt: skip
        return proc.returncode, proc.stdout + proc.stderr

    shadow = Fixture(script)
    why = expect(
        "a red review row in shadow", fold(shadow), 0,
        "shadow code-review: fail: 1 verified blocking finding (in shadow since 2026-10-19",
    )  # fmt: skip
    if why:
        return why
    live = Fixture(script)
    live.registry(
        (live.repo / ".github/gates.toml")
        .read_text()
        .replace("shadow_since = 2026-10-19\n", "")
    )
    return expect(
        "an established red review row", fold(live), 1,
        "FAIL code-review: fail: 1 verified blocking finding",
    )  # fmt: skip


# (case, control it proves, text in run/review.py, the text with the control removed)
CASES = [
    (reproduced_finding_blocks, "a verified finding blocks", "f.update(verified=why is None, blocks=why is None)", "f.update(verified=why is None, blocks=False)"),
    (unreproduced_finding_downgraded, "a check passing at the head downgrades", "    if rc == 0:\n", "    if False:\n"),
    (misquoted_finding_downgraded, "the quote check", "    why = quoted(head, finding)\n", "    why = None\n"),
    (unrunnable_check_downgraded, "an unrunnable check downgrades", "    if rc in (126, 127):\n", "    if False:\n"),
    (verifier_dissent_downgrades, "the verifier's answer", '    if not answer["reproduced"]:\n', "    if False:\n"),
    (warning_does_not_block, "only an error can block", '        if f["severity"] != "error":\n', "        if False:\n"),
    (over_token_budget_errors, "the token budget", "    if over:\n", "    if False:\n"),
    (finder_timeout_errors, "a seat's wall-time budget", "timeout=max(seconds, 0.001)", "timeout=None"),
    (verifier_timeout_errors, "the verifier pass's deadline", 'deadline = time.monotonic() + budget["verifier_seconds"]', "deadline = time.monotonic() + 3600"),
    (api_key_login_refused, "the subscription login", "method not in AUTH_METHODS", "False"),
    (seats_get_no_credentials, "the credential scrub", "if k.startswith(keep) or not any(w in k.upper() for w in SECRET_WORDS)", "if True"),
    (reproduction_gets_allowlisted_env, "the reproduction's environment allowlist", "if k in REPRODUCE_ENV or k.startswith(\"LC_\")", "if True"),
    (seats_sit_fresh_and_bare, "a toolless seat", '"--tools", "", ', ""),
    (independence_labels, "the context-only label", 'return "cross-model" if other != "unknown" and model != other', 'return "cross-model" if model != other'),
    (fallback_model_errors, "the served-model check", "    if served and served != [model]:\n", "    if False:\n"),
    (unreadable_finding_errors, "a finding's severity is checked", 'and f.get("severity") in SEVERITIES', ""),
    (bad_row_errors, "the row's budget is checked", "and all(type(v) is int and v > 0 for v in extra.values())", ""),
    (check_row_never_reviewed, "only a review row is reviewed", ' or row["kind"] != "review"', ""),
    (change_another_row_claims, "a row reads only its own and unclaimed paths", 'if p and (gate.matches(row, p) or not any(gate.matches(r, p) for r in rows))', "if p"),
    (review_row_folds_in_shadow, "the record carries the verdict", '            "verdict": verdict,\n', '            "verdict": "pass",\n'),
]  # fmt: skip


def main():
    failed = 0
    source = REVIEW.read_text()
    for case, control, old, new in CASES:
        why = case(REVIEW)
        if why:
            failed = 1
            print(f"FAIL {case.__name__}: {why}")
            continue
        print(f"ok   {case.__name__}")
        if source.count(old) != 1:
            failed = 1
            print(
                f"FAIL without {control}: the mutation matches {source.count(old)} places in run/review.py, not 1"
            )
            continue
        # review.py imports gate.py from its own directory, so the copy takes it along.
        mutant_dir = Path(tempfile.mkdtemp(dir=workdir))
        shutil.copy(RUNNER, mutant_dir / "gate.py")
        mutant = mutant_dir / "review.py"
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
    print("\nreview tests " + ("FAILED" if failed else "passed"))
    return failed


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
