#!/usr/bin/env python3
"""Regression tests for run/gate-shadow.sh: the runner's verdict is recorded and never gates.

    .ci/test-gate-shadow.py

Each case builds a fixture caller repository, runs the script under `bash -e`
as the workflow does, and returns why it failed, or None. The planted case is
the one that matters: a failing blocking row leaves the script's status 0 (so
the job and the caller's required check stay green) while the record and the
verdict file show the failure. Every case must also fail against a copy of the
script that propagates the runner's status, so a case that would pass without
the non-gating control is itself refused.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "run/gate-shadow.sh"
ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@users.noreply.github.com",
}
workdir = Path(tempfile.mkdtemp())


def caller(registry):
    repo = Path(tempfile.mkdtemp(dir=workdir))
    subprocess.run(["git", "init", "-q", "."], cwd=repo, check=True, env=ENV)
    if registry is not None:
        (repo / ".github").mkdir()
        (repo / ".github/gates.toml").write_text(registry)
    (repo / "README.md").write_text("fixture\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=ENV)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True, env=ENV)
    return repo


def shadow(script, repo):
    store = Path(tempfile.mkdtemp(dir=workdir))
    env = {**ENV, "GATES_DIR": str(ROOT), "SHADOW_STORE": str(store)}
    proc = subprocess.run(
        ["bash", "-e", str(script)], cwd=repo, env=env, capture_output=True, text=True
    )
    verdict = {}
    vfile = store / "shadow-verdict.txt"
    if vfile.exists():
        verdict = dict(
            ln.split("=", 1) for ln in vfile.read_text().splitlines() if "=" in ln
        )
    recs = []
    if (store / "records.jsonl").exists():
        recs = [
            json.loads(ln) for ln in (store / "records.jsonl").read_text().splitlines()
        ]
    return proc, verdict, recs


GREEN = '[[gate]]\nid = "ok"\ninputs = ["**"]\nrun = "true"\n'
PLANTED = '[[gate]]\nid = "planted"\ninputs = ["**"]\nrun = "exit 1"\n'
SHADOW_PLANTED = PLANTED + "shadow_since = 2026-10-24\n"


def case_green(script):
    proc, verdict, recs = shadow(script, caller(GREEN))
    if proc.returncode or verdict.get("verdict") != "clear":
        return f"want exit 0 and clear, got {proc.returncode} {verdict}"
    if [r["verdict"] for r in recs] != ["pass"]:
        return f"want one pass record, got {recs}"


def case_planted_blocking(script):
    proc, verdict, recs = shadow(script, caller(PLANTED))
    if proc.returncode != 0:
        return f"a failing blocking row turned the job red: exit {proc.returncode}"
    if verdict.get("verdict") != "refused" or [r["verdict"] for r in recs] != ["fail"]:
        return f"the failure was not recorded: {verdict} {recs}"


def case_planted_shadow_row(script):
    proc, verdict, recs = shadow(script, caller(SHADOW_PLANTED))
    if proc.returncode != 0:
        return f"exit {proc.returncode}"
    if verdict.get("verdict") != "shadow-red" or [r["verdict"] for r in recs] != [
        "fail"
    ]:
        return f"a failing shadow row was not recorded: {verdict} {recs}"


def case_no_registry(script):
    proc, verdict, _ = shadow(script, caller(None))
    if proc.returncode != 0 or verdict.get("verdict") != "undecided":
        return f"want exit 0 and undecided, got {proc.returncode} {verdict}"
    if "::warning" not in proc.stdout:
        return "an undecided shadow run raised no annotation"


CASES = [case_green, case_planted_blocking, case_planted_shadow_row, case_no_registry]

# The control removed: the script's last line propagates the runner's status.
propagating = workdir / "gate-shadow-propagating.sh"
text = SCRIPT.read_text()
assert text.rstrip().endswith("\nexit 0"), (
    "mutation anchor moved: the script must end with `exit 0`"
)
propagating.write_text(
    text.rstrip()[: -len("exit 0")]
    + 'if [ "$verdict" = clear ]; then exit 0; else exit 1; fi\n'
)

failed = False
for case in CASES:
    why = case(SCRIPT)
    # case_green and case_no_registry have no planted failure to propagate, so
    # only the cases that plant a red row are put to the mutated copy.
    control = (
        case(propagating)
        if case in (case_planted_blocking, case_planted_shadow_row)
        else "n/a"
    )
    if why is not None:
        print(f"FAIL {case.__name__}: {why}")
        failed = True
    elif control is None:
        print(f"FAIL {case.__name__}: passes without its control, so it proves nothing")
        failed = True
    else:
        print(
            f"ok   {case.__name__}"
            + ("" if control == "n/a" else " (fails without its control)")
        )
sys.exit(1 if failed else 0)
