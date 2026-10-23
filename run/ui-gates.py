#!/usr/bin/env python3
"""The UI rows: axe accessibility and Linux pixel baselines, in a pinned Chromium.

Usage: ui-gates.py axe <page>...
       ui-gates.py pixel <page> <baseline.png>
       ui-gates.py record <page> <baseline.png>
       ui-gates.py stamp

A page is an http(s) URL or a path to an HTML file.

axe     axe-core's wcag2a and wcag2aa rules over each page; a serious or critical
        violation is a finding, a moderate or minor one is printed and passes.
pixel   a screenshot of the page against its baseline: a pixel differs when any
        8-bit channel moved by more than TOLERANCE, and the gate passes only when
        none does. Two sizes are a finding. The screenshot and a diff (the shot
        faded, each differing pixel red) are kept in a fresh directory under
        TMPDIR, whose path the finding prints. Linux only: baselines are recorded
        there, and a Mac renders fonts differently, so elsewhere it is refused.
record  write the page's screenshot as its baseline; Linux only, as pixel.
stamp   print the pins and the versions UI_GATES_MODULES holds, as the runner's
        cache-key probe.

These are fac browse's two gate rules (its `browse.A11yGate` and
`browse.PixelGate`) on the same pins, driven by run/ui-gates.mjs through
Playwright rather than through a fac workspace lease; README.md, "The UI rows",
owns why and the install.

Exit status: 0 pass, 1 a finding, 2 could not decide. Every exit 2 prints
`ui-gates: refused (TYPE): ...`, and TYPE is one of:

missing-tool      node is not on PATH, UI_GATES_MODULES is unset, or Playwright
                  does not load from it.
wrong-version     Playwright, axe-core or the headless shell is not the pin.
missing-page      a page path does not exist.
missing-baseline  pixel: the baseline does not exist; record it.
not-linux         pixel or record on a system other than Linux.
tool-error        the browser crashed, timed out, the page did not load, or a
                  PNG is not one this script reads (8-bit RGB or RGBA).
"""

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

PINS = {"playwright": "1.63.0", "axe-core": "4.13.0", "browser": "153.0.8010.12"}
MODULES_ENV = "UI_GATES_MODULES"
TAGS = "wcag2a,wcag2aa"
BLOCKING = ("serious", "critical")
TOLERANCE = 25
TIMEOUT = 120
DRIVER = Path(__file__).with_name("ui-gates.mjs")


class Refused(Exception):
    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind


def page_url(page):
    if re.match(r"https?://", page):
        return page
    path = Path(page).resolve()
    if not path.is_file():
        raise Refused("missing-page", f"{page} does not exist")
    return path.as_uri()


def browse(verb, page, arg):
    """The driver's report, once it ran on the pinned versions."""
    node = shutil.which("node")
    if node is None:
        raise Refused("missing-tool", "node is not on PATH")
    modules = os.environ.get(MODULES_ENV)
    if not modules:
        raise Refused(
            "missing-tool",
            f"{MODULES_ENV} is not set to a node_modules holding Playwright {PINS['playwright']}"
            f" and axe-core {PINS['axe-core']} (install steps in README.md)",
        )
    argv = [node, str(DRIVER), modules, verb, page_url(page), arg]
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise Refused(
            "tool-error", f"the browser timed out after {TIMEOUT}s on {page}"
        ) from e
    if proc.returncode == 3:
        raise Refused(
            "missing-tool", proc.stderr.strip().removeprefix("missing-tool: ")
        )
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines:
        last = (proc.stderr.strip() or "no output").splitlines()[-1]
        raise Refused(
            "tool-error", f"the browser exited {proc.returncode} on {page}: {last}"
        )
    try:
        report = json.loads(lines[-1])
        versions = report["versions"].items()
    except (ValueError, KeyError, AttributeError) as e:
        raise Refused(
            "tool-error",
            f"the browser's report on {page} is not one this script reads: {lines[-1][:200]}",
        ) from e
    for name, found in versions:
        if found != PINS[name]:
            raise Refused(
                "wrong-version", f"{name} is {found}, the pin is {PINS[name]}"
            )
    return report


def axe(*pages):
    if not pages:
        raise Refused("missing-page", "axe takes at least one page")
    blocking = []
    for page in pages:
        violations = browse("axe", page, TAGS).get("violations")
        if not isinstance(violations, list):
            raise Refused(
                "tool-error",
                f"the browser's report on {page} carries no violation list",
            )
        for v in violations:
            line = f"{page}: {v['id']} ({v['impact']}, {v['nodes']} nodes) {v['help']}"
            print(line)
            if v["impact"] in BLOCKING:
                blocking.append(
                    f"{page}: {v['id']} ({v['impact']}, {v['nodes']} nodes)"
                )
    if blocking:
        print(
            f"ui-gates: axe refused: the gate blocks on {' and '.join(BLOCKING)} violations: "
            + ", ".join(blocking)
        )
        return 1
    print(
        f"ui-gates: axe-core {PINS['axe-core']}: {len(pages)} page(s), no serious or critical violation"
    )
    return 0


