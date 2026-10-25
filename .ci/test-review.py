#!/usr/bin/env python3
"""Regression tests for run/review.py: the review row's seats, budgets, verifier pass,
router, veto and dissent.

    .ci/test-review.py

Each case builds a repository from .ci/fixtures/review, puts its recorded-reply
`claude` first on PATH, runs one review or one route and returns why it failed,
or None. No case calls a model: the recorded sets hold real captures, and a case
that needs another answer writes it into a captured envelope. Every case
must pass against run/review.py, then fail against a copy with the one control
it exists for removed, so a case that would pass without its control is refused.
"""

import json
import os
import shutil
import socket
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

    def review(self, author="claude-opus-5-5", gate="code-review", size=None, **over):
        env = {**ENV, "PATH": f"{self.stub}:{ENV['PATH']}", **over}
        which = ["--class", size] if size else ["--gate", gate]
        proc = subprocess.run(
            [sys.executable, str(self.script), *which, "--base", "base",
             "--author", author, "--store", str(self.store)],
            cwd=self.repo, env=env, capture_output=True, text=True,
        )  # fmt: skip
        return proc.returncode, proc.stdout + proc.stderr

    def route(self, size="medium", author="claude-opus-5-5", **over):
        return self.review(author=author, size=size, **over)

    def fold(self):
        proc = subprocess.run(
            [sys.executable, str(RUNNER), "fold", "--base", "base", "--store", str(self.store)],
            cwd=self.repo, env=ENV, capture_output=True, text=True,
        )  # fmt: skip
        return proc.returncode, proc.stdout + proc.stderr

    def router(self, answer):
        self.reply("router", answer, model="claude-haiku-5-5")

    def change(self, files):
        """Commit `files` over the change, so base...head carries them too."""
        for rel, text in files.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.run("git", "add", "-A")
        self.run("git", "commit", "-qm", "more")

    def latest(self, gate="code-review"):
        return next(r for r in reversed(self.records()) if r["gate"] == gate)

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


def probed(script, probe, **over):
    """(fixture, review result, what the reproducing `probe(fixture)` printed to the verifier)."""
    f = Fixture(script)
    f.reply("finder", {"findings": [finding(reproduce=probe(f))]})
    f.reply(
        "verifier-F1", {"reproduced": False, "reason": "no"}, model="claude-opus-5-5"
    )
    result = f.review(**over)
    calls = f.calls()
    return f, result, calls[-1]["prompt"] if calls else ""


def reproduction_reads_no_home(script):
    # The jail hides the temporary directories too, so a home there proves nothing.
    home = Path(tempfile.mkdtemp(dir=ROOT, prefix=".test-review-home-"))
    try:
        (home / "secret").write_text(PLANTED)
        probe = f'cat "{home}/secret" && echo read=open || echo "read=""denied"; exit 1'
        _, result, seen = probed(script, lambda f: probe, HOME=str(home))
    finally:
        shutil.rmtree(home)
    why = expect("a probe of the user's home", result, 0, "downgraded")
    if not why and (PLANTED in seen or "read=denied" not in seen):
        why = f"the reproducing command read the user's home: {seen[-300:]}"
    return why


def reproduction_cannot_write_store(script):
    def probe(f):
        store = f.store / "records.jsonl"
        return f'echo forged >> "{store}" && echo write=open || echo "write=""denied"; exit 1'

    f, result, seen = probed(script, probe)
    why = expect("a probe of the verdict store", result, 0, "downgraded")
    if why:
        return why
    # The record quotes the probe, so only a line the probe wrote whole is a forgery.
    if "forged" in (f.store / "records.jsonl").read_text().splitlines() or (
        "write=denied" not in seen
    ):
        return f"the reproducing command wrote to the verdict store: {seen[-300:]}"
    return None


