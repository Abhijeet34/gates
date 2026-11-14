#!/usr/bin/env python3
"""The verifier's judge: whether a head may merge into the default branch, by the base's tier list.

Usage: owner-verify.py --repo OWNER/NAME --pr N --head SHA --base SHA --tree DIR [--review FILE]

Reads the pull request, its files, the base's `.github/owner-verify.toml` and
`.github/CODEOWNERS`, the owner's reviews, and the head's workflow runs, their
jobs and its check runs named `checks` live through `gh api`; never a file of
the head. DIR is a git directory holding the base and head commits,
which run/weakening.py reads beside this script; nothing of the head runs.
FILE is the review leg's verdict, `{"head": SHA, "blocking": N}`.

Each changed path, by both names on a rename, takes the first list of the
tier list that covers it, in this order:

  maintainer  passes only with the owner's approval at the head
  workflow    held: its reader, canary and review are not built yet
  verified    passes on a review verdict for this head with no blocking
              finding, never for a head outside the repository
  checked     passes
  (unlisted)  passes

A weakening that run/weakening.py finds unmatched refuses the whole change.

The head's tests must be shown to have run, and no approval replaces that: the
newest run at the head with event pull_request of the workflow at the path the
base's tier list names in `ci` (default .github/workflows/ci.yml, which the base
must hold) has, in its latest attempt, a job `checks` that concluded success;
and every check run named `checks` on the head, from any app, is the check run
of a `checks` job of a pull_request or pull_request_review run of that
workflow at the head. Anyone with a write token can post a check run named
`checks`, so its name and app prove nothing.

The owner's approval at the head passes every route: it counts when the
owner's latest approving, changes-requested or dismissed review on some pull
request at that head into the default branch is APPROVED on that commit, so
the verdict depends on the commit and not on which pull request asked.
README.md, "The owner-verify judge", owns the design.

Exit status: 0 pass, 1 refused, 2 could not decide, 3 not enrolled (the base has no
tier list, which the verifier posts as neutral so the pull request that adds one can go green).
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIERS = ".github/owner-verify.toml"
OWNERS = ".github/CODEOWNERS"
ROUTES = ("maintainer", "workflow", "verified", "checked")
CI = ".github/workflows/ci.yml"
CI_JOB = "checks"
# The runs of the base's CI at a head whose `checks` job is its genuine verdict.
CI_EVENTS = ("pull_request", "pull_request_review")
# Both lists name what could switch the verifier off, so neither may leave it.
SELF_OWNED = (TIERS, OWNERS)
# A COMMENTED review leaves GitHub's approval state as it was.
DECIDING = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
SHA = re.compile(r"^[0-9a-f]{40}$")
# No glob, no space, no byte a look-alike could hide in: a listed path is literal.
PATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*/?$")


class Undecided(Exception):
    pass


class NotEnrolled(Exception):
    pass


def pages(path):
    """Every page of `gh api --paginate path`, each as its parsed JSON."""
    proc = subprocess.run(
        ["gh", "api", "--paginate", path], stdin=subprocess.DEVNULL, capture_output=True
    )
    if proc.returncode:
        err = proc.stderr.decode(errors="replace").strip()
        raise Undecided(f"gh api {path}: {err}")
    # --paginate prints each page's JSON one after another, not one document.
    text, decoder, got, i = proc.stdout.decode(), json.JSONDecoder(), [], 0
    try:
        while i < len(text):
            if text[i].isspace():
                i += 1
                continue
            page, i = decoder.raw_decode(text, i)
            got.append(page)
    except json.JSONDecodeError as e:
        raise Undecided(f"gh api {path}: not JSON ({e})") from e
    return got


def api(path):
    """Every page of `gh api --paginate path`, as one list for an array endpoint."""
    got = pages(path)
    if len(got) == 1 and isinstance(got[0], dict):
        return got[0]
    if not all(isinstance(p, list) for p in got):
        raise Undecided(f"gh api {path}: unexpected response shape")
    return [item for page in got for item in page]


def listed(path, key):
    """Every item under key across the pages of an endpoint that wraps its list in an object."""
    got = pages(path)
    if not got or not all(
        isinstance(p, dict) and isinstance(p.get(key), list) for p in got
    ):
        raise Undecided(f"gh api {path}: no {key} list")
    items = [item for page in got for item in page[key]]
    total = got[0].get("total_count", len(items))
    # The runs endpoint stops at 1000 results, so a shorter list would be a guess.
    if type(total) is not int or len(items) < total:
        raise Undecided(f"gh api {path}: listed {len(items)} of {total} {key}")
    return items


def blob(repo, path, ref):
    """A regular file's bytes at ref; a link, a directory or a file too large to serve is undecided."""
    got = api(f"repos/{repo}/contents/{path}?ref={ref}")
    if not isinstance(got, dict) or got.get("type") != "file":
        raise Undecided(f"{path} at {ref[:12]} is not a regular file")
    if got.get("encoding") != "base64":
        raise Undecided(f"{path} at {ref[:12]} is not served whole (over 1 MB)")
    return base64.b64decode(got.get("content") or "")


