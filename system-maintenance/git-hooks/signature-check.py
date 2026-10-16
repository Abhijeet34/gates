#!/usr/bin/env python3
"""Refuse a push to main that introduces an unsigned commit.

Policy (plan r5, P1-G4; supersedes "refuse every unsigned commit"): signatures
are enforced on main only, through GitHub-signed squash merges by the merger
App. A branch may carry unsigned commits, because a commit made with an App
token is unsigned by design, so this check says nothing about any other ref.
It never reads a signature's validity: presence of the `gpgsig` header is all
it asks, and GitHub's own required-signatures rule is what verifies. Reading
the headers is one `git cat-file --batch` (20 ms over 223 commits) where
`git log --format=%G?` verifies each signature (3,441 ms).

Usage: signature-check push <remote> <url>    # ref lines on stdin, as git sends them
Source of truth: system-maintenance/git-hooks/signature-check.py in the gates repo.
"""

import subprocess
import sys

from pushrange import ranges_from

TOOL = "signature-check"
PROTECTED = "refs/heads/main"
SHOWN = 10


def refuse(msg):
    sys.stderr.write("%s: %s\n" % (TOOL, msg))
    sys.stderr.write(
        "%s: COULD NOT ESTABLISH that this push is signed. Push refused.\n" % TOOL
    )
    return 1


def git(*argv, stdin=None):
    proc = subprocess.run(["git", *argv], input=stdin, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "git %s failed: %s"
            % (argv[0], proc.stderr.decode("utf-8", "replace").strip())
        )
    return proc.stdout


def unsigned(shas):
    """[(sha, subject)] for each commit with no signature header, one cat-file pass."""
    out = git("cat-file", "--batch", stdin="".join(s + "\n" for s in shas).encode())
    pos, found = 0, []
    for sha in shas:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].decode().split()
        if len(header) != 3 or header[1] != "commit":
            raise RuntimeError("git cat-file --batch returned no commit for %s" % sha)
        size = int(header[2])
        body = out[nl + 1 : nl + 1 + size]
        pos = nl + 1 + size + 1
        # Headers end at the first blank line; a message that quotes `gpgsig`
        # is not a signature.
        head, _, message = body.partition(b"\n\n")
        if not any(
            ln.startswith((b"gpgsig ", b"gpgsig-sha256 ")) for ln in head.split(b"\n")
        ):
            found.append((sha, message.split(b"\n", 1)[0].decode("utf-8", "replace")))
    return found


def cmd_push(argv):
    remote = argv[0] if argv else "origin"
    url = argv[1] if len(argv) > 1 else ""
    lines = [
        ln
        for ln in sys.stdin.read().splitlines()
        if len(ln.split()) >= 4 and ln.split()[2] == PROTECTED
    ]
    if not lines:
        return 0
    try:
        shas = []
        seen = set()
        for spec in ranges_from("\n".join(lines), remote, url):
            for sha in git("rev-list", *spec).decode().split():
                if sha not in seen:
                    seen.add(sha)
                    shas.append(sha)
        bad = unsigned(shas) if shas else []
    except (RuntimeError, OSError, ValueError) as exc:
        return refuse(
            "cannot read the commits of this push to %s: %s" % (PROTECTED, exc)
        )
    if not bad:
        return 0
    sys.stderr.write(
        "%s: %d commit(s) in this push to %s carry no signature:\n"
        % (TOOL, len(bad), PROTECTED)
    )
    for sha, subject in bad[:SHOWN]:
        sys.stderr.write("  %s  %s\n" % (sha[:12], subject))
    if len(bad) > SHOWN:
        sys.stderr.write("  ... and %d more\n" % (len(bad) - SHOWN))
    sys.stderr.write(
        "%s: main takes signed commits only. Land the change through a pull request,\n"
        "%s: whose squash merge GitHub signs, or sign these before pushing:\n"
        "%s:   git rebase --exec 'git commit --amend --no-edit -S' <base>\n"
        "%s: unsigned commits are fine on any other branch.\n" % ((TOOL,) * 4)
    )
    return refuse("unsigned commits on a push to %s." % PROTECTED)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "push":
        return cmd_push(sys.argv[2:])
    sys.stderr.write("usage: %s push <remote> <url>\n" % TOOL)
    return 2


if __name__ == "__main__":
    sys.exit(main())
