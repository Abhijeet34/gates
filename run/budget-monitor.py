#!/usr/bin/env python3
"""Print each budgeted gate's weekly p50/p95, its alarm, and the fix task to file.

Usage: budget-monitor.py --budgets FILE [--now ISO8601] [--max-runs N]

FILE is TOML, one `[[budget]]` per series:

  id       = "pointback-check"
  repo     = "Abhijeet34/pointback"
  workflow = "ci.yml"          # file name under .github/workflows
  job      = "check"
  step     = "check"           # optional: absent measures the whole job
  budget_s = 360               # the step's timeout, in seconds

Until the runner's records carry timings fleet-wide, durations come from the
Actions API through `gh api` alone: three 7-day slices of completed runs, then
the jobs of each run, keeping the series' step (or job) when it ended `success`
or `failure`. A step cut short by a cancel says nothing about the budget.
Nothing is stored and nothing is filed; the report is computed on every call.

A week is the 7 days ending at --now (default: now), and a step belongs to the
week its `started_at` falls in. Per series the report prints the last three
weeks' sample count, p50 and p95 (nearest rank) and:

  ALARM  the latest week's p95 is at or over 70% of budget_s
  OVER   it is at or over budget_s itself
  FIX    the trailing-7-day p95 was at or over budget_s on each of the last 14
         days, so the exact task text for the operator to file follows

`--max-runs` bounds the jobs calls per workflow (one per run) by keeping an
evenly spaced sample of the runs; the report says when it sampled.

Exit status: 0 every series under 70%, 1 an alarm, 2 could not decide (gh
failed, a bad file, or a series with no sample in the latest week).
"""

import argparse
import datetime
import json
import math
import subprocess
import sys
import tomllib
from pathlib import Path

ROW_KEYS = {"id", "repo", "workflow", "job", "step", "budget_s"}
ALARM_PCT = 70
WEEKS = 3
FIX_DAYS = 14
DAY = datetime.timedelta(days=1)
WEEK = 7 * DAY
# The runs API returns at most 1,000 results for one filtered query.
API_CAP = 1000
COUNTED = ("success", "failure")
GH_TIMEOUT = 300
RUNS_JQ = ".workflow_runs[]|{id,created:.created_at}"
JOBS_JQ = (
    ".jobs[]|{name,conclusion,started:.started_at,completed:.completed_at,"
    "steps:[(.steps // [])[]|{name,conclusion,started:.started_at,completed:.completed_at}]}"
)


class Undecided(Exception):
    """The monitor could not establish an answer; never read as under budget."""


def stamp(text):
    try:
        t = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else None


def load(path):
    try:
        rows = tomllib.loads(path.read_text()).get("budget", [])
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Undecided(f"{path}: {e}") from e
    if not rows or not all(isinstance(r, dict) for r in rows):
        raise Undecided(f"{path} lists no [[budget]] tables")
    seen = set()
    for row in rows:
        problems = [f"unknown key {k!r}" for k in sorted(set(row) - ROW_KEYS)]
        for k in ("id", "repo", "workflow", "job"):
            if not isinstance(row.get(k), str) or not row[k]:
                problems.append(f"no {k}")
        if not isinstance(row.get("step", ""), str) or row.get("step") == "":
            problems.append("step must be a non-empty name, or absent")
        b = row.get("budget_s")
        if isinstance(b, bool) or not isinstance(b, int | float) or b <= 0:
            problems.append("budget_s must be a positive number")
        if row.get("id") in seen:
            problems.append("duplicate id")
        if problems:
            raise Undecided(f"{path}: budget {row.get('id')!r}: {'; '.join(problems)}")
        seen.add(row["id"])
    return rows


def gh(path, jq):
    cmd = ["gh", "api", path, "--paginate", "--jq", jq]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=GH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Undecided(f"gh api {path}: {e}") from e
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()
        raise Undecided(f"gh api {path}: exit {proc.returncode}: {err}")
    try:
        return [json.loads(line) for line in proc.stdout.decode().splitlines() if line]
    except ValueError as e:
        raise Undecided(f"gh api {path}: not JSON lines: {e}") from e


def zulu(t):
    return t.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_runs(repo, workflow, now, notes):
    runs = {}
    for k in range(WEEKS):
        end = now - k * WEEK
        span = f"{zulu(end - WEEK)}..{zulu(end)}"
        found = gh(
            f"repos/{repo}/actions/workflows/{workflow}/runs"
            f"?per_page=100&status=completed&created={span}",
            RUNS_JQ,
        )
        if len(found) >= API_CAP:
            notes.append(
                f"{repo} {workflow}: {span} returned {len(found)} runs, the API's cap, so older runs in it are missing"
            )
        for run in found:
            runs[run["id"]] = run
    return sorted(runs.values(), key=lambda r: (r["created"], r["id"]))


def seconds(item):
    a, b = stamp(item.get("started")), stamp(item.get("completed"))
    if a is None or b is None or item.get("conclusion") not in COUNTED:
        return None
    return a, max(0, round((b - a).total_seconds()))


