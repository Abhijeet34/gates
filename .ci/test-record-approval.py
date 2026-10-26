#!/usr/bin/env python3
"""Regression tests for run/record-approval.py.

    .ci/test-record-approval.py

Each case serves a pull request, its files and its reviews through a stub `gh`
on PATH, runs the check, and requires its exit status and a line of its output.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "run/record-approval.py"
REPO = "Abhijeet34/gates"
HEAD = "b" * 40
OLD = "a" * 40
BOT = "hw-bodhi[bot]"
RECORDS = "policy/relaxations.toml"

# Serves `gh api --paginate <path>` from the file <GH_STUB>/<path with / as __>,
# printed as written, so a case can hand it two pages back to back the way
# --paginate prints them.
STUB = """#!/usr/bin/env python3
import os, sys
from pathlib import Path
page = Path(os.environ["GH_STUB"]) / sys.argv[-1].replace("/", "__")
if not page.is_file():
    sys.exit("gh: Not Found (HTTP 404)")
sys.stdout.write(page.read_text())
"""


def review(login, state, commit=HEAD):
    return {"user": {"login": login}, "state": state, "commit_id": commit}


def run(files, reviews, author=BOT, head=HEAD, changed=None, pr="7", raw=None):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pull = f"repos/{REPO}/pulls/7".replace("/", "__")
        n = len(files) if changed is None else changed
        pr_obj = {"user": {"login": author}, "head": {"sha": head}, "changed_files": n}
        (tmp / pull).write_text(json.dumps(pr_obj))
        (tmp / f"{pull}__files").write_text(json.dumps(files))
        (tmp / f"{pull}__reviews").write_text(raw or json.dumps(reviews))
        (tmp / "bin").mkdir()
        gh = tmp / "bin" / "gh"
        gh.write_text(STUB)
        gh.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
            "GH_STUB": str(tmp),
        }
        proc = subprocess.run(
            [sys.executable, str(CHECK), "--repo", REPO, "--pr", pr, "--head", HEAD],
            capture_output=True,
            text=True,
            env=env,
        )
        return proc.returncode, proc.stdout + proc.stderr


TOUCHED = [{"filename": RECORDS}, {"filename": "README.md"}]
UNTOUCHED = [{"filename": "README.md"}]

CASES = [
    (
        "approved at the head passes",
        dict(files=TOUCHED, reviews=[review("Abhijeet34", "APPROVED")]),
        0,
        f"record-approval: Abhijeet34 approved this change to {RECORDS} at the head bbbbbbbbbbbb",
    ),
    (
        "approved at an older commit refuses",
        dict(files=TOUCHED, reviews=[review("Abhijeet34", "APPROVED", OLD)]),
        1,
        "record-approval: refused: Abhijeet34 approved aaaaaaaaaaaa, not the head bbbbbbbbbbbb",
    ),
    (
        "changes requested refuses",
        dict(files=TOUCHED, reviews=[review("Abhijeet34", "CHANGES_REQUESTED")]),
        1,
        f"record-approval: refused: Abhijeet34's latest review of this change to {RECORDS} is CHANGES_REQUESTED",
    ),
    (
        "no review refuses",
        dict(files=TOUCHED, reviews=[]),
        1,
        "needs Abhijeet34's approving review at the head, and there is none",
    ),
    (
        "a pull request opened by the approver refuses",
        dict(
            files=TOUCHED,
            reviews=[review("Abhijeet34", "APPROVED")],
            author="Abhijeet34",
        ),
        1,
        f"record-approval: refused: a change to {RECORDS} must be opened by a bot identity, not by Abhijeet34",
    ),
    (
        "a pull request that leaves the file alone passes",
        dict(files=UNTOUCHED, reviews=[], author="Abhijeet34"),
        0,
        f"record-approval: {RECORDS} is not changed by this pull request",
    ),
    (
        "an approval by anyone else does not count",
        dict(files=TOUCHED, reviews=[review("someone", "APPROVED")]),
        1,
        "and there is none",
    ),
    (
        "changes requested after the approval refuses",
        dict(
            files=TOUCHED,
            reviews=[
                review("Abhijeet34", "APPROVED"),
                review("Abhijeet34", "CHANGES_REQUESTED"),
            ],
        ),
        1,
        "latest review of this change to policy/relaxations.toml is CHANGES_REQUESTED",
    ),
    (
        "a dismissed approval refuses",
        dict(files=TOUCHED, reviews=[review("Abhijeet34", "DISMISSED")]),
        1,
        "latest review of this change to policy/relaxations.toml is DISMISSED",
    ),
    (
        "an approval after changes requested passes",
        dict(
            files=TOUCHED,
            reviews=[
                review("Abhijeet34", "CHANGES_REQUESTED", OLD),
                review("abhijeet34", "APPROVED"),
            ],
        ),
        0,
        "approved this change",
    ),
    (
        "a comment after the approval leaves it standing",
        dict(
            files=TOUCHED,
            reviews=[
                review("Abhijeet34", "APPROVED"),
                review("Abhijeet34", "COMMENTED"),
            ],
        ),
        0,
        "approved this change",
    ),
    (
        "the approval on a second page of reviews is read",
        dict(
            files=TOUCHED,
            reviews=None,
            raw=json.dumps([review("Abhijeet34", "CHANGES_REQUESTED", OLD)])
            + json.dumps([review("Abhijeet34", "APPROVED")]),
        ),
        0,
        "approved this change",
    ),
    (
        "renaming the file away counts as changing it",
        dict(
            files=[{"filename": "policy/old.toml", "previous_filename": RECORDS}],
            reviews=[],
        ),
        1,
        "and there is none",
    ),
    (
        "a files list shorter than the change cannot decide",
        dict(files=UNTOUCHED, reviews=[], changed=3001),
        2,
        "record-approval: could not decide: listed 1 of 3001 changed files",
    ),
    (
        "a run for a commit that is no longer the head refuses",
        dict(files=UNTOUCHED, reviews=[], head=OLD),
        1,
        "the run for the head decides",
    ),
    (
        "a push to main has no pull request to hold",
        dict(files=TOUCHED, reviews=[], pr=""),
        0,
        "record-approval: not a pull request, nothing to hold",
    ),
    (
        "a reviews list the API cannot serve cannot decide",
        dict(files=TOUCHED, reviews=None, raw="not json"),
        2,
        "record-approval: could not decide: gh api repos/Abhijeet34/gates/pulls/7/reviews: not JSON",
    ),
]


def main():
    failed = 0
    for name, kwargs, want_code, want_line in CASES:
        code, out = run(**kwargs)
        if code == want_code and want_line in out:
            print(f"ok   {name}")
        else:
            failed += 1
            print(
                f"FAIL {name}: exit {code} (want {want_code}), want {want_line!r} in:"
            )
            print("     " + out.replace("\n", "\n     "))
    if failed:
        print(
            f"record-approval: {failed} of {len(CASES)} case(s) failed", file=sys.stderr
        )
        return 1
    print(f"record-approval: all {len(CASES)} cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
