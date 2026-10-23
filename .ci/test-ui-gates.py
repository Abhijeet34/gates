#!/usr/bin/env python3
"""Regression tests for run/ui-gates.py, and its two rows in shadow under run/gate.py.

    .ci/test-ui-gates.py            a stub node and uname; needs no browser
    .ci/test-ui-gates.py --real     the pinned Playwright, axe-core and headless
                                    shell: UI_GATES_MODULES set, and on macOS a
                                    session with its sandbox off

The default run proves the script's own decisions: which impact blocks, how far
a channel may move, which sizes and systems are refused, and that every missing
or wrong piece is a typed refusal. `--real` puts a page with an image missing its
alt text, and a page whose heading moved, to the real browser; off Linux it
stubs uname for the pixel cases and says so, since the gate refuses there. It is
not part of .ci/check.sh until a CI job installs the browser (README.md, "The UI
rows").
"""

import datetime
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/ui-gates.py"
RUNNER = ROOT / "run/gate.py"
BUILD = ROOT / ".ci/fixtures/ui/build.sh"
MODULES_ENV = "UI_GATES_MODULES"
workdir = Path(tempfile.mkdtemp())
# The gate keeps a refused screenshot under TMPDIR, so this run's TMPDIR is its own.
ENV = {
    **{
        k: v
        for k, v in os.environ.items()
        if k != MODULES_ENV and not k.startswith("GIT_")
    },
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@invalid",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@invalid",
    "PYTHONDONTWRITEBYTECODE": "1",
    "TMPDIR": str(workdir),
}
VERSIONS = '"versions": {"playwright": "1.63.0", "browser": "153.0.8010.12", "axe-core": "4.13.0"}'


def fresh_dir():
    return Path(tempfile.mkdtemp(dir=workdir))


