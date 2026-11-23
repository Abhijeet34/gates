#!/usr/bin/env python3
"""Refuse a release artifact that carries this machine's identifiers.

    artifact-scan.py <file-or-directory>...

Runs exposure-scan.py's derived needles over every byte of each artifact,
binaries included, which the push gate cannot read, and over the artifact
read as UTF-16 in both byte orders and at both alignments, which is how a
Windows resource, a .NET string or a JavaScript engine's snapshot stores
text. A directory (a .app
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


def wide_needles():
    """[(class, str pattern)] for text decoded from UTF-16, one char per code unit.

    re.ASCII gives them the byte patterns' boundaries: a non-ASCII character
    next to a needle is a boundary, as a stray high byte is.
    """
    return [
        (rule, re.compile(pat.pattern, (pat.flags & re.I) | re.A))
        for rule, _, pat in exposure.derive_needles()
    ]


def utf16_views(data):
    """[(offset, text)]: data as UTF-16LE and BE from byte 0 and from byte 1.

    Each 16-bit code unit becomes exactly one character, surrogates included,
    by widening it to UTF-32 rather than decoding UTF-16, so a match at char i
    sits at byte offset + 2*i and its span lines up with the byte scan's. A
    string's alignment is not known from outside, so both are read.
    """
    views = []
    for offset in (0, 1):
        units = data[offset:]
        units = units[: len(units) & ~1]
        for order in ("le", "be"):
            wide = bytearray(2 * len(units))
            # UTF-32LE puts the low byte first: a LE unit is (low, high), so it
            # lands as is, and a BE unit is (high, low), so it lands swapped.
            if order == "le":
                wide[0::4], wide[1::4] = units[0::2], units[1::2]
            else:
                wide[0::4], wide[1::4] = units[1::2], units[0::2]
            views.append((offset, wide.decode("utf-32-le", "surrogatepass")))
    return views


def count_by_class(needles, data, wide=()):
    """{class: n} of non-overlapping matches, merging a class's overlapping needles.

    A hostname is two needles (full and short name), and one occurrence of the
    long form must count once. With wide needles the UTF-16 views are scanned
    too, their spans mapped back to bytes, so a run that reads as text in both
    byte orders (ASCII at an odd offset) is one occurrence, not two.
    """
    spans = {}
    for rule, pat in needles:
        spans.setdefault(rule, []).extend(m.span() for m in pat.finditer(data))
    if wide:
        for offset, text in utf16_views(data):
            for rule, pat in wide:
                spans.setdefault(rule, []).extend(
                    (offset + 2 * m.start(), offset + 2 * m.end())
                    for m in pat.finditer(text)
                )
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
            linked = [d for d in dirs if os.path.islink(os.path.join(root, d))]
            for name in sorted(names + linked):
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
    wide = wide_needles()
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
            for rule, n in sorted(count_by_class(needles, data, wide).items()):
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
