#!/usr/bin/env python3
"""Refuse an instruction file that names a repository path which is not there.

Usage: path-truth.py [root]    (default: the current directory)

Every single-backtick span in an AGENTS.md or CLAUDE.md that looks like a
repository path must name a file or directory of the tree (as a whole path or
as its trailing segments, because prose cites `tools.tsv` for
`a/b/tools.tsv`), or carry a marker such as `(absent here)` right after the span.
Not judged, because prose uses them for things that are not tree paths: fenced
blocks, URLs, globs, `~/` and `/` paths (a CI checkout cannot answer for a
machine's home directory), `.git/` and build output, a bare name without a
stem or a known extension, and
a two-segment name without an extension (`owner/repo`, `feat/branch`).

Resolution cannot see a path that exists but is owned by another repository
now, so SYNCED_FROM_GATES names the files whose owner moved from automation to
gates on 2026-09-16 and refuses a line that credits automation for one.
"""

import os
import re
import sys

NAMES = {"AGENTS.md", "CLAUDE.md"}
SKIP_DIRS = {".git", "node_modules", "target", "vendor", ".venv"}
SPAN = re.compile(r"`([^`\n]+)`(\s*\(absent[^)]*\))?")
LINE_SUFFIX = re.compile(r":\d+(-\d+)?$")
FILE_EXTENSION = re.compile(
    r"\.(sh|md|json|jsonl|toml|ya?ml|py|rs|[cm]?[jt]sx?|css|html|tsv|csv|txt|conf|lock|cfg|ini|plist)$"
)
ANY_EXTENSION = re.compile(r"[^/.]\.[A-Za-z][A-Za-z0-9]{0,5}$")
SHAPE = re.compile(r"^[A-Za-z0-9_.@+-]+(/[A-Za-z0-9_.@+-]+)*/?$")
SYNCED_FROM_GATES = {".gitleaks.toml", ".githooks/pre-push", ".githooks/commit-msg"}


def candidate(span):
    """The repository-relative path a span names, or None when it names none."""
    span = LINE_SUFFIX.sub("", span.strip())
    span = span.removeprefix("./")
    if span.startswith(
        ("~", "/", "-", "@", ".git/", "refs/", "node_modules/", "target/", ".../")
    ):
        return None
    if "://" in span or span.endswith("..") or not SHAPE.match(span):
        return None
    span = span.rstrip("/")
    segments = span.split("/")
    if len(segments) == 1:
        # `.mjs` is an extension, not a file.
        return (
            span
            if FILE_EXTENSION.search(span) and span.count(".") > (span[0] == ".")
            else None
        )
    if len(segments) == 2 and not ANY_EXTENSION.search(span):
        return None
    return span


def walk(root):
    """The instruction files, and every path suffix the tree can be cited by."""
    instructions, suffixes = [], set()
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        base = os.path.relpath(here, root)
        for name in dirs + files:
            parts = os.path.normpath(os.path.join(base, name)).split(os.sep)
            suffixes.update("/".join(parts[i:]) for i in range(len(parts)))
        instructions += [os.path.join(here, n) for n in sorted(files) if n in NAMES]
    return instructions, suffixes


def findings(path, root, suffixes):
    here = os.path.dirname(path)
    rel = os.path.relpath(path, root)
    fenced = False
    with open(path, encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
                continue
            if fenced:
                continue
            spans = SPAN.findall(line)
            for text, absent in spans:
                target = candidate(text)
                if target is None:
                    continue
                if (
                    target in SYNCED_FROM_GATES
                    and "automation" in line
                    and "gates" not in line
                ):
                    yield f"{rel}:{number}: `{target}` is synced from gates, but the line names automation"
                if (
                    absent
                    or target in suffixes
                    or os.path.exists(os.path.join(here, target))
                ):
                    continue
                yield f"{rel}:{number}: `{target}` does not exist; fix it or mark it `{target}` (absent here)"


def main():
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    instructions, suffixes = walk(root)
    bad = [f for path in instructions for f in findings(path, root, suffixes)]
    for finding in bad:
        print(finding)
    if bad:
        print(f"{len(bad)} instruction path(s) are not true.", file=sys.stderr)
        return 1
    print("Every backticked path in the instruction files resolves. OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