def run(*args, cwd, env=ENV):
    proc = subprocess.run(
        args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    return proc.returncode, proc.stdout + proc.stderr


def fixture(*steps):
    """A directory built by build.sh, one call per step."""
    root = fresh_dir()
    for step in steps:
        rc, out = run("bash", str(BUILD), *step.split(), cwd=root)
        assert rc == 0, out
    return root


def node(root, script):
    """Replace the fixture's stub node with `script`."""
    (root / "bin/node").write_text(f"#!/bin/sh\n{script}\n")
    (root / "bin/node").chmod(0o755)


def gate(root, *args, path=None, modules=True):
    env = {**ENV, "PATH": path if path is not None else str(root / "bin")}
    if modules:
        env[MODULES_ENV] = str(root / "modules")
    return run(sys.executable, str(GATE), *args, cwd=root, env=env)


def png(path, width, height, pixels, channels=3, filters=(0,)):
    """Write an 8-bit PNG, filtering row y with filters[y % len(filters)]."""
    stride = width * channels
    rows = [pixels[y * stride : (y + 1) * stride] for y in range(height)]
    raw, prev = b"", bytes(stride)
    for y, row in enumerate(rows):
        kind = filters[y % len(filters)]
        out = bytearray(stride)
        for i in range(stride):
            a = row[i - channels] if i >= channels else 0
            b, c = prev[i], prev[i - channels] if i >= channels else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            paeth = a if pa <= pb and pa <= pc else b if pb <= pc else c
            pred = (0, a, b, (a + b) >> 1, paeth)[kind]
            out[i] = (row[i] - pred) & 255
        raw += bytes([kind]) + bytes(out)
        prev = row

    def chunk(k, body):
        return (
            struct.pack(">I", len(body))
            + k
            + body
            + struct.pack(">I", zlib.crc32(k + body))
        )

    ctype = 6 if channels == 4 else 2
    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def shot_node(root, shot):
    """A stub node whose screenshot is `shot`."""
    node(root, f'for out; do :; done\n/bin/cp "{shot}" "$out"\necho \'{{{VERSIONS}}}\'')


def expect(name, result, want, *texts, absent=()):
    rc, out = result
    if rc != want:
        return f"{name}: exit {rc}, wanted {want}:\n{out}"
    for text in texts:
        if text not in out:
            return f"{name}: output does not carry {text!r}:\n{out}"
    for text in absent:
        if text in out:
            return f"{name}: output carries {text!r}:\n{out}"
    return None


def refused(kind):
    return f"ui-gates: refused ({kind}):"


def axe_cases():
    red, clean = fixture("axe red"), fixture("axe clean")
    serious = fixture("axe red")
    (serious / "report.json").write_text(
        (serious / "report.json").read_text().replace('"critical"', '"serious"')
    )
    minor = fixture("axe clean")
    (minor / "report.json").write_text(
        (minor / "report.json").read_text().replace('"moderate"', '"minor"')
    )
    two = fixture("axe clean")
    (two / "other.html").write_text("<!doctype html><title>u</title>\n")
    # The second page carries the blocking violation, so every page is judged.
    node(
        two,
        f'case "$4" in *other.html) /bin/cat "{red}/report.json" ;; *) /bin/cat "{two}/report.json" ;; esac',
    )
    old = fixture("axe clean")
    (old / "report.json").write_text(
        (old / "report.json").read_text().replace("4.13.0", "4.14.0")
    )
    return [
        expect(
            "axe: a critical violation blocks",
            gate(red, "axe", "page.html"),
            1,
            "the gate blocks on serious and critical violations: page.html: image-alt (critical, 1 nodes)",
        ),
        expect(
            "axe: a serious violation blocks",
            gate(serious, "axe", "page.html"),
            1,
            "image-alt (serious, 1 nodes)",
        ),
        expect(
            "axe: a moderate violation is printed and passes",
            gate(clean, "axe", "page.html"),
            0,
            "page.html: image-alt (moderate, 1 nodes)",
            "no serious or critical violation",
        ),
        expect(
            "axe: a minor violation passes",
            gate(minor, "axe", "page.html"),
            0,
            "(minor, 1 nodes)",
        ),
        expect(
            "axe: every page is judged",
            gate(two, "axe", "page.html", "other.html"),
            1,
            "other.html: image-alt (critical, 1 nodes)",
        ),
        expect("axe: no page", gate(clean, "axe"), 2, refused("missing-page")),
        expect(
            "axe: a page that does not exist",
            gate(clean, "axe", "gone.html"),
            2,
            refused("missing-page"),
        ),
        expect(
            "axe: no node",
            gate(clean, "axe", "page.html", path=str(fresh_dir())),
            2,
            refused("missing-tool"),
            "node is not on PATH",
        ),
        expect(
            "axe: no modules",
            gate(clean, "axe", "page.html", modules=False),
            2,
            refused("missing-tool"),
            MODULES_ENV,
        ),
        expect(
            "axe: another axe-core",
            gate(old, "axe", "page.html"),
            2,
            refused("wrong-version"),
            "axe-core is 4.14.0, the pin is 4.13.0",
        ),
    ]


def driver_cases():
    """How the driver's own exits map, whichever verb ran."""
    gone = fixture("axe clean")
    node(gone, "echo 'missing-tool: playwright does not load from x' >&2\nexit 3")
    crash = fixture("axe clean")
    node(crash, "echo 'page.goto: net::ERR_FILE_NOT_FOUND' >&2\nexit 1")
    silent = fixture("axe clean")
    node(silent, "exit 0")
    partial = fixture("axe clean")
    node(partial, f"echo '{{{VERSIONS}}}'")
    shell = fixture("pixel clean", "linux")
    node(
        shell, 'echo \'{"versions": {"playwright": "1.63.0", "browser": "154.0.0.0"}}\''
    )
    return [
        expect(
            "driver: Playwright does not load",
            gate(gone, "axe", "page.html"),
            2,
            refused("missing-tool"),
            "playwright does not load from x",
        ),
        expect(
            "driver: the page does not load",
            gate(crash, "axe", "page.html"),
            2,
            refused("tool-error"),
            "net::ERR_FILE_NOT_FOUND",
        ),
        expect(
            "driver: no report",
            gate(silent, "axe", "page.html"),
            2,
            refused("tool-error"),
        ),
        expect(
            "driver: a report with no violation list",
            gate(partial, "axe", "page.html"),
            2,
            refused("tool-error"),
            "no violation list",
        ),
        expect(
            "driver: another headless shell",
            gate(shell, "pixel", "page.html", "baseline.png"),
            2,
            refused("wrong-version"),
            "browser is 154.0.0.0, the pin is 153.0.8010.12",
        ),
    ]


def moved(root, by, channel=0, channels=3):
    """A shot of `root`'s 4x4 white baseline with one channel of the first pixel moved by `by`."""
    pixels = bytearray(b"\xff" * 16 * channels)
    pixels[channel] -= by
    shot = root / f"shot-{by}-{channel}.png"
    png(shot, 4, 4, bytes(pixels), channels)
    shot_node(root, shot)


def pixel_cases():
    results = []
    for by, want in ((25, 0), (26, 1)):
        root = fixture("pixel clean", "linux")
        moved(root, by)
        results.append(
            expect(
                f"pixel: a channel moved by {by}",
                gate(root, "pixel", "page.html", "baseline.png"),
                want,
                "matches baseline.png" if want == 0 else "1 pixels differ",
            )
        )
    alpha = fixture("pixel clean", "linux")
    png(alpha / "baseline.png", 4, 4, b"\xff" * 64, 4)
    moved(alpha, 26, channel=3, channels=4)
    results.append(
        expect(
            "pixel: alpha counts as a channel",
            gate(alpha, "pixel", "page.html", "baseline.png"),
            1,
            "1 pixels differ",
        )
    )
    red = fixture("pixel red", "linux")
    rc, out = gate(red, "pixel", "page.html", "baseline.png")
    results.append(
        expect(
            "pixel: a drawn change",
            (rc, out),
            1,
            "1 pixels differ from baseline.png by more than 25 in a channel",
            "screenshot.png",
        )
    )
    results.append(
        diff_case(out.rsplit("the diff ", 1)[-1].strip())
        if "the diff " in out
        else "pixel: no diff named"
    )
    big = fixture("pixel clean", "linux")
    png(big / "big.png", 5, 4, b"\xff" * 60)
    shot_node(big, big / "big.png")
    results.append(
        expect(
            "pixel: two sizes",
            gate(big, "pixel", "page.html", "baseline.png"),
            1,
            "the screenshot is 5x4 and the baseline 4x4",
        )
    )
    none = fixture("pixel clean", "linux")
    results.append(
        expect(
            "pixel: no baseline",
            gate(none, "pixel", "page.html", "absent.png"),
            2,
            refused("missing-baseline"),
            "record page.html absent.png",
        )
    )
    mac = fixture("pixel clean", "darwin")
    results.append(
        expect(
            "pixel: off Linux",
            gate(mac, "pixel", "page.html", "baseline.png"),
            2,
            refused("not-linux"),
            "this is Darwin",
        )
    )
    bare = fixture("pixel clean")
    results.append(
        expect(
            "pixel: no uname",
            gate(bare, "pixel", "page.html", "baseline.png"),
            2,
            refused("not-linux"),
        )
    )
    jpeg = fixture("pixel clean", "linux")
    (jpeg / "baseline.png").write_bytes(b"\xff\xd8\xff\xe0 not a png")
    results.append(
        expect(
            "pixel: a baseline that is not a PNG",
            gate(jpeg, "pixel", "page.html", "baseline.png"),
            2,
            refused("tool-error"),
            "is not a PNG",
        )
    )
    for system in ("linux", "darwin"):
        rec = fixture("pixel red", system)
        want = 0 if system == "linux" else 2
        result = gate(rec, "record", "page.html", "base/new.png")
        error = expect(
            f"record on {system}",
            result,
            want,
            "recorded base/new.png" if want == 0 else refused("not-linux"),
        )
        if (
            not error
            and want == 0
            and (rec / "base/new.png").read_bytes() != (rec / "shot.png").read_bytes()
        ):
            error = "record on linux: base/new.png is not the screenshot"
        if not error and want == 2 and (rec / "base").exists():
            error = "record on darwin: wrote base/ although it refused"
        results.append(error)
    return results


def gate_module():
    """run/ui-gates.py imported, leaving no __pycache__ in run/ for gates-proof to find."""
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(GATE.parent))
    return __import__("ui-gates")


