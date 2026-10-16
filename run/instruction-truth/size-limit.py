#!/usr/bin/env python3
"""Refuse an instruction file too large for a session to load whole.

Usage: size-limit.py [root]    (default: the current directory)

Any AGENTS.md or CLAUDE.md over MAX_LINES lines fails, and so does one whose
parent directory is named `home` (the file deployed as ~/AGENTS.md) over
HOME_MAX_BYTES, because that one loads into every session on the machine.
"""

import os
import sys

NAMES = {"AGENTS.md", "CLAUDE.md"}
SKIP_DIRS = {".git", "node_modules", "target", "vendor", ".venv"}
MAX_LINES = 200
HOME_MAX_BYTES = 5000


def findings(root):
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(n for n in files if n in NAMES):
            path = os.path.join(here, name)
            rel = os.path.relpath(path, root)
            with open(path, "rb") as handle:
                data = handle.read()
            lines = data.count(b"\n") + (not data.endswith(b"\n") and bool(data))
            if lines > MAX_LINES:
                yield f"{rel}: {lines} lines, over the {MAX_LINES}-line limit"
            if os.path.basename(here) == "home" and len(data) > HOME_MAX_BYTES:
                yield f"{rel}: {len(data)} bytes, over the {HOME_MAX_BYTES}-byte limit for a home file"


def main():
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    bad = list(findings(root))
    for finding in bad:
        print(finding)
    if bad:
        print(f"{len(bad)} instruction file(s) are too large.", file=sys.stderr)
        return 1
    print("Every instruction file is within its size limit. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
