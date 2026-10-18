#!/usr/bin/env python3
"""Regression tests for run/budget-monitor.py, through its command line.

    .ci/test-budget-monitor.py

Each case builds Actions API response bodies in a tempdir, puts a `gh` on PATH
that serves them (and runs the monitor's own --jq expression through the real
`jq`), runs the monitor and returns why it failed, or None. Every case must pass
against run/budget-monitor.py, and then fail against a copy with the one
control that case exists for removed, so a case that would pass without its
control is itself refused.

The replay is pointback's `check` step as report 2.5 tabulated it: the daily
p50 and max below are that table (25 s on 2026-09-04, 332 s on 2026-10-15),
with the days between interpolated and six runs a day spread from 80% of the
p50 up to the max. The raw runs were scratch and are gone; this is the shape of
the series, not the series.
"""

import datetime
import itertools
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MONITOR = ROOT / "run/budget-monitor.py"

BUDGETS = """[[budget]]
id = "pointback-check"
repo = "Abhijeet34/pointback"
workflow = "ci.yml"
job = "check"
step = "check"
budget_s = 360
"""
NOW = datetime.datetime(2026, 10, 19, 12, tzinfo=datetime.UTC)
DAY = datetime.timedelta(days=1)

# gh api: the monitor's calls are `gh api PATH --paginate --jq EXPR`.
GH = """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if os.environ.get("GH_FAIL"):
    print("gh: HTTP 401: Bad credentials", file=sys.stderr)
    sys.exit(1)
fx = Path(os.environ["FIXTURE"])
url, jq = urlparse(sys.argv[2]), sys.argv[sys.argv.index("--jq") + 1]
if url.path.endswith("/runs"):
    lo, hi = parse_qs(url.query)["created"][0].split("..")
    runs = [r for r in json.loads((fx / "runs.json").read_text()) if lo <= r["created_at"] <= hi]
    body = {"total_count": len(runs), "workflow_runs": runs}
else:
    body = json.loads((fx / "jobs" / (url.path.split("/")[-2] + ".json")).read_text())
subprocess.run(["jq", "-c", jq], input=json.dumps(body).encode(), check=True)
"""

workdir = Path(tempfile.mkdtemp())


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def fixture(samples):
    """API bodies for (started, step seconds, step conclusion) samples, one run each."""
    fx = Path(tempfile.mkdtemp(dir=workdir))
    (fx / "jobs").mkdir()
    runs = []
    for n, (started, secs, conclusion) in enumerate(sorted(samples), 1):
        created = started - datetime.timedelta(seconds=30)
        step_end = started + datetime.timedelta(seconds=secs)
        step = {
            "name": "check",
            "conclusion": conclusion,
            "started_at": iso(started),
            "completed_at": iso(step_end),
        }
        job = {
            "name": "check",
            "conclusion": "success",
            "started_at": iso(created),
            "completed_at": iso(step_end + datetime.timedelta(seconds=10)),
            "steps": [
                {
                    "name": "Set up job",
                    "conclusion": "success",
                    "started_at": iso(created),
                    "completed_at": iso(started),
                },
                step,
            ],
        }
        other = {
            "name": "checks",
            "conclusion": "success",
            "started_at": iso(created),
            "completed_at": iso(step_end),
            "steps": [],
        }
        runs.append({"id": n, "created_at": iso(created), "status": "completed"})
        (fx / "jobs" / f"{n}.json").write_text(json.dumps({"jobs": [other, job]}))
    (fx / "runs.json").write_text(json.dumps(runs))
    return fx