def diff_case(path):
    """The diff marks the moved pixel red and fades the rest."""
    ui = gate_module()
    width, height, rgba = ui.read_png(path)
    first, rest = rgba[:4], set(rgba[i : i + 4] for i in range(4, len(rgba), 4))
    if (width, height, first, rest) != (
        4,
        4,
        b"\xff\x00\x00\xff",
        {bytes((254, 254, 254, 255))},
    ):
        return f"pixel: the diff is {width}x{height}, first pixel {first!r}, the rest {rest}"
    return None


def decode_cases():
    """Each PNG filter type, which Chromium's encoder mixes, decodes to the pixels written."""
    ui = gate_module()
    results = []
    for channels in (3, 4):
        pixels = bytes(
            (x * 37 + y * 11 + c * 53) % 256
            for y in range(5)
            for x in range(7)
            for c in range(channels)
        )
        path = fresh_dir() / "f.png"
        png(path, 7, 5, pixels, channels, filters=(0, 1, 2, 3, 4))
        _, _, got = ui.read_png(path)
        want = (
            pixels
            if channels == 4
            else b"".join(pixels[i : i + 3] + b"\xff" for i in range(0, len(pixels), 3))
        )
        results.append(
            None
            if got == want
            else f"decode: filters 0 to 4 with {channels} channels do not round-trip"
        )
    return results


