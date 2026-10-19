#!/usr/bin/env python3
"""Run a command and refuse when it leaves a path behind outside its own temporary directory.

Usage: scratch-guard.py [--tree-only] [--root DIR] -- COMMAND [ARG...]

The command runs with TMPDIR set to a directory made under the system temporary
directory and removed afterwards, so a run that cleans up after itself writes
only there. Before and after, this lists

  tree  every path under DIR (default: the current directory), except the
        directories of build output and caches that a tool expects to fill:
        SKIP_DIRS.
  temp  the entries directly under the system temporary directory, unless
        --tree-only. A shared /tmp has other writers, so a machine that is
        not a job's own should pass --tree-only.

Exit status: 1 when a path is new after the run, whatever the command's own
status, and each is printed as `scratch-guard: left behind: PATH (tree|temp)`;
otherwise the command's status; 2 when it could not run. Only a new path counts:
a file the run changed or removed is a different question.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

SKIP_DIRS = {".git", "node_modules", "target", ".venv", "__pycache__"}


def tree_paths(root, own):
    found = set()
    for here, dirs, files in os.walk(root):
        dirs[:] = [
            d
            for d in dirs
            if d not in SKIP_DIRS and os.path.realpath(os.path.join(here, d)) != own
        ]
        found.update(os.path.relpath(os.path.join(here, f), root) for f in files)
    return found


def snapshot(root, temp, own):
    entries = set(os.listdir(temp)) - {os.path.basename(own)} if temp else set()
    return tree_paths(root, own), entries


def main():
    ap = argparse.ArgumentParser(
        usage="scratch-guard.py [--tree-only] [--root DIR] -- COMMAND [ARG...]"
    )
    ap.add_argument("--tree-only", action="store_true")
    ap.add_argument("--root", default=".")
    ap.add_argument("command", nargs="+")
    args = ap.parse_args()
    temp = None if args.tree_only else tempfile.gettempdir()
    own = os.path.realpath(tempfile.mkdtemp(prefix="scratch-guard."))
    try:
        before = snapshot(args.root, temp, own)
        try:
            status = subprocess.run(
                args.command, env={**os.environ, "TMPDIR": own}
            ).returncode
        except OSError as e:
            print(
                f"scratch-guard: {args.command[0]} could not run: {e}", file=sys.stderr
            )
            return 2
        after = snapshot(args.root, temp, own)
    finally:
        # Best effort: it is this script's own directory, and a failure to remove it costs disk only.
        shutil.rmtree(own, ignore_errors=True)
    new = [(p, "tree") for p in sorted(after[0] - before[0])]
    new += [(p, "temp") for p in sorted(after[1] - before[1])]
    for path, where in new:
        print(f"scratch-guard: left behind: {path} ({where})")
    if new:
        print(
            f"scratch-guard: {len(new)} new path(s) outside the command's own temporary directory"
        )
        return 1
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    sys.exit(main())