def literal_path(entry, where):
    if not isinstance(entry, str) or not PATH.match(entry) or ".." in entry.split("/"):
        raise Undecided(f"{where}: {entry!r} is not a literal repository path")
    return entry


def load_tiers(raw):
    try:
        text = raw.decode("ascii")
        data = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise Undecided(f"{TIERS} at the base: {e}") from e
    unknown = set(data) - {"owner", "ci", *ROUTES}
    if unknown:
        raise Undecided(f"{TIERS} at the base: unknown key {sorted(unknown)[0]}")
    owner = data.get("owner")
    if (
        not isinstance(owner, dict)
        or set(owner) != {"login", "id"}
        or not isinstance(owner["login"], str)
        or not re.fullmatch(r"[A-Za-z0-9-]+", owner["login"])
        or type(owner["id"]) is not int
        or owner["id"] <= 0
    ):
        raise Undecided(f"{TIERS} at the base: owner must be {{ login, id }}")
    tiers, seen = {}, set()
    for route in ROUTES:
        entries = data.get(route, [])
        if not isinstance(entries, list):
            raise Undecided(f"{TIERS} at the base: {route} is not a list")
        tiers[route] = [literal_path(e, f"{TIERS} {route}") for e in entries]
        for e in tiers[route]:
            if e in seen:
                raise Undecided(f"{TIERS} at the base: {e} is listed twice")
            seen.add(e)
    tiers["ci"] = literal_path(data.get("ci", CI), f"{TIERS} ci")
    if tiers["ci"].endswith("/"):
        raise Undecided(f"{TIERS} at the base: ci names a directory, not a workflow")
    for must in SELF_OWNED:
        if must not in tiers["maintainer"]:
            raise Undecided(f"{TIERS} at the base: maintainer must list {must}")
    return owner, tiers