def reproduction_has_no_network(script):
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        connect = f"import socket; socket.create_connection(('127.0.0.1', {port}), 5)"
        probe = (
            f'python3 -c "{connect}" && echo net=open || echo "net=""denied"; exit 1'
        )
        _, result, seen = probed(script, lambda f: probe)
    why = expect("a probe of the network", result, 0, "downgraded")
    if not why and "net=denied" not in seen:
        why = f"the reproducing command reached the network: {seen[-300:]}"
    return why


def reproduction_lists_no_sibling_temp(script):
    sibling = Path(tempfile.mkdtemp(dir=workdir))
    (sibling / "note").write_text(PLANTED)
    probe = f'ls "{sibling}" && cat "{sibling}/note" && echo temp=open || echo "temp=""hidden"; exit 1'
    _, result, seen = probed(script, lambda f: probe)
    why = expect(
        "a probe of another task's temporary directory", result, 0, "downgraded"
    )
    if not why and (PLANTED in seen or "temp=hidden" not in seen):
        why = (
            f"the reproducing command read a sibling temporary directory: {seen[-300:]}"
        )
    return why


def reproduction_connects_to_no_socket(script):
    path = Path(tempfile.mkdtemp(dir=workdir)) / "s"
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        server.listen()
        connect = f"import socket; socket.socket(socket.AF_UNIX).connect('{path}')"
        probe = (
            f'python3 -c "{connect}" && echo sock=open || echo "sock=""refused"; exit 1'
        )
        _, result, seen = probed(script, lambda f: probe)
    why = expect("a probe of a local socket", result, 0, "downgraded")
    if not why and "sock=refused" not in seen:
        why = f"the reproducing command reached a local socket: {seen[-300:]}"
    return why


KEYCHAIN = """import ctypes
c = ctypes.CDLL(None)
kr = c.bootstrap_look_up(ctypes.c_uint.in_dll(c, "bootstrap_port"), b"com.apple.SecurityServer", ctypes.byref(ctypes.c_uint()))
print("keychain=" + ("reached" if kr == 0 else "refu" + "sed"))"""


def reproduction_reaches_no_mach_service(script):
    probe = (
        'launchctl print "gui/$(id -u)" >/dev/null 2>&1 && echo launchd=reached'
        ' || echo "launchd=""refused"\n'
        f"python3 - <<'PY'\n{KEYCHAIN}\nPY\nexit 1"
    )
    _, result, seen = probed(script, lambda f: probe)
    why = expect("a probe of launchd and the keychain daemon", result, 0, "downgraded")
    if not why and not ("launchd=refused" in seen and "keychain=refused" in seen):
        why = f"the reproducing command reached a Mach service: {seen[-300:]}"
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


def independence_labels(script, family_cases=False):
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
    # A dated or provider-prefixed id is the same family as the bare one.
    family = [
        ("claude-opus-5-5-20261001", ["claude-opus-5-5", "claude-sonnet-5-5"], "claude-sonnet-5-5", {"author": "cross-model"}),
        ("us.anthropic.claude-opus-5-5-v1:0", ["claude-opus-5-5"], "claude-opus-5-5", {"author": "context-only"}),
    ]  # fmt: skip
    cases += family if family_cases else []
    for author, reviewers, model, label in cases:
        rc, out, sat = seats(author, reviewers, model)
        got = [(s["model"], s["independence"]) for s in sat]
        if rc != 0 or got != [(model, label)]:
            return f"author {author}, reviewers {reviewers}: wanted {[(model, label)]}, got {got} (exit {rc}):\n{out}"
    return None


def independence_by_family(script):
    return independence_labels(script, family_cases=True)


def unnamed_model_errors(script):
    f = Fixture(script)
    f.reply("finder", {"findings": []})
    path = f.stub / "replies/finder.json"
    env = json.loads(path.read_text())
    del env["modelUsage"]
    path.write_text(json.dumps(env))
    return expect(
        "a finder reply naming no model", f.review(), 2,
        "asked claude-sonnet-5-5 but was answered by no named model",
    )  # fmt: skip


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


# --- the router ------------------------------------------------------------------------------

