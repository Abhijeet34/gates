#!/usr/bin/env python3
"""Regression tests for run/landing-watchdog.py, through its command line.

    .ci/test-landing-watchdog.py

The two answers under .ci/fixtures/landing-watchdog/ are gates' own, recorded
with `--record` on 2026-11-14: fresh.json at 09:33 UTC, 81 minutes after the
merger's last action, and stale.json after the 90 minutes had run out with the
same two pull requests ready since the day before. Each case replays one
through a `gh` on PATH that serves the recorded answers (and logs every write)
and returns why it failed, or None. Every case must pass against
run/landing-watchdog.py, and then fail against a copy with the one control
that case exists for removed, so a case that would pass without its control is
itself refused.

The red-first pair is the first two cases: stale exits 1, fresh exits 0.
"""

import copy
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCHDOG = ROOT / "run/landing-watchdog.py"
FIXTURES = ROOT / ".ci/fixtures/landing-watchdog"
REPO = "Abhijeet34/gates"
ISSUES = f"repos/{REPO}/issues?state=open&creator=github-actions%5Bbot%5D&per_page=100"

# `gh api [-X METHOD] PATH [--input -]`, served from $FIXTURE. A GET of a path
# not recorded answers as gh does: the error body on stdout, exit 1. A write is
# logged to $WRITES and fails only when $FAIL_WRITES is set.
GH = r"""#!/usr/bin/env python3
import json, os, sys

args = sys.argv[2:]
method = "GET"
if args[0] == "-X":
    method, args = args[1], args[2:]
path = args[0]
if method == "GET":
    answers = json.loads(open(os.environ["FIXTURE"]).read())["answers"]
    if path not in answers:
        print(json.dumps({"message": "Not Found", "status": "404"}))
        print("gh: Not Found (HTTP 404)", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(answers[path]))
    sys.exit(0)
data = json.loads(sys.stdin.read()) if "--input" in args else None
with open(os.environ["WRITES"], "a") as f:
    f.write(json.dumps({"method": method, "path": path, "data": data}) + "\n")
if os.environ.get("FAIL_WRITES"):
    print(json.dumps({"message": "Resource not accessible by integration", "status": "403"}))
    print("gh: Resource not accessible by integration (HTTP 403)", file=sys.stderr)
    sys.exit(1)
print(json.dumps({"number": 900} if method == "POST" and path.endswith("/issues") else {}))
"""

workdir = Path(tempfile.mkdtemp())


