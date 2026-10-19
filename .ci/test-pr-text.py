#!/usr/bin/env python3
"""Regression tests for run/pr-text.py, and its row in shadow under run/gate.py.

    .ci/test-pr-text.py

Each case feeds the script a pull request's title and body as JSON and requires
its exit status and, for a refusal, a line of its output. The near misses matter
as much as the hits: a rule that fires on its own examples and on a log line from
a CI runner would cost more false alarms than it prevents. Values that name a
person's account are assembled here, so this file holds none.
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/pr-text.py"
RUNNER = ROOT / "run/gate.py"
ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@invalid",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@invalid",
    "PYTHONDONTWRITEBYTECODE": "1",
}
workdir = tempfile.mkdtemp()
DASH = "—"
HOME = "/" + "Users" + "/" + "jane"
# A count of findings is refused in tracked text by the exposure gate, so it is assembled here.
COUNT = str(3)
# The CI account'"'"'s home, assembled: the exposure gate refuses the running account's own home in tracked text.
RUNNER_HOME = "/" + "home" + "/" + "runner"
LINUX_HOME = "/" + "home" + "/" + "jane"


def run(*args, cwd, env=ENV, stdin=""):
    proc = subprocess.run(
        args, cwd=cwd, env=env, input=stdin, capture_output=True, text=True
    )
    return proc.returncode, proc.stdout + proc.stderr


def pr(title="Add a thing", body="Fixes the thing.\n"):
    return json.dumps({"title": title, "body": body})


# (name, stdin, want exit, a line the output must carry, or a rule it must not name)
CASES = [
    ("a plain title and body pass", pr(), 0, "pr-text: title and body are clean"),
    ("a null body passes", json.dumps({"title": "Add a thing", "body": None}), 0, None),
    (
        "an em dash in the title",
        pr(title=f"Add a thing {DASH} quickly"),
        1,
        "title:1: em-dash",
    ),
    (
        "an em dash on body line 3",
        pr(body=f"One.\n\nTwo {DASH} three.\n"),
        1,
        "body:3: em-dash",
    ),
    (
        "a spaced hyphen is not an em dash",
        pr(body="one - two, and 2026-10-19.\n"),
        0,
        None,
    ),
    (
        "a slop phrase",
        pr(body="This lets us delve into the cache.\n"),
        1,
        'body:1: slop-phrase: "delve"',
    ),
    (
        "a curly-apostrophe slop phrase",
        pr(body="It’s worth noting that it works.\n"),
        1,
        "slop-phrase",
    ),
    (
        "a seamless slop phrase in the title",
        pr(title="A seamless bump"),
        1,
        "title:1: slop-phrase",
    ),
    (
        "a hollow closer",
        pr(body="Fixed. At the end of the day, it works.\n"),
        1,
        "slop-phrase",
    ),
    (
        "leverage is soft and left to review",
        pr(body="We leverage the cache and keep it robust.\n"),
        0,
        None,
    ),
    (
        "a substring of a slop word",
        pr(body="The seam less joint, and a delver-free path.\n"),
        0,
        None,
    ),
    (
        "an agent footer",
        pr(
            body="Done.\n\nGenerated with [Claude Code](https://claude.com/claude-code)\n"
        ),
        1,
        "body:3: attribution",
    ),
    (
        "an agent co-author line",
        pr(body="Done.\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"),
        1,
        "attribution",
    ),
    (
        "a human co-author line",
        pr(body="Done.\n\nCo-authored-by: Jane Doe <jane@example.com>\n"),
        0,
        None,
    ),
    (
        "a footer naming a build tool",
        pr(body="Generated with make from the schema.\n"),
        0,
        None,
    ),
    (
        "a macOS home path",
        pr(body=f"Ran it from {HOME}/work/x.\n"),
        1,
        f'home-path: "{HOME}"',
    ),
    (
        "a Linux home path in a fence",
        pr(body=f"```\ncd {LINUX_HOME}/src\n```\n"),
        1,
        "body:2: home-path",
    ),
    ("a CI runner path", pr(body=f"at {RUNNER_HOME}/work/gates/gates\n"), 0, None),
    (
        "a placeholder path",
        pr(body="Replace /Users/<name>/ and /home/user/ with yours.\n"),
        0,
        None,
    ),
    (
        "a path that only ends in users",
        pr(body="See docs/Users/guide and ~/Users/x.\n"),
        0,
        None,
    ),
    (
        "a count of security alerts",
        pr(body=f"Closes {COUNT} Dependabot alerts.\n"),
        1,
        "security-marker",
    ),
    (
        "a count of vulnerabilities",
        pr(body=f"This fixes {COUNT} vulnerabilities.\n"),
        1,
        "security-marker",
    ),
    (
        "a threshold is not a count",
        pr(body="Fail past 5 open alerts of any kind.\n"),
        0,
        None,
    ),
    (
        "an alert by number",
        pr(body="Resolves code scanning alert #14.\n"),
        1,
        "security-marker",
    ),
    (
        "a GHSA id",
        pr(body="Bumps lodash for GHSA-35jh-r3h4-6jhm.\n"),
        1,
        "security-marker",
    ),
    ("a CVE id", pr(title="Patch CVE-2026-12345"), 1, "title:1: security-marker"),
    (
        "the word CVE alone",
        pr(body="Explains what a CVE is and how ids are issued.\n"),
        0,
        None,
    ),
    ("input that is not JSON", "not json", 2, "not a pull request's title and body"),
    (
        "input with no title",
        json.dumps({"body": "x"}),
        2,
        "not a pull request's title and body",
    ),
    (
        "a title that is not a string",
        json.dumps({"title": 5, "body": "x"}),
        2,
        "title and body must be strings",
    ),
    ("input over the size cap", pr(body="x" * 1_000_001), 2, "is over 1000000 bytes"),
]


def expect(name, stdin, want, finding):
    rc, out = run(sys.executable, str(GATE), cwd=workdir, stdin=stdin)
    if rc != want:
        return f"{name}: exit {rc}, wanted {want}:\n{out}"
    if finding and finding not in out:
        return f"{name}: output does not carry {finding!r}:\n{out}"
    return None


def both_rules_named():
    rc, out = run(
        sys.executable,
        str(GATE),
        cwd=workdir,
        stdin=pr(body=f"Ran {HOME}/x {DASH} {COUNT} Dependabot alerts.\n"),
    )
    missing = [r for r in ("em-dash", "home-path", "security-marker") if r not in out]
    return (
        f"one line with three problems did not name {missing}:\n{out}"
        if missing
        else None
    )


ROW = """
[[gate]]
id = "pr-text"
inputs = ["**"]
tools = {{ pull-request = 'gh pr view "$PR_NUMBER" --json title,body' }}
run = 'gh pr view "$PR_NUMBER" --json title,body | python3 "$GATES_DIR/run/pr-text.py"'
shadow_since = {since}
"""


def under_the_runner():
    """The text is not in the tree, so the probe must re-key a verdict when only the text changes."""
    root = Path(tempfile.mkdtemp(dir=workdir))
    repo, bin_dir, store = root / "repo", root / "bin", root / "store"
    for d in (repo / ".github", bin_dir):
        d.mkdir(parents=True)
    text = root / "text.json"
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\ncat {text}\n")
    gh.chmod(0o755)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    (repo / ".github/gates.toml").write_text(ROW.format(since=today))
    (repo / "a.txt").write_text("one\n")
    env = {
        **ENV,
        "GATES_DIR": str(ROOT),
        "PR_NUMBER": "7",
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }
    run("git", "init", "-q", "--template=", "-b", "main", cwd=repo)
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-qm", "base", cwd=repo)
    run("git", "tag", "base", cwd=repo)
    (repo / "a.txt").write_text("two\n")
    run("git", "commit", "-qam", "change", cwd=repo)
    args = ("--base", "base", "--store", str(store))

    def verdicts():
        path = store / "records.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    text.write_text(pr())
    run(sys.executable, str(RUNNER), "run", *args, cwd=repo, env=env)
    text.write_text(pr(title=f"Add a thing {DASH} quickly"))
    run_rc, _ = run(sys.executable, str(RUNNER), "run", *args, cwd=repo, env=env)
    fold_rc, out = run(sys.executable, str(RUNNER), "fold", *args, cwd=repo, env=env)
    got = [(r["verdict"], r["reused_from"]) for r in verdicts()]
    if got != [("pass", None), ("fail", None)]:
        return f"after the text changed on the same tree the records were {got}, wanted a pass then a fresh fail"
    if (
        run_rc != 0
        or fold_rc != 0
        or "shadow pr-text: fail: exit 1 (in shadow since" not in out
    ):
        return (
            f"a red text row in its shadow window: run {run_rc}, fold {fold_rc}:\n{out}"
        )
    return None


def main():
    failures = [expect(*case) for case in CASES]
    failures += [both_rules_named(), under_the_runner()]
    failures = [f for f in failures if f]
    total = len(CASES) + 2
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    print(f"test-pr-text: {total - len(failures)} of {total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