def check_owners(raw, owner, maintainer):
    """CODEOWNERS is the native form of the maintainer list; any other shape is undecided."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as e:
        raise Undecided(f"{OWNERS} at the base holds a non-ASCII byte") from e
    patterns = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2 or parts[1] != f"@{owner['login']}":
            raise Undecided(f"{OWNERS}:{n} is not `/<path> @{owner['login']}`")
        if not parts[0].startswith("/"):
            raise Undecided(f"{OWNERS}:{n} is not anchored at the root")
        patterns.append(literal_path(parts[0][1:], f"{OWNERS}:{n}"))
    if len(set(patterns)) != len(patterns) or set(patterns) != set(maintainer):
        missing = sorted(set(maintainer) - set(patterns))
        extra = sorted(set(patterns) - set(maintainer))
        raise Undecided(
            f"{OWNERS} and the maintainer list of {TIERS} differ at the base"
            f" (only in the list: {missing}; only in {OWNERS}: {extra})"
        )


def check_run_id(job):
    url = job.get("check_run_url")
    tail = url.rsplit("/", 1)[-1] if isinstance(url, str) else ""
    if not tail.isdigit():
        raise Undecided(f"job {job.get('id')} names no check run")
    return int(tail)


def ci_ran(repo, head, base, path):
    """(notes, failures): whether the base's CI ran at head, and is all that is named checks there."""
    blob(repo, path, base)
    runs = [
        r
        for r in listed(
            f"repos/{repo}/actions/runs?head_sha={head}&per_page=100", "workflow_runs"
        )
        if r["head_sha"] == head and r["path"] == path and r["event"] in CI_EVENTS
    ]
    jobs = {
        r["id"]: listed(
            f"repos/{repo}/actions/runs/{r['id']}/jobs?filter=all&per_page=100", "jobs"
        )
        for r in runs
    }
    notes, failures = [], []
    pr_runs = [r for r in runs if r["event"] == "pull_request"]
    if not pr_runs:
        failures.append(f"ci: no pull_request run of {path} at the head")
    else:
        run = max(pr_runs, key=lambda r: r["id"])
        which = f"run {run['id']} (attempt {run['run_attempt']})"
        mine = [
            j
            for j in jobs[run["id"]]
            if j["name"] == CI_JOB and j["run_attempt"] == run["run_attempt"]
        ]
        if len(mine) != 1:
            many = f"{len(mine)} jobs" if mine else "no job"
            failures.append(f"ci: {which} has {many} named {CI_JOB}")
        elif mine[0]["status"] != "completed":
            failures.append(
                f"ci: the {CI_JOB} job of {which} is {mine[0]['status']}; it is judged again once it ends"
            )
        elif mine[0]["conclusion"] != "success":
            failures.append(
                f"ci: the {CI_JOB} job of {which} concluded {mine[0]['conclusion']}"
            )
        else:
            notes.append(f"ci: {which} of {path}: {CI_JOB} concluded success")
    ours = {check_run_id(j) for js in jobs.values() for j in js if j["name"] == CI_JOB}
    for c in listed(
        f"repos/{repo}/commits/{head}/check-runs?check_name={CI_JOB}&filter=all&per_page=100",
        "check_runs",
    ):
        if c["name"] == CI_JOB and c["id"] not in ours:
            app = c.get("app") or {}
            failures.append(
                f"ci: check run {c['id']} named {CI_JOB} from {app.get('slug')} (app {app.get('id')})"
                f" is not the {CI_JOB} job of a run of {path} at the head"
            )
    return notes, failures


def route_of(path, tiers):
    for route in ROUTES:
        for e in tiers[route]:
            if path == e or (e.endswith("/") and path.startswith(e)):
                return route
    return None


def owner_approved(repo, head, default, owner):
    """Whether the owner's latest deciding review is APPROVED on head, on any pull request at head."""
    for pr in api(f"repos/{repo}/commits/{head}/pulls"):
        if pr["head"]["sha"] != head or pr["base"]["ref"] != default:
            continue
        mine = [
            r
            for r in api(f"repos/{repo}/pulls/{pr['number']}/reviews")
            if (r.get("user") or {}).get("id") == owner["id"]
            and r.get("state") in DECIDING
        ]
        # The endpoint lists reviews in the order they were submitted.
        if mine and mine[-1]["state"] == "APPROVED" and mine[-1]["commit_id"] == head:
            return pr["number"]
    return None