def monitor(path, fx, now=NOW, budgets=BUDGETS, *extra, fail=False):
    """(exit status, stdout, stderr)."""
    bin_dir = workdir / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(GH)
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    file = Path(tempfile.mkdtemp(dir=workdir)) / "budgets.toml"
    file.write_text(budgets)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FIXTURE": str(fx)}
    if fail:
        env["GH_FAIL"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(path),
            "--budgets",
            str(file),
            "--now",
            iso(now),
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def daily(age_to_secs, ages=None, per_day=4, conclusion="success"):
    """Samples `age` days before NOW, `age_to_secs(age, i)` seconds for run i of that day."""
    return [
        (
            NOW - age * DAY - datetime.timedelta(minutes=per_day - i),
            age_to_secs(age, i),
            conclusion,
        )
        for age in (range(21) if ages is None else ages)
        for i in range(per_day)
    ]


# --- the pointback replay ----------------------------------------------------

ANCHORS = [
    ("2026-09-04", 25, 25),
    ("2026-09-30", 22, 24),
    ("2026-10-02", 38, 78),
    ("2026-10-05", 150, 227),
    ("2026-10-12", 218, 267),
    ("2026-10-13", 250, 328),
    ("2026-10-15", 332, 361),
]


def pointback():
    pts = [(datetime.date.fromisoformat(d), p50, mx) for d, p50, mx in ANCHORS]
    samples = []
    for (d0, a50, amx), (d1, b50, bmx) in itertools.pairwise(pts):
        for k in range((d1 - d0).days):
            f = k / (d1 - d0).days
            p50, mx = a50 + (b50 - a50) * f, amx + (bmx - amx) * f
            day = datetime.datetime.combine(
                d0 + k * DAY, datetime.time(9), datetime.UTC
            )
            for i in range(6):
                secs = (
                    p50 * (0.8 + 0.08 * i) if i < 3 else p50 + (mx - p50) * (i - 2) / 3
                )
                samples.append(
                    (day + datetime.timedelta(minutes=i), round(secs), "success")
                )
    last = datetime.datetime(2026, 10, 15, 9, tzinfo=datetime.UTC)
    samples += [
        (last + datetime.timedelta(minutes=i), 332 + 5 * i, "success") for i in range(6)
    ]
    return fixture(samples)


def replay_alarms_by_oct_13(path):
    fx = pointback()
    at = lambda d: monitor(path, fx, datetime.datetime.fromisoformat(d))  # noqa: E731
    rc, out, err = at("2026-10-05T23:59:59+00:00")
    if rc != 0 or "ALARM" in out:
        return f"2026-10-05, 150 s p50 of 360 s, exited {rc} with an alarm:\n{out}{err}"
    rc, out, err = at("2026-10-13T23:59:59+00:00")
    if rc != 1 or "ALARM: p95" not in out or "pointback-check" not in out:
        return f"2026-10-13, two days before the near-miss, exited {rc} with no alarm:\n{out}{err}"
    return None


# --- series that stay under the alarm ----------------------------------------


def peak(secs):
    # One run in four at `secs`, the rest at 100 s, and a cancelled run each day
    # at 1,000 s: a cancel ends a step early and says nothing about the budget.
    samples = daily(lambda age, i: secs if i == 0 else 100)
    return fixture(
        samples + daily(lambda a, i: 1000, per_day=1, conclusion="cancelled")
    )


def under_70_raises_none(path):
    rc, out, err = monitor(path, peak(251))
    if rc != 0 or "ALARM" in out:
        return f"p95 251 s of 360 s (69.7%) exited {rc}:\n{out}{err}"
    return None


def at_70_alarms(path):
    rc, out, err = monitor(path, peak(252))
    if (
        rc != 1
        or "ALARM: p95 252 s is 70% of the 360 s budget (alarm at 70%)" not in out
    ):
        return f"p95 252 s of 360 s exited {rc}:\n{out}{err}"
    return None


def cancelled_steps_are_not_samples(path):
    rc, out, err = monitor(path, peak(251))
    if "n=28 p50=100 p95=251" not in out:
        return f"the cancelled 1,000 s steps entered the sample:\n{out}{err}"
    return None


def weekly_percentiles_are_nearest_rank(path):
    fx = fixture(
        [(NOW - datetime.timedelta(minutes=i + 1), i + 1, "success") for i in range(21)]
    )
    rc, out, err = monitor(path, fx)
    if "n=21 p50=11 p95=20" not in out or "no samples" not in out:
        return f"21 runs of 1..21 s should print p50=11 p95=20, then empty earlier weeks:\n{out}{err}"
    return None


# --- the fix task -------------------------------------------------------------


def fix_after_14_days_over(path):
    rc, out, err = monitor(path, fixture(daily(lambda age, i: 400)))
    want = "title: gates: pointback-check p95 over its 360 s budget for 14 days"
    if (
        rc != 1
        or "  FIX: p95 at or over budget on 14 of the last 14 days" not in out
        or want not in out
    ):
        return f"400 s steps for 21 days exited {rc} without the fix task:\n{out}{err}"
    return None


def no_fix_at_13_days(path):
    rc, out, err = monitor(
        path, fixture(daily(lambda age, i: 400 if age < 13 else 100))
    )
    if "over budget on 13 of the 14 days a fix task needs" not in out or "FIX:" in out:
        return f"13 days over filed or hid the count:\n{out}{err}"
    return None


# --- what the monitor will not call fine --------------------------------------


def gh_failure_is_undecided(path):
    rc, out, err = monitor(path, fixture(daily(lambda age, i: 100)), fail=True)
    if rc != 2 or "gh api" not in err or "Bad credentials" not in err:
        return f"a failing gh exited {rc}:\n{out}{err}"
    return None


def silence_is_undecided(path):
    old = daily(lambda age, i: 100, ages=range(9, 21))
    rc, out, err = monitor(path, fixture(old))
    if rc != 2 or "NO DATA" not in out:
        return f"a week with no sample exited {rc}:\n{out}{err}"
    return None


def unknown_key_is_refused(path):
    rc, out, err = monitor(path, fixture([]), budgets=BUDGETS + "budget_ms = 3000\n")
    if rc != 2 or "unknown key 'budget_ms'" not in err:
        return f"a misspelt key exited {rc}:\n{out}{err}"
    return None


def sampling_is_reported(path):
    rc, out, err = monitor(
        path, fixture(daily(lambda age, i: 100)), NOW, BUDGETS, "--max-runs", "5"
    )
    if "sampled 5 of 84 runs" not in out:
        return f"--max-runs 5 over 84 runs did not say it sampled:\n{out}{err}"
    return None


CASES = [
    (
        replay_alarms_by_oct_13,
        "the alarm logic",
        "if 100 * latest[3] >= ALARM_PCT * budget:",
        "if False:",
    ),
    (under_70_raises_none, "the 70% threshold", "ALARM_PCT = 70\n", "ALARM_PCT = 60\n"),
    (
        at_70_alarms,
        "the threshold counting 70% itself",
        "if 100 * latest[3] >= ALARM_PCT * budget:",
        "if 100 * latest[3] > ALARM_PCT * budget:",
    ),
    (
        cancelled_steps_are_not_samples,
        "the success/failure filter",
        'COUNTED = ("success", "failure")',
        'COUNTED = ("success", "failure", "cancelled")',
    ),
    (
        weekly_percentiles_are_nearest_rank,
        "nearest-rank percentiles",
        "math.ceil(q * len(ordered))",
        "math.floor(q * len(ordered))",
    ),
    (
        fix_after_14_days_over,
        "the 14-day fix rule",
        "if over >= FIX_DAYS:",
        "if False:",
    ),
    (
        no_fix_at_13_days,
        "the fix task waiting for 14 days",
        "if over >= FIX_DAYS:",
        "if over >= 1:",
    ),
    (
        gh_failure_is_undecided,
        "a gh failure refusing",
        "if proc.returncode != 0:",
        "if False:",
    ),
    (
        silence_is_undecided,
        "an empty week being undecided",
        "status = status or 2",
        "status = status or 0",
    ),
    (
        unknown_key_is_refused,
        "the unknown-key refusal",
        'problems = [f"unknown key {k!r}" for k in sorted(set(row) - ROW_KEYS)]',
        "problems = []",
    ),
    (
        sampling_is_reported,
        "the sampling note",
        "if len(runs) > max_runs:",
        "if False:",
    ),
]


def main():
    failed = 0
    source = MONITOR.read_text()
    for case, control, old, new in CASES:
        why = case(MONITOR)
        if why:
            failed = 1
            print(f"FAIL {case.__name__}: {why}")
            continue
        print(f"ok   {case.__name__}")
        if source.count(old) != 1:
            failed = 1
            print(
                f"FAIL without {control}: the mutation matches {source.count(old)} places in run/budget-monitor.py, not 1"
            )
            continue
        mutant = Path(tempfile.mkdtemp(dir=workdir)) / "budget-monitor.py"
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
    print("\nbudget monitor tests " + ("FAILED" if failed else "passed"))
    return failed


if __name__ == "__main__":
    try:
        if not shutil.which("jq"):
            sys.exit(
                "test-budget-monitor: needs jq to run the monitor's --jq expressions"
            )
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