SKIP = {"class": "trivial", "reviews": []}


def router_skip_caught(script):
    f = Fixture(script)
    shutil.copy(FIXTURES / "replies/router-skips.json", f.stub / "replies/router.json")
    why = expect(
        "a router that skips the required review", f.route(), 1,
        "review: route: code-review sits: the router skipped it; convened by the rule:"
        " calc.py is under its inputs",
        "review: code-review: F1 error calc.py:5:", "review: fail: code-review fail",
    )  # fmt: skip
    if why:
        return why
    route = f.latest()["review"]["route"]
    if route["rows"]["code-review"] != {
        "sits": True, "rule": "calc.py is under its inputs", "router_skipped": True
    }:  # fmt: skip
        return f"the record does not say the router skipped it: {route}"
    call = f.calls()[0]
    argv = call["argv"]
    if (
        call["seat"] != "router"
        or argv[argv.index("--model") + 1] != "claude-haiku-5-5"
    ):
        return f"the first seat was not the Haiku router: {call['seat']} {argv}"
    if FLOOR.strip() in call["prompt"] or "+4 -2 calc.py" not in call["prompt"]:
        return f"the router was not given the paths alone:\n{call['prompt']}"
    return None


def router_cannot_lower_class(script):
    f = Fixture(script)
    f.router(SKIP)
    why = expect(
        "a router that sizes the change below its class", f.route(), 1,
        "review: route: class medium (given medium; the router cannot lower it to trivial)",
    )  # fmt: skip
    if not why and f.latest()["review"]["route"]["class"] != "medium":
        why = f"the recorded class is not medium: {f.latest()['review']['route']}"
    return why


TWO_ROWS = """
[[gate]]
id = "code-review"
kind = "review"
inputs = ["**/*.py"]
lens = "code"
reviewers = ["claude-sonnet-5-5", "claude-opus-5-5"]

[[gate]]
id = "docs-review"
kind = "review"
inputs = ["**/*.md"]
lens = "docs"
reviewers = ["claude-sonnet-5-5", "claude-opus-5-5"]
"""


def router_addition_reads_and_folds(script):
    f = Fixture(script)
    f.registry(TWO_ROWS)
    f.change({"calc.py": (FIXTURES / "base/calc.py").read_text()})
    f.run("git", "tag", "-f", "base")
    f.change({"calc.py": (FIXTURES / "floor/calc.py").read_text()})
    f.router({"class": "medium", "reviews": [
        {"gate": "code-review", "why": "calc.py is code"},
        {"gate": "docs-review", "why": "the docstring of percent changed"},
    ]})  # fmt: skip
    why = expect(
        "a row the router adds", f.route(), 1,
        "review: route: docs-review sits: named by the router: the docstring of percent changed",
        "review: docs-review: F1 error calc.py:5:",
    )  # fmt: skip
    if why:
        return why
    docs = [c for c in f.calls() if c["seat"] == "finder"][-1]["prompt"]
    if FLOOR.strip() not in docs:
        return f"the added row's finder did not read the change:\n{docs}"
    return expect(
        "the fold of a row selection left out", f.fold(), 1,
        "FAIL docs-review: fail: 1 verified blocking finding",
    )  # fmt: skip


def router_unavailable_falls_back(script):
    f = Fixture(script)
    (f.stub / "replies/router.json").unlink()
    why = expect(
        "a router with no reply", f.route(), 1,
        "review: route: the router gave no usable route: the router on claude-haiku-5-5"
        " exited 1 with no readable reply",
        "the rule routes alone", "review: route: code-review sits: calc.py is under its inputs",
    )  # fmt: skip
    if (
        not why
        and "router_skipped" in f.latest()["review"]["route"]["rows"]["code-review"]
    ):
        why = "an unavailable router was recorded as having skipped the row"
    return why