def collect(rows, now, max_runs):
    """({series id: [(started, seconds)]}, notes)."""
    samples, notes = {r["id"]: [] for r in rows}, []
    groups = {}
    for row in rows:
        groups.setdefault((row["repo"], row["workflow"]), []).append(row)
    for (repo, workflow), group in groups.items():
        runs = list_runs(repo, workflow, now, notes)
        if len(runs) > max_runs:
            # Evenly spaced, so every week keeps its share of the sample.
            kept = [runs[i * len(runs) // max_runs] for i in range(max_runs)]
            notes.append(f"{repo} {workflow}: sampled {max_runs} of {len(runs)} runs")
            runs = kept
        for run in runs:
            jobs = gh(
                f"repos/{repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100",
                JOBS_JQ,
            )
            for row in group:
                for job in (j for j in jobs if j.get("name") == row["job"]):
                    if "step" not in row:
                        got = seconds(job)
                        if got:
                            samples[row["id"]].append(got)
                        continue
                    for step in job.get("steps", []):
                        got = seconds(step) if step.get("name") == row["step"] else None
                        if got:
                            samples[row["id"]].append(got)
    return samples, notes


def percentile(values, q):
    """Nearest rank: the smallest value with at least q of the sample at or below it."""
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def window(samples, end):
    return [s for t, s in samples if end - WEEK < t <= end]


def analyse(samples, budget, now):
    weeks = []
    for k in range(WEEKS):
        end = now - k * WEEK
        w = window(samples, end)
        weeks.append(
            (end, len(w), percentile(w, 0.5), percentile(w, 0.95))
            if w
            else (end, 0, None, None)
        )
    over = 0
    for i in range(FIX_DAYS):
        w = window(samples, now - i * DAY)
        if not w or percentile(w, 0.95) < budget:
            break
        over += 1
    return weeks, over


def task_text(row, weeks, now):
    lines = [
        f"title: gates: {row['id']} p95 over its {row['budget_s']:g} s budget for {FIX_DAYS} days",
        "body:",
        f"{row['id']} ({row['repo']}, {row['workflow']}, job {row['job']!r}"
        + (f", step {row['step']!r}" if "step" in row else "")
        + f") had a trailing 7-day p95 at or over its {row['budget_s']:g} s budget on each of the {FIX_DAYS} days to {now:%Y-%m-%d}.",
        "Weekly p50 / p95 in seconds, newest first:",
        *weekly_lines(weeks),
        "Find what made it slower and fix that. Change budget_s only in the pull request that explains the growth with these numbers.",
        f"Done when this report shows the latest week's p95 under {ALARM_PCT}% of the budget.",
    ]
    return "\n".join(lines)


def weekly_lines(weeks):
    return [
        f"  week to {end:%Y-%m-%d}: "
        + (f"n={n} p50={p50:g} p95={p95:g}" if n else "no samples")
        for end, n, p50, p95 in weeks
    ]


def report(rows, samples, notes, now):
    """(exit status, lines)."""
    out = [
        f"gate budget report as of {zulu(now)}: trailing 7-day windows, nearest-rank percentiles,",
        "computed from the Actions API now and stored nowhere",
        "",
    ]
    status = 0
    for row in rows:
        budget = row["budget_s"]
        weeks, over = analyse(samples[row["id"]], budget, now)
        out.append(
            f"{row['id']}: budget {budget:g} s, alarm at {ALARM_PCT * budget / 100:g} s"
        )
        out.extend(weekly_lines(weeks))
        latest = weeks[0]
        if not latest[1]:
            out.append("  NO DATA: no sample in the latest week, so nothing is decided")
            status = status or 2
            out.append("")
            continue
        pct = f"{100 * latest[3] / budget:.0f}%"
        if latest[3] >= budget:
            out.append(
                f"  OVER: p95 {latest[3]:g} s is {pct} of the {budget:g} s budget"
            )
        if 100 * latest[3] >= ALARM_PCT * budget:
            out.append(
                f"  ALARM: p95 {latest[3]:g} s is {pct} of the {budget:g} s budget (alarm at {ALARM_PCT}%)"
            )
            status = 1
        if over >= FIX_DAYS:
            out.append(
                f"  FIX: p95 at or over budget on {over} of the last {FIX_DAYS} days. File this task:"
            )
            out.extend(
                "    " + line for line in task_text(row, weeks, now).splitlines()
            )
        elif over:
            out.append(
                f"  over budget on {over} of the {FIX_DAYS} days a fix task needs"
            )
        out.append("")
    out.extend(f"note: {n}" for n in notes)
    return status, out


def main():
    ap = argparse.ArgumentParser(description="Weekly gate budget report.")
    ap.add_argument("--budgets", required=True, type=Path)
    ap.add_argument("--now", default=None)
    ap.add_argument("--max-runs", type=int, default=300)
    args = ap.parse_args()
    try:
        now = stamp(args.now) if args.now else datetime.datetime.now(datetime.UTC)
        if now is None:
            raise Undecided(
                "--now must be a timezone-aware time such as 2026-10-19T12:00:00Z"
            )
        if args.max_runs < 1:
            raise Undecided("--max-runs must be at least 1")
        rows = load(args.budgets)
        samples, notes = collect(rows, now, args.max_runs)
    except Undecided as e:
        print(f"budget-monitor: {e}", file=sys.stderr)
        return 2
    status, lines = report(rows, samples, notes, now)
    print("\n".join(lines).rstrip())
    return status


if __name__ == "__main__":
    sys.exit(main())