def linux():
    uname = shutil.which("uname")
    system = (
        subprocess.run([uname], capture_output=True, text=True).stdout.strip()
        if uname
        else ""
    )
    if system != "Linux":
        raise Refused(
            "not-linux",
            f"pixels are judged only on Linux, where baselines are recorded; this is {system or 'unknown'}",
        )


def read_png(path):
    """(width, height, RGBA bytes) of an 8-bit, non-interlaced RGB or RGBA PNG."""
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise Refused("tool-error", f"{path} is not a PNG")
    pos, idat, head = 8, [], None
    while pos < len(data):
        size, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + size]
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        pos += 12 + size
    if not head or head[2] != 8 or head[3] not in (2, 6) or head[6] != 0:
        raise Refused(
            "tool-error", f"{path} is not an 8-bit, non-interlaced RGB or RGBA PNG"
        )
    width, height, channels = head[0], head[1], 4 if head[3] == 6 else 3
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    prev = bytearray(stride)
    rows = []
    for y in range(height):
        start = y * (stride + 1)
        kind, row = raw[start], bytearray(raw[start + 1 : start + 1 + stride])
        if kind == 1:
            for i in range(channels, stride):
                row[i] = (row[i] + row[i - channels]) & 255
        elif kind == 2:
            row = bytearray((a + b) & 255 for a, b in zip(row, prev, strict=True))
        elif kind == 3:
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                a = row[i - channels] if i >= channels else 0
                b, c = prev[i], prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[i] = (
                    row[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)
                ) & 255
        rows.append(bytes(row))
        prev = row
    pixels = b"".join(rows)
    if channels == 3:
        rgba = bytearray(width * height * 4)
        rgba[0::4], rgba[1::4], rgba[2::4] = pixels[0::3], pixels[1::3], pixels[2::3]
        rgba[3::4] = b"\xff" * (width * height)
        pixels = bytes(rgba)
    return width, height, pixels


def write_png(path, width, height, rgba):
    def chunk(kind, body):
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body))
        )

    stride = width * 4
    raw = b"".join(b"\0" + rgba[y * stride : (y + 1) * stride] for y in range(height))
    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def compare(baseline, shot):
    """(differing pixel count, diff RGBA or None); a size mismatch is a finding of its own."""
    bw, bh, want = read_png(baseline)
    sw, sh, got = read_png(shot)
    if (bw, bh) != (sw, sh):
        return f"the screenshot is {sw}x{sh} and the baseline {bw}x{bh}", None
    if want == got:
        return 0, None
    diff, differing = bytearray(len(got)), 0
    for i in range(0, len(got), 4):
        if any(abs(want[i + c] - got[i + c]) > TOLERANCE for c in range(4)):
            differing += 1
            diff[i : i + 4] = b"\xff\x00\x00\xff"
        else:
            diff[i : i + 4] = bytes(
                (191 + got[i] // 4, 191 + got[i + 1] // 4, 191 + got[i + 2] // 4, 255)
            )
    return differing, bytes(diff)


def pixel(page, baseline):
    linux()
    if not Path(baseline).is_file():
        raise Refused(
            "missing-baseline",
            f"{baseline} does not exist; record it with `ui-gates.py record {page} {baseline}` on Linux",
        )
    out = Path(tempfile.mkdtemp(prefix="ui-gates-"))
    shot = out / "screenshot.png"
    browse("shot", page, str(shot))
    differing, diff = compare(baseline, shot)
    if differing == 0:
        # Best effort: a pass leaves nothing to look at, and the directory is under TMPDIR.
        shutil.rmtree(out, ignore_errors=True)
        print(f"ui-gates: {page} matches {baseline}")
        return 0
    if isinstance(differing, str):
        print(f"ui-gates: pixel refused {page}: {differing}; the screenshot is {shot}")
        return 1
    width, height, _ = read_png(shot)
    write_png(out / "diff.png", width, height, diff)
    print(
        f"ui-gates: pixel refused {page}: {differing} pixels differ from {baseline}"
        f" by more than {TOLERANCE} in a channel; the screenshot is {shot}, the diff {out / 'diff.png'}"
    )
    return 1


def record(page, baseline):
    linux()
    Path(baseline).parent.mkdir(parents=True, exist_ok=True)
    browse("shot", page, str(Path(baseline).resolve()))
    print(f"ui-gates: recorded {baseline} from {page}")
    return 0


def stamp():
    print(" ".join(f"{k}={v}" for k, v in PINS.items()))
    modules = os.environ.get(MODULES_ENV, "")
    for name in ("playwright", "axe-core"):
        manifest = Path(modules) / name / "package.json"
        found = (
            json.loads(manifest.read_text())["version"]
            if modules and manifest.is_file()
            else "absent"
        )
        print(f"{name} {found}")
    return 0


CHECKS = {
    "axe": (axe, None),
    "pixel": (pixel, 2),
    "record": (record, 2),
    "stamp": (stamp, 0),
}


def main(argv):
    check, want = CHECKS.get(argv[1] if len(argv) > 1 else "", (None, None))
    if check is None or (want is not None and len(argv) - 2 != want):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        return check(*argv[2:])
    except Refused as e:
        print(f"ui-gates: refused ({e.kind}): {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
