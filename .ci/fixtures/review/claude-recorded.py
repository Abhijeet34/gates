#!/usr/bin/env python3
"""Stand-in for the `claude` CLI that answers run/review.py from recorded replies.

Installed as `claude` beside a `replies/` directory (build.sh --replies), or
answering from $CLAUDE_RECORDED_REPLIES/$GATES_CORPUS_CASE when both are set, as
run/corpus.py names each case of a seeded corpus:

  claude --version              replies/version.txt
  claude auth status --json     replies/auth.json, else a subscription login
  claude -p ...                 replies/<seat>.json, the seat read from the prompt's
                                first line (`seat: verifier F1` -> verifier-F1.json),
                                after sleeping <seat>.sleep seconds when that exists

Every `-p` call appends {argv, seat, cwd entries, credential names it received,
prompt} to calls.jsonl beside this file, so a test can assert what each seat was
given. A seat with no recorded reply exits 1, as an unexpected call should.
"""

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPLIES = HERE / "replies"
if os.environ.get("CLAUDE_RECORDED_REPLIES") and os.environ.get("GATES_CORPUS_CASE"):
    REPLIES = Path(
        os.environ["CLAUDE_RECORDED_REPLIES"], os.environ["GATES_CORPUS_CASE"]
    )
WATCHED = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
SUBSCRIPTION = {
    "loggedIn": True,
    "authMethod": "claude.ai",
    "apiProvider": "firstParty",
}


def main():
    argv = sys.argv[1:]
    if argv == ["--version"]:
        print((REPLIES / "version.txt").read_text().strip())
        return 0
    if argv[:2] == ["auth", "status"]:
        auth = REPLIES / "auth.json"
        print(auth.read_text() if auth.exists() else json.dumps(SUBSCRIPTION))
        return 0
    if "-p" not in argv:
        print(f"claude-recorded: no recording for {argv}", file=sys.stderr)
        return 1
    prompt = sys.stdin.read()
    first = prompt.split("\n", 1)[0]
    seat = first.removeprefix("seat: ").replace(" ", "-")
    with open(HERE / "calls.jsonl", "a") as log:
        call = {
            "argv": argv,
            "seat": seat,
            "cwd": sorted(os.listdir(".")),
            "credentials": [k for k in WATCHED if k in os.environ],
            "prompt": prompt,
        }
        log.write(json.dumps(call) + "\n")
    reply = REPLIES / f"{seat}.json"
    if not first.startswith("seat: ") or not reply.exists():
        print(f"claude-recorded: no recorded reply for {first!r}", file=sys.stderr)
        return 1
    pause = REPLIES / f"{seat}.sleep"
    if pause.exists():
        time.sleep(float(pause.read_text()))
    sys.stdout.write(reply.read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
