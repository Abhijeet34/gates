#!/usr/bin/env python3
"""Page the maintainer when landing stops: green pull requests wait and the merger is silent.

Usage: landing-watchdog.py --repo OWNER/NAME [--repo ...] [--minutes 90]
           [--merger hw-milan[bot]] [--now ISO8601]
           [--act --issue-repo OWNER/NAME --assignee LOGIN]
       landing-watchdog.py --repo OWNER/NAME --record FILE

It reads only what GitHub knows, through `gh api` and the REST API alone, and
needs no secret: a run's own token reads the repository it runs in.

Landing has STOPPED when both hold, across the watched repositories:

  waiting  an open, non-draft pull request is ready to land and has been for
           over --minutes. Ready: `mergeable_state` is `clean` or `has_hooks`
           (GitHub's word for no conflict and every required check and review
           satisfied), and every check run on the head completed as success,
           neutral or skipped and every commit status is success. Ready since:
           the latest of the pull request's creation, its last check or status
           and its last approving review.
  silent   the merger App has neither merged nor updated anything in the last
           --minutes: no issue event (merged, closed, head_ref_force_pushed,
           head_ref_deleted, labeled, ...) and no repository event (a push,
           a pull request or review action) whose actor is the merger.

A `behind` pull request is not waiting: it cannot land without an update, and
a stopped merger stops landing too, so the ready ones stay `clean`.

With --act it then keeps one issue in --issue-repo, found by the marker below
among the open issues the run's own identity (--issue-author) opened: opened,
assigned to --assignee, while landing has stopped ("no landing for N h, M pull
requests wait"), its title kept current while it stays stopped, and closed
with a comment once it is not. Without --act it writes nothing.

--record FILE saves the answers it read, cut to the fields it uses, with the
time; FILE replays through any `gh` that serves them (.ci/test-landing-watchdog.py).

Exit status: 0 landing is not stopped, 1 it has stopped, 2 could not decide
(gh failed, an answer was not what the API returns, or a bad argument). With
--act, a failed issue write is 2 as well.
"""

import argparse
import datetime
import json
import math
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

MARKER = "<!-- landing-watchdog -->"
READY_STATES = ("clean", "has_hooks")
CHECK_OK = ("success", "neutral", "skipped")
GH_TIMEOUT = 120
PAGE = 100


class Undecided(Exception):
    """The watchdog could not establish an answer; never read as landing."""


def stamp(text):
    try:
        t = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else None


def zulu(t):
    return t.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def bot(login):
    """GraphQL and some events drop the `[bot]` suffix REST keeps; compare without it."""
    login = (login or "").lower()
    return login[: -len("[bot]")] if login.endswith("[bot]") else login


# Each answer is cut to the fields the watchdog reads, so a recording holds no
# pull request text. The cut is idempotent, so a replay cuts a recording again.
def keep(obj, fields):
    return {k: obj.get(k) for k in fields if k in obj}


def cut(path, body):
    base = path.split("?", 1)[0]
    if base.endswith("/pulls") and isinstance(body, list):
        return [
            {
                **keep(p, ("number", "draft", "created_at")),
                "head": keep(p.get("head") or {}, ("sha",)),
            }
            for p in body
        ]
    if "/pulls/" in base and base.endswith("/reviews") and isinstance(body, list):
        return [
            {
                **keep(r, ("state", "submitted_at")),
                "user": keep(r.get("user") or {}, ("login",)),
            }
            for r in body
        ]
    if "/pulls/" in base and isinstance(body, dict):
        return keep(body, ("number", "draft", "mergeable_state", "created_at"))
    if base.endswith("/check-runs") and isinstance(body, dict):
        return {
            "total_count": body.get("total_count"),
            "check_runs": [
                keep(c, ("name", "status", "conclusion", "completed_at"))
                for c in body.get("check_runs") or []
            ],
        }
    if base.endswith("/status") and isinstance(body, dict):
        return {
            "state": body.get("state"),
            "total_count": body.get("total_count"),
            "statuses": [
                keep(s, ("context", "state", "updated_at"))
                for s in body.get("statuses") or []
            ],
        }
    if base.endswith("/issues/events") and isinstance(body, list):
        return [
            {
                **keep(e, ("event", "created_at")),
                "actor": keep(e.get("actor") or {}, ("login",)),
            }
            for e in body
        ]
    if base.endswith("/events") and isinstance(body, list):
        return [
            {
                **keep(e, ("type", "created_at")),
                "actor": keep(e.get("actor") or {}, ("login",)),
            }
            for e in body
        ]
    return body


