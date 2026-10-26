#!/usr/bin/env python3
"""Hold a change to policy/relaxations.toml to the maintainer's approving review.

Usage: record-approval.py --repo OWNER/NAME --pr N --head SHA

Reads the pull request, its files and its reviews live through `gh api`. A pull
request that does not change RECORDS passes. One that does passes only when
APPROVER's latest review is APPROVED and was submitted on the head commit, so a
push after the approval refuses again. One opened by APPROVER refuses, because
GitHub refuses an author's approval of their own pull request; a relaxation is
opened by a bot identity. An empty `--pr` (a push to main) passes.

Exit status: 0 pass, 1 refused, 2 could not decide.
"""

import argparse
import json
import subprocess
import sys

RECORDS = "policy/relaxations.toml"
APPROVER = "Abhijeet34"
# A COMMENTED review leaves GitHub's approval state as it was, so it is not the
# latest review for this purpose either.
DECIDING = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}


class Undecided(Exception):
    pass


def api(path):
    """Every page of `gh api --paginate path`, as one list for an array endpoint."""
    proc = subprocess.run(
        ["gh", "api", "--paginate", path], stdin=subprocess.DEVNULL, capture_output=True
    )
    if proc.returncode:
        err = proc.stderr.decode(errors="replace").strip()
        raise Undecided(f"gh api {path}: {err}")
    # --paginate prints each page's JSON one after another, not one document.
    text, decoder, pages, i = proc.stdout.decode(), json.JSONDecoder(), [], 0
    try:
        while i < len(text):
            if text[i].isspace():
                i += 1
                continue
            page, i = decoder.raw_decode(text, i)
            pages.append(page)
    except json.JSONDecodeError as e:
        raise Undecided(f"gh api {path}: not JSON ({e})") from e
    if len(pages) == 1 and isinstance(pages[0], dict):
        return pages[0]
    if not all(isinstance(p, list) for p in pages):
        raise Undecided(f"gh api {path}: unexpected response shape")
    return [item for page in pages for item in page]


def same(login):
    return (login or "").lower() == APPROVER.lower()


def decide(repo, number, head):
    pr = api(f"repos/{repo}/pulls/{number}")
    live = pr["head"]["sha"]
    if live != head:
        return 1, (
            f"refused: this run is for {head[:12]}, but the head is now {live[:12]};"
            " the run for the head decides"
        )
    files = api(f"repos/{repo}/pulls/{number}/files")
    if len(files) < pr["changed_files"]:
        # The files endpoint stops at 3000, so an untouched verdict would be a guess.
        raise Undecided(
            f"listed {len(files)} of {pr['changed_files']} changed files,"
            f" so whether {RECORDS} changed is unknown"
        )
    if not any(
        RECORDS in (f.get("filename"), f.get("previous_filename")) for f in files
    ):
        return 0, f"{RECORDS} is not changed by this pull request"
    if same(pr["user"]["login"]):
        return 1, (
            f"refused: a change to {RECORDS} must be opened by a bot identity, not by"
            f" {APPROVER}, so that {APPROVER} can approve it"
        )
    reviews = [
        r
        for r in api(f"repos/{repo}/pulls/{number}/reviews")
        if same((r.get("user") or {}).get("login")) and r.get("state") in DECIDING
    ]
    if not reviews:
        return (
            1,
            f"refused: a change to {RECORDS} needs {APPROVER}'s approving review at the head, and there is none",
        )
    # The endpoint lists reviews in the order they were submitted.
    latest = reviews[-1]
    if latest["state"] != "APPROVED":
        return (
            1,
            f"refused: {APPROVER}'s latest review of this change to {RECORDS} is {latest['state']}",
        )
    if latest.get("commit_id") != head:
        return 1, (
            f"refused: {APPROVER} approved {str(latest.get('commit_id'))[:12]}, not the head"
            f" {head[:12]}; a push after the approval needs a new one"
        )
    return 0, f"{APPROVER} approved this change to {RECORDS} at the head {head[:12]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", default="")
    ap.add_argument("--head", default="")
    args = ap.parse_args()
    if not args.pr:
        print("record-approval: not a pull request, nothing to hold")
        return 0
    if not args.pr.isdigit() or not args.head:
        print(
            "record-approval: --pr must be a number and --head a commit",
            file=sys.stderr,
        )
        return 2
    try:
        code, msg = decide(args.repo, args.pr, args.head)
    except (Undecided, KeyError, TypeError) as e:
        print(f"record-approval: could not decide: {e}", file=sys.stderr)
        return 2
    print(f"record-approval: {msg}", file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
