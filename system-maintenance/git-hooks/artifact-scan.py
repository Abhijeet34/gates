#!/usr/bin/env python3
"""Refuse a release artifact that carries this machine's identifiers.

    artifact-scan.py <file-or-directory>...

Runs exposure-scan.py's derived needles over every byte of each artifact,
binaries included, which the push gate cannot read. A directory (a .app
bundle) is walked, and a symlink's target text is scanned as well. Prints
`class  count  path` per hit and never the matched value; a path that itself
holds a needle is redacted. system-maintenance/git-hooks/README.md, "Release
artifacts", owns the design.

Exit 0 clean, 1 on any machine.* hit or when the needles cannot be derived,
2 for a bad argument or an unreadable path: an artifact that was not read
never passes.
"""

import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = "artifact-scan"

# exposure-scan.py has a hyphen in its name, and its own `from pushrange`
# import resolves against its directory.
sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location(
    "exposure_scan", os.path.join(HERE, "exposure-scan.py")
)
exposure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exposure)


def byte_needles():
    """[(class, bytes pattern)]: the same patterns, matched byte for byte.

    A str pattern's \\w is Unicode-aware, so a path followed by a stray
    high byte would escape its lookahead; a bytes pattern's \\w is ASCII.
    """
    return [
        (rule, re.compile(pat.pattern.encode("utf-8"), pat.flags & re.I))
        for rule, _, pat in exposure.derive_needles()
    ]


def count_by_class(needles, data):
    """{class: n} of non-overlapping matches, merging a class's overlapping needles.

    A hostname is two needles (full and short name), and one occurrence of the
    long form must count once.
    """
    spans = {}
    for rule, pat in needles:
        spans.setdefault(rule, []).extend(m.span() for m in pat.finditer(data))
    counts = {}
    for rule, found in spans.items():
        n, end = 0, -1
        for start, stop in sorted(found):
            if start >= end:
                n, end = n + 1, stop
            else:
                end = max(end, stop)
        if n:
            counts[rule] = n
    return counts


def files_under(path):
    """Every file under path in a stable order, or the path itself."""
    if os.path.isdir(path) and not os.path.islink(path):
        for root, dirs, names in os.walk(path):
            dirs.sort()
            for name in sorted(names):
                yield os.path.join(root, name)
    else:
        yield path


def read_artifact(path):
    if os.path.islink(path):
        return os.fsencode(os.readlink(path))
    with open(path, "rb") as fh:
        return fh.read()


def main(argv):
    if not argv:
        sys.stderr.write("usage: %s <file-or-directory>...\n" % TOOL)
        return 2
    for path in argv:
        if not os.path.lexists(path):
            sys.stderr.write("%s: %s does not exist\n" % (TOOL, path))
            return 2
    needles = byte_needles()
    text_needles = exposure.derive_needles()
    scanned = hits = 0
    for arg in argv:
        for path in files_under(arg):
            try:
                data = read_artifact(path)
            except OSError as exc:
                sys.stderr.write(
                    "%s: cannot read %s: %s\n"
                    % (TOOL, exposure.redact(text_needles, path), exc.strerror)
                )
                return 2
            scanned += 1
            for rule, n in sorted(count_by_class(needles, data).items()):
                hits += n
                print("%-22s %7d  %s" % (rule, n, exposure.redact(text_needles, path)))
    if hits:
        sys.stderr.write(
            "%s: REFUSED, %d machine identifier(s) in %d file(s) scanned. "
            "Remove them at the build (path remapping) and rebuild; "
            "the value is never printed.\n" % (TOOL, hits, scanned)
        )
        return 1
    print("%s: %d file(s) scanned, no machine identifier found." % (TOOL, scanned))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