ROWS = """
[[gate]]
id = "ui-axe"
inputs = ["page.html"]
tools = {{ ui = 'python3 "$GATES_DIR/run/ui-gates.py" stamp' }}
run = 'python3 "$GATES_DIR/run/ui-gates.py" axe page.html'
{shadow}
[[gate]]
id = "ui-pixel"
inputs = ["page.html", "baseline.png"]
tools = {{ ui = 'python3 "$GATES_DIR/run/ui-gates.py" stamp' }}
run = 'python3 "$GATES_DIR/run/ui-gates.py" pixel page.html baseline.png'
{shadow}"""
IDS = ("ui-axe", "ui-pixel")


def shadow(since):
    """(run's exit, fold's exit, fold's output) for a change both rows refuse, under gate.py."""
    repo = fixture("pixel red", "linux")
    axe = fixture("axe red")
    # One stub node for both rows: the axe report for axe, the moved screenshot otherwise.
    node(
        repo,
        f'case "$3" in axe) /bin/cat "{axe}/report.json" ;; *) for out; do :; done; '
        f'/bin/cp "{repo}/shot.png" "$out"; echo \'{{{VERSIONS}}}\' ;; esac',
    )
    (repo / ".gitignore").write_text("bin/\nmodules/\nshot.png\nreport.json\n")
    registry = ROWS.format(shadow=f"shadow_since = {since}\n" if since else "")
    (repo / ".github").mkdir()
    (repo / ".github/gates.toml").write_text(registry)
    # The stub uname must win over /usr/bin's.
    env = {
        **ENV,
        "GATES_DIR": str(ROOT),
        "PATH": f"{repo / 'bin'}:{Path(sys.executable).parent}:/usr/bin:/bin",
        MODULES_ENV: str(repo / "modules"),
    }
    run("git", "init", "-q", "--template=", "-b", "main", cwd=repo)
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-qm", "base", cwd=repo)
    run("git", "tag", "base", cwd=repo)
    (repo / "page.html").write_text("<!doctype html><title>changed</title>\n")
    run("git", "commit", "-qam", "change", cwd=repo)
    store = repo.parent / f"{repo.name}-store"
    args = ("--base", "base", "--store", str(store))
    run_rc, _ = run(sys.executable, str(RUNNER), "run", *args, cwd=repo, env=env)
    fold_rc, out = run(sys.executable, str(RUNNER), "fold", *args, cwd=repo, env=env)
    return run_rc, fold_rc, out