class GitHub:
    def __init__(self, record=None):
        self.record = record
        self.answers = {}

    def call(self, args, path, data=None):
        cmd = ["gh", "api", *args, path]
        if data is not None:
            cmd += ["--input", "-"]
        try:
            proc = subprocess.run(
                cmd,
                input=None if data is None else json.dumps(data).encode(),
                capture_output=True,
                timeout=GH_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Undecided(f"gh api {path}: {e}") from e
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace").strip()
            raise Undecided(
                f"gh api {' '.join(args + [path])}: exit {proc.returncode}: {err}"
            )
        try:
            return json.loads(proc.stdout.decode() or "null")
        except ValueError as e:
            raise Undecided(f"gh api {path}: not JSON: {e}") from e

    def get(self, path):
        body = cut(path, self.call([], path))
        self.answers[path] = body
        return body

    def write(self, method, path, data):
        return self.call(["-X", method], path, data)


def need(cond, what):
    if not cond:
        raise Undecided(f"unexpected answer: {what}")


def ready_since(gh, repo, pr):
    """The time a pull request became ready to land, or None when it is not ready."""
    n, sha = pr["number"], (pr.get("head") or {}).get("sha")
    need(
        isinstance(n, int) and isinstance(sha, str),
        f"repos/{repo}/pulls: a pull request without number or head sha",
    )
    if pr.get("draft"):
        return None, "draft"
    detail = gh.get(f"repos/{repo}/pulls/{n}")
    need(isinstance(detail, dict), f"repos/{repo}/pulls/{n} is not an object")
    state = detail.get("mergeable_state")
    if state not in READY_STATES:
        return None, f"mergeable_state {state}"
    times = [stamp(pr.get("created_at"))]
    runs = gh.get(f"repos/{repo}/commits/{sha}/check-runs?per_page={PAGE}")
    need(
        isinstance(runs, dict) and isinstance(runs.get("check_runs"), list),
        f"check-runs of {sha}",
    )
    for run in runs["check_runs"]:
        if run.get("status") != "completed" or run.get("conclusion") not in CHECK_OK:
            return (
                None,
                f"check {run.get('name')!r} is {run.get('status')}/{run.get('conclusion')}",
            )
        times.append(stamp(run.get("completed_at")))
    status = gh.get(f"repos/{repo}/commits/{sha}/status")
    need(
        isinstance(status, dict) and isinstance(status.get("statuses"), list),
        f"status of {sha}",
    )
    for s in status["statuses"]:
        if s.get("state") != "success":
            return None, f"status {s.get('context')!r} is {s.get('state')}"
        times.append(stamp(s.get("updated_at")))
    if not runs["check_runs"] and not status["statuses"]:
        return None, "no check has run on the head"
    reviews = gh.get(f"repos/{repo}/pulls/{n}/reviews?per_page={PAGE}")
    need(isinstance(reviews, list), f"reviews of #{n}")
    times += [
        stamp(r.get("submitted_at")) for r in reviews if r.get("state") == "APPROVED"
    ]
    need(all(times), f"#{n}: a time that is not ISO 8601 with a zone")
    return max(times), "ready"


def merger_activity(gh, repo, merger):
    """The merger's newest action in the repository, or None when none is listed."""
    seen = []
    events = gh.get(f"repos/{repo}/issues/events?per_page={PAGE}")
    need(isinstance(events, list), f"repos/{repo}/issues/events is not a list")
    seen += [e for e in events if bot((e.get("actor") or {}).get("login")) == merger]
    events = gh.get(f"repos/{repo}/events?per_page={PAGE}")
    need(isinstance(events, list), f"repos/{repo}/events is not a list")
    seen += [e for e in events if bot((e.get("actor") or {}).get("login")) == merger]
    times = [stamp(e.get("created_at")) for e in seen]
    need(all(times), f"repos/{repo}: an event time that is not ISO 8601 with a zone")
    return max(times, default=None)


def watch(gh, repos, merger, now, window):
    """(stopped, waiting [(repo, number, since)], last merger action, lines)."""
    waiting, lines, last = [], [], None
    for repo in repos:
        prs = gh.get(f"repos/{repo}/pulls?state=open&per_page={PAGE}")
        need(isinstance(prs, list), f"repos/{repo}/pulls is not a list")
        if len(prs) >= PAGE:
            lines.append(
                f"note: {repo} has {PAGE} or more open pull requests; only the first {PAGE} were read"
            )
        for pr in prs:
            since, why = ready_since(gh, repo, pr)
            if since is None:
                lines.append(f"{repo}#{pr['number']}: not ready ({why})")
                continue
            age = now - since
            lines.append(
                f"{repo}#{pr['number']}: ready since {zulu(since)}, {hours(age)} h"
            )
            if age > window:
                waiting.append((repo, pr["number"], since))
        acted = merger_activity(gh, repo, merger)
        lines.append(
            f"{repo}: merger's last action "
            + (zulu(acted) if acted else "not in the newest events")
        )
        if acted and (last is None or acted > last):
            last = acted
    silent = last is None or now - last >= window
    return bool(waiting) and silent, waiting, last, lines


def hours(delta):
    return f"{math.floor(delta.total_seconds() / 360) / 10:.1f}"


def issue_text(waiting, last, now, merger, minutes):
    gap = (
        f"no landing for {hours(now - last)} h"
        if last
        else "no landing in the newest events"
    )
    title = f"landing watchdog: {gap}, {len(waiting)} pull request{'s' if len(waiting) != 1 else ''} wait"
    body = [
        MARKER,
        f"As of {zulu(now)} the merger App `{merger}` has neither merged nor updated anything for over {minutes} minutes"
        + (f" (its last action: {zulu(last)})" if last else "")
        + f", while these pull requests have been ready to land for over {minutes} minutes:",
        "",
        *[
            f"- {repo}#{n}, ready since {zulu(since)}"
            for repo, n, since in sorted(waiting)
        ],
        "",
        "This issue closes itself on the first hourly run that finds landing resumed.",
    ]
    return title, "\n".join(body)


def act(gh, issue_repo, author, assignee, stopped, waiting, last, now, merger, minutes):
    found = gh.get(
        f"repos/{issue_repo}/issues?state=open&creator={quote(author)}&per_page={PAGE}"
    )
    need(isinstance(found, list), f"repos/{issue_repo}/issues is not a list")
    mine = sorted(
        (
            i
            for i in found
            if "pull_request" not in i and MARKER in (i.get("body") or "")
        ),
        key=lambda i: i["number"],
    )
    if stopped:
        title, body = issue_text(waiting, last, now, merger, minutes)
        if mine:
            gh.write(
                "PATCH",
                f"repos/{issue_repo}/issues/{mine[0]['number']}",
                {"title": title, "body": body},
            )
            print(f"updated {issue_repo}#{mine[0]['number']}: {title}")
        else:
            made = gh.write(
                "POST",
                f"repos/{issue_repo}/issues",
                {"title": title, "body": body, "assignees": [assignee]},
            )
            print(f"opened {issue_repo}#{(made or {}).get('number')}: {title}")
        duplicates = mine[1:]
    else:
        duplicates = mine
    for issue in duplicates:
        n = issue["number"]
        why = (
            f"Landing resumed: as of {zulu(now)} no pull request has waited over {minutes} minutes with `{merger}` silent."
            if not stopped
            else f"A duplicate of #{mine[0]['number']}."
        )
        gh.write("POST", f"repos/{issue_repo}/issues/{n}/comments", {"body": why})
        gh.write(
            "PATCH",
            f"repos/{issue_repo}/issues/{n}",
            {"state": "closed", "state_reason": "completed"},
        )
        print(f"closed {issue_repo}#{n}")


def main():
    ap = argparse.ArgumentParser(description="Page when landing stops.")
    ap.add_argument("--repo", action="append", required=True)
    ap.add_argument("--minutes", type=int, default=90)
    ap.add_argument("--merger", default="hw-milan[bot]")
    ap.add_argument("--now", default=None)
    ap.add_argument("--act", action="store_true")
    ap.add_argument("--issue-repo", default=None)
    ap.add_argument("--issue-author", default="github-actions[bot]")
    ap.add_argument("--assignee", default="Abhijeet34")
    ap.add_argument("--record", type=Path, default=None)
    args = ap.parse_args()
    gh = GitHub()
    try:
        now = stamp(args.now) if args.now else datetime.datetime.now(datetime.UTC)
        if now is None:
            raise Undecided(
                "--now must be a timezone-aware time such as 2026-11-14T09:00:00Z"
            )
        if args.minutes < 1:
            raise Undecided("--minutes must be at least 1")
        if any(r.count("/") != 1 or not all(r.split("/")) for r in args.repo):
            raise Undecided("--repo must be OWNER/NAME")
        if args.act and args.record:
            raise Undecided("--record reads only; it does not take --act")
        window = datetime.timedelta(minutes=args.minutes)
        stopped, waiting, last, lines = watch(
            gh, args.repo, bot(args.merger), now, window
        )
        print(
            f"landing watchdog as of {zulu(now)}: window {args.minutes} min, merger {args.merger}"
        )
        print("\n".join(lines))
        verdict = "STOPPED" if stopped else "landing"
        print(
            f"{verdict}: {len(waiting)} pull request(s) ready for over {args.minutes} min; merger last acted "
            + (f"{hours(now - last)} h ago" if last else "not in the newest events")
        )
        if args.record:
            args.record.write_text(
                json.dumps(
                    {"now": zulu(now), "answers": gh.answers}, indent=1, sort_keys=True
                )
                + "\n"
            )
        if args.act:
            act(
                gh,
                args.issue_repo or args.repo[0],
                args.issue_author,
                args.assignee,
                stopped,
                waiting,
                last,
                now,
                args.merger,
                args.minutes,
            )
    except Undecided as e:
        print(f"landing-watchdog: could not decide: {e}", file=sys.stderr)
        return 2
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())