def router_unknown_gate_falls_back(script):
    f = Fixture(script)
    f.router({"class": "large", "reviews": [{"gate": "no-such-review", "why": "x"}]})
    return expect(
        "a router naming no registered row", f.route(), 1,
        "review: route: the router gave no usable route:",
        "names no review row; the rule routes alone",
        "review: route: class medium (given medium)\n",
    )  # fmt: skip


SECURITY_ROW = """
[[gate]]
id = "security-review"
kind = "review"
inputs = ["crypto/**"]
lens = "security"
reviewers = ["claude-sonnet-5-5", "claude-opus-5-5"]
"""


def sensitive_path_sizes_large(script):
    f = Fixture(script)
    f.row(SECURITY_ROW)
    f.change({"auth/token.py": "TTL = 60\n"})
    f.router(SKIP)
    why = expect(
        "a trivial claim on a sensitive path", f.route(size="trivial"), 1,
        "review: route: class large (given trivial; auth/token.py is a sensitive path)",
        "review: route: security-review sits: the router skipped it; convened by the rule:"
        " a large change gets the security lens",
    )  # fmt: skip
    if not why and "security-review" not in {r["gate"] for r in f.records()}:
        why = "the security row recorded no verdict"
    return why


def large_change_seats_security_lens(script):
    f = Fixture(script)
    f.row(SECURITY_ROW)
    f.router({"class": "large", "reviews": [{"gate": "code-review", "why": "code"}]})
    return expect(
        "a large change", f.route(size="large"), 1,
        "review: route: security-review sits: the router skipped it; convened by the rule:"
        " a large change gets the security lens",
        "review: security-review: F1 error calc.py:5:",
    )  # fmt: skip


def trivial_runs_no_review(script):
    f = Fixture(script, "guard", "unreproduced")
    f.router(SKIP)
    why = expect(
        "a trivial change", f.route(size="trivial"), 0,
        "review: route: code-review does not sit: a trivial change runs no review",
        "review: code-review: pass: not convened: a trivial change runs no review",
    )  # fmt: skip
    if why:
        return why
    if [c["seat"] for c in f.calls()] != ["router"]:
        return f"a seat other than the router sat on a trivial change: {f.calls()}"
    rec = f.latest()
    if rec["verdict"] != "pass" or rec["review"]["independence"] != "none":
        return f"the not-convened record is not a pass by no seat: {rec}"
    return None


def trivial_claim_without_router_errors(script):
    f = Fixture(script)
    found = [
        d for d in ENV["PATH"].split(os.pathsep) if d and shutil.which("claude", path=d)
    ]
    path = os.pathsep.join(d for d in ENV["PATH"].split(os.pathsep) if d not in found)
    why = expect(
        "a trivial claim with no claude on PATH", f.route(size="trivial", PATH=path), 2,
        "review: route: the router gave no usable route: claude is not on PATH",
        "review: code-review: error: not convened: the change is declared trivial"
        " and no router answer confirmed it",
    )  # fmt: skip
    if not why and f.latest()["verdict"] != "error":
        why = f"an unconfirmed trivial claim was recorded as {f.latest()}"
    if not why and f.calls():
        why = f"a seat sat with no claude on PATH: {f.calls()}"
    return why


def held_verdict_not_covered(script):
    f = Fixture(script)
    if f.review()[0] != 1:
        return "the floor change did not fail its direct review"
    f.router(SKIP)
    why = expect(
        "a trivial route after a failed review", f.route(size="trivial"), 0,
        "review: route: code-review does not sit",
    )  # fmt: skip
    if not why and f.latest()["verdict"] != "fail":
        why = f"a not-convened pass covered the failed review: {f.latest()}"
    return why


# --- the veto and dissent ------------------------------------------------------------------