def in_shadow():
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    run_rc, fold_rc, out = shadow(today)
    window = f"(in shadow since {today}: 1 of 50 runs, 0 of 14 days)"
    missing = [i for i in IDS if f"shadow {i}: fail: exit 1 {window}" not in out]
    if run_rc or fold_rc or missing:
        return f"shadow rows: wanted run and fold to exit 0 with a shadow line for {missing}, got {run_rc}, {fold_rc}:\n{out}"
    return None


def established_blocks():
    """The same rows with no shadow window block, so the window is what holds them back."""
    run_rc, fold_rc, out = shadow(None)
    missing = [i for i in IDS if f"FAIL {i}: fail: exit 1" not in out]
    if run_rc != 1 or fold_rc != 1 or missing:
        return f"established rows: wanted run and fold to refuse for {missing}, got {run_rc}, {fold_rc}:\n{out}"
    return None


# --real: the same questions put to the pinned browser.
NO_ALT = '<!doctype html><html lang="en"><title>t</title><main><h1>Hi</h1><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></main></html>\n'
CSP = '<meta http-equiv="Content-Security-Policy" content="script-src \'none\'">'


def real_cases(seen):
    if not os.environ.get(MODULES_ENV):
        return [f'--real needs {MODULES_ENV} (README.md, "The UI rows")']
    pages = fresh_dir()
    (pages / "red.html").write_text(NO_ALT.replace("<title>", CSP + "<title>"))
    (pages / "clean.html").write_text(NO_ALT.replace("<img ", '<img alt="a dot" '))
    (pages / "moved.html").write_text(
        NO_ALT.replace("<img ", '<img alt="a dot" ').replace("Hi", "Hello there")
    )
    env = dict(ENV, **{MODULES_ENV: os.environ[MODULES_ENV]})
    linux = platform.system() == "Linux"
    if not linux:
        stub = fresh_dir()
        (stub / "uname").write_text("#!/bin/sh\necho Linux\n")
        (stub / "uname").chmod(0o755)
        env["PATH"] = f"{stub}:{env['PATH']}"
        print(
            f"--real: this is {platform.system()}, so the pixel cases run with uname stubbed to Linux"
        )

    def timed(name, *args):
        start = time.monotonic()
        result = run(sys.executable, str(GATE), *args, cwd=pages, env=env)
        seen.append((name, time.monotonic() - start))
        return result

    base = pages / "base/clean.png"
    return [
        expect(
            "real axe: an img without alt, under script-src 'none'",
            timed("axe red", "axe", "red.html"),
            1,
            "red.html: image-alt (critical, 1 nodes)",
        ),
        expect(
            "real axe: the same img with alt",
            timed("axe clean", "axe", "clean.html"),
            0,
            "no serious or critical violation",
        ),
        expect(
            "real record",
            timed("record", "record", "clean.html", str(base)),
            0,
            "recorded",
        ),
        expect(
            "real pixel: the page against its own baseline",
            timed("pixel clean", "pixel", "clean.html", str(base)),
            0,
            "matches",
        ),
        expect(
            "real pixel: the heading moved",
            timed("pixel red", "pixel", "moved.html", str(base)),
            1,
            "pixels differ from",
            "diff.png",
        ),
    ]


def main():
    real = "--real" in sys.argv[1:]
    seen = []
    if real:
        results = real_cases(seen)
    else:
        results = axe_cases() + driver_cases() + pixel_cases() + decode_cases()
        results += [f() for f in (in_shadow, established_blocks)]
    failures = [r for r in results if r]
    total = len(results)
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    for name, seconds in seen:
        print(f"  {seconds:5.2f} s  {name}")
    label = "real" if real else "stub"
    print(f"test-ui-gates ({label}): {total - len(failures)} of {total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