def answer(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def run(script, fixture, *extra, fail_writes=False):
    """(exit status, stdout + stderr, [writes])."""
    case = Path(tempfile.mkdtemp(dir=workdir))
    (case / "bin").mkdir()
    gh = case / "bin" / "gh"
    gh.write_text(GH)
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    (case / "fixture.json").write_text(json.dumps(fixture))
    env = dict(
        os.environ,
        PATH=f"{case / 'bin'}{os.pathsep}{os.environ['PATH']}",
        FIXTURE=str(case / "fixture.json"),
        WRITES=str(case / "writes.jsonl"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    env.pop("FAIL_WRITES", None)
    if fail_writes:
        env["FAIL_WRITES"] = "1"
    proc = subprocess.run(
        [sys.executable, str(script), "--repo", REPO, "--now", fixture["now"], *extra],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    log = case / "writes.jsonl"
    writes = (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )
    return proc.returncode, proc.stdout + proc.stderr, writes


def with_issues(fixture, issues):
    fixture = copy.deepcopy(fixture)
    fixture["answers"][ISSUES] = issues
    return fixture


OURS = {
    "number": 7,
    "title": "landing watchdog: old",
    "body": "<!-- landing-watchdog -->\nold",
}
FOREIGN = {"number": 8, "title": "something else", "body": "a human's issue"}


def expect(rc, out, want):
    if rc != want:
        return f"exit {rc}, want {want}:\n{out}"
    return None


def stale_exits_1(script):
    rc, out, writes = run(script, answer("stale"))
    if why := expect(rc, out, 1):
        return why
    if "STOPPED" not in out or writes:
        return f"want STOPPED and no write without --act, got {writes}:\n{out}"
    return None


def fresh_exits_0(script):
    rc, out, writes = run(script, answer("fresh"))
    return expect(rc, out, 0) or (f"wrote without --act: {writes}" if writes else None)


def stale_act_opens_one_assigned_issue(script):
    rc, out, writes = run(script, with_issues(answer("stale"), [FOREIGN]), "--act")
    if why := expect(rc, out, 1):
        return why
    posts = [w for w in writes if w["method"] == "POST"]
    if (
        len(writes) != 1
        or len(posts) != 1
        or posts[0]["path"] != f"repos/{REPO}/issues"
    ):
        return f"want one POST to repos/{REPO}/issues, got {writes}"
    data = posts[0]["data"]
    if data.get("assignees") != ["Abhijeet34"]:
        return f"issue not assigned to Abhijeet34: {data}"
    if (
        "no landing for 1." not in data["title"]
        or "2 pull requests wait" not in data["title"]
    ):
        return f"title does not say how long and how many: {data['title']!r}"
    if "<!-- landing-watchdog -->" not in data["body"] or "#114" not in data["body"]:
        return f"body lacks the marker or the waiting pull requests: {data['body']!r}"
    return None


def stale_act_keeps_one_issue(script):
    rc, out, writes = run(script, with_issues(answer("stale"), [OURS]), "--act")
    if why := expect(rc, out, 1):
        return why
    if [(w["method"], w["path"]) for w in writes] != [
        ("PATCH", f"repos/{REPO}/issues/7")
    ]:
        return f"want only a PATCH of the open issue #7, got {writes}"
    if "state" in writes[0]["data"]:
        return f"the open issue was closed while landing is stopped: {writes[0]}"
    return None


def fresh_act_closes_the_issue(script):
    rc, out, writes = run(
        script, with_issues(answer("fresh"), [OURS, FOREIGN]), "--act"
    )
    if why := expect(rc, out, 0):
        return why
    got = [(w["method"], w["path"]) for w in writes]
    want = [
        ("POST", f"repos/{REPO}/issues/7/comments"),
        ("PATCH", f"repos/{REPO}/issues/7"),
    ]
    if got != want or writes[1]["data"].get("state") != "closed":
        return f"want #7 commented on and closed and #8 untouched, got {writes}"
    return None


def behind_is_not_waiting(script):
    fixture = answer("stale")
    for body in fixture["answers"].values():
        if isinstance(body, dict) and body.get("mergeable_state") == "clean":
            body["mergeable_state"] = "behind"
    rc, out, _ = run(script, fixture)
    return expect(rc, out, 0)


def failed_check_is_not_waiting(script):
    fixture = answer("stale")
    for path, body in fixture["answers"].items():
        if path.endswith("/check-runs?per_page=100"):
            for check in body["check_runs"]:
                check["conclusion"] = "failure"
    rc, out, _ = run(script, fixture)
    return expect(rc, out, 0)


def a_failed_write_is_undecided(script):
    rc, out, _ = run(
        script, with_issues(answer("stale"), []), "--act", fail_writes=True
    )
    if why := expect(rc, out, 2):
        return why
    return None if "could not decide" in out else f"no refusal named:\n{out}"


def a_wrong_shaped_answer_is_undecided(script):
    fixture = answer("stale")
    fixture["answers"][f"repos/{REPO}/events?per_page=100"] = {
        "message": "an object, not the list"
    }
    rc, out, _ = run(script, fixture)
    if why := expect(rc, out, 2):
        return why
    return None if "could not decide" in out else f"no refusal named:\n{out}"


# (case, the control it proves, the source text that control is, its removal)
CASES = [
    (
        stale_exits_1,
        "the stopped exit status",
        "return 1 if stopped else 0",
        "return 0",
    ),
    (
        fresh_exits_0,
        "the merger-silence condition",
        "silent = last is None or now - last >= window",
        "silent = True",
    ),
    (
        stale_act_opens_one_assigned_issue,
        "the assignee",
        '"assignees": [assignee]',
        '"assignees": []',
    ),
    (
        stale_act_keeps_one_issue,
        "reusing the open issue",
        "        if mine:\n",
        "        if False:\n",
    ),
    (
        fresh_act_closes_the_issue,
        "closing on resume",
        "    else:\n        duplicates = mine\n",
        "    else:\n        duplicates = []\n",
    ),
    (
        behind_is_not_waiting,
        "the ready states",
        'READY_STATES = ("clean", "has_hooks")',
        'READY_STATES = ("clean", "has_hooks", "behind")',
    ),
    (
        failed_check_is_not_waiting,
        "the check conclusions",
        'run.get("conclusion") not in CHECK_OK',
        "False",
    ),
    (
        a_failed_write_is_undecided,
        "a gh failure refusing",
        "if proc.returncode != 0:",
        "if False:",
    ),
    (
        a_wrong_shaped_answer_is_undecided,
        "the answer-shape check",
        "    if not cond:\n",
        "    if False:\n",
    ),
]


def main():
    failed = 0
    source = WATCHDOG.read_text()
    for case, control, old, new in CASES:
        why = case(WATCHDOG)
        if why:
            failed = 1
            print(f"FAIL {case.__name__}: {why}")
            continue
        print(f"ok   {case.__name__}")
        if source.count(old) != 1:
            failed = 1
            print(
                f"FAIL without {control}: the mutation matches {source.count(old)} places in run/landing-watchdog.py, not 1"
            )
            continue
        mutant = Path(tempfile.mkdtemp(dir=workdir)) / "landing-watchdog.py"
        mutant.write_text(source.replace(old, new))
        why = case(mutant)
        if why:
            quoted = " | ".join(why.splitlines())
            print(f"ok   without {control}, {case.__name__} goes red: {quoted[:300]}")
        else:
            failed = 1
            print(
                f"FAIL without {control}, {case.__name__} still passes, so the case does not prove it"
            )
    print("\nlanding watchdog tests " + ("FAILED" if failed else "passed"))
    return failed


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