def escalated_veto_blocks(script):
    f = Fixture(script, replies="escalated")
    why = expect(
        "a verifier's dissent the adjudicator overrules", f.review(author="claude-sonnet-5-5"), 1,
        "review: veto: F1 by the finder on claude-opus-5-5, upheld by the adjudicator F1"
        " on claude-opus-5-5",
        "review: dissent: F1: the verifier F1 on claude-sonnet-5-5 voted clears:",
        "overruled: the adjudicator F1 on claude-opus-5-5",
    )  # fmt: skip
    if why:
        return why
    rec = f.latest()["review"]
    dissent = rec["dissent"]
    if [(d["seat"], d["vote"], d["overruled_by"]) for d in dissent] != [
        ("verifier F1", "clears", "the adjudicator F1 on claude-opus-5-5")
    ]:
        return f"the dissent is not the verifier's, overruled by the adjudicator: {dissent}"
    judge = rec["seats"][-1]
    if judge["seat"] != "adjudicator F1" or judge["independence"] != {
        "author": "cross-model", "finder": "context-only", "verifier": "cross-model"
    }:  # fmt: skip
        return f"the adjudicator's seat is not recorded with its labels: {judge}"
    prompt = f.calls()[-1]["prompt"]
    if "## The dissent\nThe verifier found it not reproduced:" not in prompt:
        return f"the adjudicator was not shown the dissent:\n{prompt[-400:]}"
    return None


def adjudicator_clears_downgrades(script):
    f = Fixture(script, replies="escalated")
    shutil.copy(
        FIXTURES / "replies/adjudicator-clears.json",
        f.stub / "replies/adjudicator-F1.json",
    )
    why = expect(
        "an adjudicator agreeing with the verifier", f.review(author="claude-sonnet-5-5"), 0,
        "downgraded: the verifier did not reproduce it, nor did the adjudicator on claude-opus-5-5:",
        "review: dissent: F1: the finder on claude-opus-5-5 voted blocks:",
    )  # fmt: skip
    if not why and f.latest()["review"]["veto"]:
        why = (
            f"a cleared finding was recorded as a veto: {f.latest()['review']['veto']}"
        )
    return why


def no_stronger_seat_no_escalation(script):
    f = Fixture(script)
    no = {"reproduced": False, "reason": "not shown"}
    f.reply("verifier-F1", no, model="claude-opus-5-5")
    why = expect(
        "a dissent from the strongest seat", f.review(), 0,
        "downgraded: the verifier did not reproduce it: not shown",
    )  # fmt: skip
    if not why and [c["seat"] for c in f.calls()] != ["finder", "verifier-F1"]:
        why = f"a seat outranking no one was convened: {[c['seat'] for c in f.calls()]}"
    return why


def veto_names_its_seats(script):
    f = Fixture(script)
    why = expect("a verified finding", f.review(), 1, "review: veto: F1 by the finder")
    veto = f.latest()["review"]["veto"]
    want = [{"finding": "F1", "seat": "finder", "model": "claude-sonnet-5-5",
             "upheld_by": ["the verifier F1 on claude-opus-5-5"]}]  # fmt: skip
    if not why and veto != want:
        why = f"the veto was recorded as {veto}"
    return why


def downgrade_records_finder_dissent(script):
    f = Fixture(script, "guard", "unreproduced")
    why = expect("a refuted finding", f.review(), 0, "review: dissent: F1: the finder")
    dissent = f.latest()["review"]["dissent"]
    want = [("finder", "blocks", "its reproducing check passed at the head (exit 0)")]
    if not why and [(d["seat"], d["vote"], d["overruled_by"]) for d in dissent] != want:
        why = f"the finder's dissent was recorded as {dissent}"
    return why


def every_verdict_carries_the_fields(script):
    blocked = Fixture(script)
    blocked.review()
    lone = Fixture(script)
    lone.reply("finder", {"findings": []})
    lone.review()
    broken = Fixture(script)
    broken.row('budget = { finder_seconds = "5" }')
    broken.review()
    got = [
        (r["verdict"], r["review"].get("independence"), r["review"].get("veto") is not None,
         r["review"].get("dissent") is not None)
        for r in (blocked.latest(), lone.latest(), broken.latest())
    ]  # fmt: skip
    want = [
        ("fail", "context-only", True, True),
        ("pass", "cross-model", True, True),
        ("error", "none", True, True),
    ]
    return None if got == want else f"the verdicts carried {got}, not {want}"