def weakening(tree, repo, base, head):
    proc = subprocess.run(
        [
            sys.executable,
            str(HERE / "weakening.py"),
            *("--base", base, "--head", head, "--repo", repo),
        ],
        cwd=tree,
        env={
            **os.environ,
            "GATES_DIR": str(HERE.parent),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()]
    if proc.returncode == 2 or proc.returncode not in (0, 1):
        raise Undecided("; ".join(lines) or f"weakening.py exited {proc.returncode}")
    return proc.returncode == 1, lines


def review_verdict(path, head):
    try:
        got = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise Undecided(f"review verdict {path}: {e}") from e
    if (
        not isinstance(got, dict)
        or set(got) != {"head", "blocking"}
        or type(got["blocking"]) is not int
        or got["blocking"] < 0
    ):
        raise Undecided(f"review verdict {path}: not {{head, blocking}}")
    if got["head"] != head:
        raise Undecided(
            f"review verdict {path} is for {str(got['head'])[:12]}, not the head"
        )
    return got["blocking"]


def decide(args):
    repo, head, base = args.repo, args.head, args.base
    default = api(f"repos/{repo}")["default_branch"]
    pr = api(f"repos/{repo}/pulls/{args.pr}")
    if pr["base"]["ref"] != default:
        return 1, [
            f"refused: the base is {pr['base']['ref']}, not {default}; the verdict is only ever against the default branch"
        ]
    if pr["head"]["sha"] != head or pr["base"]["sha"] != base:
        return 1, [
            f"refused: this run is for {head[:12]} on {base[:12]}, but the pull request is now"
            f" {pr['head']['sha'][:12]} on {pr['base']['sha'][:12]}; the run for those decides"
        ]
    try:
        raw = blob(repo, TIERS, base)
    except Undecided as e:
        # Only GitHub's plain Not Found on this one read: the contents API's answer for an
        # absent path. A 404 with another message (no commit for the ref), a 5xx, a 401,
        # a timeout or a non-file stays undecided.
        if str(e).endswith(": gh: Not Found (HTTP 404)"):
            raise NotEnrolled(f"not enrolled: no {TIERS} at {base}") from e
        raise
    owner, tiers = load_tiers(raw)
    check_owners(blob(repo, OWNERS, base), owner, tiers["maintainer"])
    ci_notes, ci_failures = ci_ran(repo, head, base, tiers["ci"])
    files = api(f"repos/{repo}/pulls/{args.pr}/files")
    if len(files) < pr["changed_files"]:
        # The files endpoint stops at 3000, so an unlisted verdict would be a guess.
        raise Undecided(f"listed {len(files)} of {pr['changed_files']} changed files")

    held, verified, notes = [], [], []
    for f in files:
        names = [n for n in (f.get("filename"), f.get("previous_filename")) if n]
        routes = [r for r in (route_of(n, tiers) for n in names) if r]
        route = min(routes, key=ROUTES.index, default=None)
        shown = " <- ".join(names)
        if route == "maintainer":
            held.append(f"maintainer {shown}: needs the owner's approval at the head")
        elif route == "workflow":
            held.append(
                f"workflow {shown}: its reader, canary and review are not built, so it needs the owner's approval"
            )
        elif route == "verified":
            verified.append(shown)
        elif route == "checked":
            notes.append(f"checked {shown}: passes on this check")
    weakened, wlines = weakening(args.tree, repo, base, head)
    notes.extend(wlines)
    if weakened:
        held.append(
            "weakening: an unmatched weakening above needs the owner's approval"
        )

    if verified:
        fork = (pr["head"].get("repo") or {}).get("full_name") != repo
        if fork or not args.review:
            why = (
                "a head outside the repository gets the deterministic verdict alone"
                if fork
                else "no review verdict was given"
            )
            held += [
                f"verified {p}: {why}, so it needs the owner's approval"
                for p in verified
            ]
        elif blocking := review_verdict(args.review, head):
            held += [
                f"verified {p}: the review holds {blocking} blocking finding(s)"
                for p in verified
            ]
        else:
            notes += [
                f"verified {p}: the review holds no blocking finding" for p in verified
            ]

    notes.extend(ci_notes)
    if ci_failures:
        # No approval stands in for tests that did not run.
        return 1, [
            *notes,
            *held,
            *ci_failures,
            f"refused: the head's tests are not shown to have run at {head[:12]}",
        ]
    if not held:
        return 0, [*notes, f"pass: {head[:12]} needs nothing more"]
    number = owner_approved(repo, head, default, owner)
    if number:
        return 0, [
            *notes,
            *held,
            f"pass: {owner['login']} approved {head[:12]} on pull request {number}",
        ]
    lines = [*notes, *held]
    if pr["user"].get("id") == owner["id"]:
        lines.append(
            f"opened under the owner's login, whose own pull request {owner['login']} cannot approve;"
            " reopen it as the builder App"
        )
    return 1, [
        *lines,
        f"refused: {len(held)} item(s) above need {owner['login']}'s approval at {head[:12]}",
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--tree", required=True)
    ap.add_argument("--review")
    args = ap.parse_args()
    if (
        not args.pr.isdigit()
        or not SHA.match(args.head)
        or not SHA.match(args.base)
        or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", args.repo)
    ):
        print(
            "owner-verify: could not decide: --pr must be digits, --head and --base 40 hex, --repo OWNER/NAME",
            file=sys.stderr,
        )
        return 2
    try:
        code, lines = decide(args)
    except NotEnrolled as e:
        print(f"owner-verify: {e}")
        return 3
    except (Undecided, KeyError, TypeError) as e:
        print(f"owner-verify: could not decide: {e}", file=sys.stderr)
        return 2
    for line in lines:
        print(f"owner-verify: {line}", file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