# --- the role lenses -----------------------------------------------------------------------

LENS_ROWS = """
[[gate]]
id = "tester"
kind = "lens"
inputs = ["**"]
role = "test engineer"
checklist = ["Would the change's own check fail before it and pass after it?"]
question = "Would this check still pass if the change were reverted?"
evidence = "The quoted line, and the check it lacks."
stop = "Report only gaps that affect correctness or the stated requirements."

[[gate]]
id = "designer"
kind = "lens"
inputs = ["**/*.css"]
role = "product designer"
checklist = ["Does each new screen state its empty, loading and error states?"]
question = "What does the user see when this is empty, slow or broken?"
evidence = "The quoted line that adds the screen."
stop = "Report only gaps that affect correctness or the stated requirements."

[[gate]]
id = "architect"
kind = "lens"
inputs = ["**"]
classes = ["large"]
role = "software architect"
checklist = ["Can every step be undone?"]
question = "What does this choice prevent us from changing later?"
evidence = "The quoted irreversible step."
stop = "Report only gaps that affect correctness or the stated requirements."
"""


def system_prompt(call):
    argv = call["argv"]
    return argv[argv.index("--system-prompt") + 1]


def lens_assigned_by_class_and_path(script):
    f = Fixture(script)
    f.row(LENS_ROWS)
    why = expect(
        "a medium change with three lens rows", f.route(), 1,
        "review: route: lens tester: .github/gates.toml is under its inputs (class medium)",
        "review: code-review: F1 error calc.py:5:",
    )  # fmt: skip
    if why:
        return why
    out = f.route()[1]
    if "lens designer" in out or "lens architect" in out:
        return f"a lens whose path or class the change misses was assigned:\n{out}"
    finder = system_prompt(next(c for c in f.calls() if c["seat"] == "finder"))
    want = "### Lens: tester\nRole, for tone only: test engineer.\nChecklist, each answered yes or no:\n- Would the change's own check fail"
    if want not in finder or "Lens: designer" in finder or "Lens: architect" in finder:
        return f"the finder did not read the tester lens alone:\n{finder}"
    for c in f.calls():
        if c["seat"] == "router" and "tester" in c["prompt"] + system_prompt(c):
            return f"the router was shown a lens:\n{c['prompt']}"
    rec = f.latest()["review"]
    if rec["lenses"] != ["tester"] or list(rec["route"]["lenses"]) != ["tester"]:
        return f"the record does not carry the tester lens alone: {rec['lenses']}, {rec['route']['lenses']}"
    return None


def seed_open_alternative_lens_assigned_by_path(script):
    """The shipped open-alternative row, not a stand-in, rides the existing selector."""
    seed = (ROOT / "corpus/lens/gates.toml").read_text()
    row = seed[seed.index('[[gate]]\nid = "open-alternative"') :]
    f = Fixture(script)
    f.row(row)
    rc, out = f.route()
    if "lens open-alternative" in out:
        return f"a change touching no product path was assigned the lens (exit {rc}):\n{out}"
    f.change({"product/request.md": "# Request\nProduct: Openwrite\n"})
    why = expect(
        "a medium change adding a product request", f.route(), 1,
        "review: route: lens open-alternative: product/request.md is under its inputs (class medium)",
    )  # fmt: skip
    if why:
        return why
    finder = system_prompt([c for c in f.calls() if c["seat"] == "finder"][-1])
    want = "- Does the product name contain none of those marks, compared ignoring case, spacing, punctuation and diacritics?"
    return (
        None
        if want in finder
        else f"the finder did not read the lens checklist:\n{finder}"
    )


def raised_class_assigns_its_lenses(script):
    """The lenses follow the final class, which the router may raise but never lower."""
    f = Fixture(script)
    f.row(LENS_ROWS)
    f.router({"class": "large", "reviews": [{"gate": "code-review", "why": "code"}]})
    return expect(
        "a medium change the router sizes large", f.route(), 1,
        "review: route: lens tester: .github/gates.toml is under its inputs (class large)",
        "review: route: lens architect: .github/gates.toml is under its inputs (class large)",
    )  # fmt: skip


def trivial_assigns_no_lens(script):
    f = Fixture(script, "guard", "unreproduced")
    f.row(LENS_ROWS.replace('classes = ["large"]\n', ""))
    f.router(SKIP)
    rc, out = f.route(size="trivial")
    if rc != 0 or "route: lens" in out:
        return f"a trivial change was assigned a lens (exit {rc}):\n{out}"
    return None


def malformed_lens_refused(script):
    f = Fixture(script)
    f.row(
        LENS_ROWS.replace('"Can every step be undone?"', '"Every step can be undone."')
    )
    why = expect(
        "a lens whose checklist holds a statement", f.route(), 2,
        "review: error: .github/gates.toml: lens 'architect': checklist must be a"
        " non-empty list of yes or no questions",
    )  # fmt: skip
    if not why and f.calls():
        why = f"a seat sat under a malformed lens: {[c['seat'] for c in f.calls()]}"
    return why


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
    (independence_labels, "the context-only label", 'if other != "unknown" and family(model) != family(other)', 'if family(model) != family(other)'),
    (fallback_model_errors, "the served-model check", "    if served != [model]:\n", "    if False:\n"),
    (unnamed_model_errors, "a reply that names no model", "    if served != [model]:\n", "    if served and served != [model]:\n"),
    (independence_by_family, "comparing models by family", "    return next((word for word in RANK if word in low), low)\n", "    return model\n"),
    (reproduction_reads_no_home, "the user's home hidden from a reproduction", "    hidden = sorted(\n", "    hidden = [] and sorted(\n"),
    (reproduction_cannot_write_store, "a reproduction writing only its own directories", "for p in (tree, home, tmp)]", 'for p in (tree, home, tmp, "/")]'),
    (reproduction_has_no_network, "a reproduction with no network", "    network = False\n", "    network = True\n"),
    (reproduction_lists_no_sibling_temp, "the shared temporary directories hidden from a reproduction", "    shared = {os.path.realpath(p) for p in (*SHARED_ROOTS, tempfile.gettempdir())}\n", "    shared = set()\n"),
    (trivial_claim_without_router_errors, "an unconfirmed trivial claim recorded as error", 'verdict = "error" if entry.get("unconfirmed") else "pass"', 'verdict = "pass"'),
    (unreadable_finding_errors, "a finding's severity is checked", 'and f.get("severity") in SEVERITIES', ""),
    (bad_row_errors, "the row's budget is checked", "and all(type(v) is int and v > 0 for v in extra.values())", ""),
    (check_row_never_reviewed, "only a review row is reviewed", ' or row["kind"] != "review"', ""),
    (change_another_row_claims, "a row reads only its own and unclaimed paths", 'if p and (gate.matches(row, p) or not any(gate.matches(r, p) for r in rows))', "if p"),
    (review_row_folds_in_shadow, "the record carries the verdict", '            "verdict": verdict,\n', '            "verdict": "pass",\n'),
    (router_skip_caught, "the rule's floor under the router", 'entry = {"sits": rule is not None or pick is not None}', 'entry = {"sits": pick is not None}'),
    (router_cannot_lower_class, "the router only raising the class", 'if CLASSES.index(size) > CLASSES.index(plan["class"]):', "if True:"),
    (router_addition_reads_and_folds, "an added row reading the whole change", "    if whole and not diff.strip():\n", "    if False:\n"),
    (router_unavailable_falls_back, "the fallback when the router is unavailable", "except (Undecided, ValueError) as e:", "except ValueError as e:"),
    (router_unknown_gate_falls_back, "the router's rows checked against the registry", 'and item.get("gate") in ids', ""),
    (sensitive_path_sizes_large, "a sensitive path sizing the change large", 'if sensitive and given != "large":', "if False:"),
    (large_change_seats_security_lens, "the security lens on a large change", 'elif size == "large" and r.get("lens") == "security":', "elif False:"),
    (trivial_runs_no_review, "no review for a trivial change", '    if size == "trivial":\n        return {}\n', "    if False:\n        return {}\n"),
    (held_verdict_not_covered, "a held verdict kept over a not-convened pass", "elif gid in chosen and gid not in held:", "elif gid in chosen:"),
    (escalated_veto_blocks, "the escalation of a dissent", "        if judge is None:\n", "        if True:\n"),
    (adjudicator_clears_downgrades, "the adjudicator's answer deciding", '        if answer["reproduced"]:\n            return None\n', "        if True:\n            return None\n"),
    (no_stronger_seat_no_escalation, "escalating only to a stronger seat", "above = [m for m in reviewers if rank(m) > rank(verifier)]", "above = list(reviewers)"),
    (veto_names_its_seats, "the veto recorded", '    if finding["blocks"]:\n', "    if False:\n"),
    (downgrade_records_finder_dissent, "the dissent recorded", 'if v["vote"] != won:', "if False:"),
    (every_verdict_carries_the_fields, "the weakest label deciding independence", 'return "context-only" if "context-only" in labels else "cross-model"', 'return "cross-model"'),
    (every_verdict_carries_the_fields, "dissent on a verdict no seat reached", 'state.setdefault("dissent", [])', "pass"),
    (lens_assigned_by_class_and_path, "a lens's classes", 'if hit and size in lens["classes"]:', "if hit:"),
    (lens_assigned_by_class_and_path, "a lens's inputs", 'if hit and size in lens["classes"]:', 'if size in lens["classes"]:'),
    (lens_assigned_by_class_and_path, "only the assigned lenses reaching the finder", 'assigned = [lr for lr in lenses if lr["id"] in plan["lenses"]]', "assigned = lenses"),
    (lens_assigned_by_class_and_path, "the lens block in the finder's prompt", 'finder_system(lens, [lens_text(lr, "checklist") for lr in lenses])', "finder_system(lens)"),
    (seed_open_alternative_lens_assigned_by_path, "a lens's inputs", 'if hit and size in lens["classes"]:', 'if size in lens["classes"]:'),
    (seed_open_alternative_lens_assigned_by_path, "the lens block in the finder's prompt", 'finder_system(lens, [lens_text(lr, "checklist") for lr in lenses])', "finder_system(lens)"),
    (raised_class_assigns_its_lenses, "the final class assigning lenses", 'plan["lenses"] = assign(lenses, paths, plan["class"])', 'plan["lenses"] = assign(lenses, paths, given)'),
    (trivial_assigns_no_lens, "a trivial change getting no lens", 'LENS_CLASSES = ("medium", "large")', "LENS_CLASSES = CLASSES"),
    (malformed_lens_refused, "a lens's checklist of yes or no questions", 'and all(isinstance(q, str) and q.strip().endswith("?") for q in items)', "and all(isinstance(q, str) for q in items)"),
]  # fmt: skip
# A filesystem socket is refused by the network rule on macOS and by hiding the
# shared directories under bwrap; launchd and Mach services exist only on macOS.
if sys.platform == "darwin":
    CASES += [
        (reproduction_connects_to_no_socket, "a reproduction reaching no local socket", "    network = False\n", "    network = True\n"),
        (reproduction_reaches_no_mach_service, "a reproduction with no bootstrap port", 'return [sys.executable, "-I", "-c", NO_BOOTSTRAP, *jail]', "return jail"),
    ]  # fmt: skip
else:
    CASES += [
        (reproduction_connects_to_no_socket, "a reproduction reaching no local socket", "    shared = {os.path.realpath(p) for p in (*SHARED_ROOTS, tempfile.gettempdir())}\n", "    shared = set()\n"),
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
