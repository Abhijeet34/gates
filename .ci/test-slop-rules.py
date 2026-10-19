#!/usr/bin/env python3
"""Regression tests for run/slop-rules.py, and its scoped shadow rows under run/gate.py.

    .ci/test-slop-rules.py

Each case writes a small tree, runs one row over it (or over what a commit added)
and requires its exit status and, for a finding, a line of its output. The near
misses matter as much as the hits: a rule that fires on a fixture file a test
writes, on a flag of another tool or on a ratio that is exactly at the floor
would cost more false alarms than it prevents. Strings the exposure gate refuses
in tracked text, such as an em dash, are written as escapes.
"""

import datetime
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/slop-rules.py"
RUNNER = ROOT / "run/gate.py"
ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@invalid",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@invalid",
    "PYTHONDONTWRITEBYTECODE": "1",
}
workdir = tempfile.mkdtemp()
DASH = "\u2014"


def run(*args, cwd, env=ENV):
    proc = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def put(here, files):
    for name, text in files.items():
        path = here / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def tree(files):
    here = Path(tempfile.mkdtemp(dir=workdir))
    put(here, files)
    return here


def repo(base_files, change_files):
    """A repository with `base_files` committed and tagged `base`, then `change_files` on top."""
    here = Path(tempfile.mkdtemp(dir=workdir))
    run("git", "init", "-q", "--template=", "-b", "main", cwd=here)
    put(here, base_files)
    run("git", "add", "-A", cwd=here)
    run("git", "commit", "-qm", "base", cwd=here)
    run("git", "tag", "base", cwd=here)
    put(here, change_files)
    run("git", "add", "-A", cwd=here)
    run("git", "commit", "-qm", "change", cwd=here)
    return here


def sweep(name, row, files, want, finding=None):
    here = tree(files)
    rc, out = run(sys.executable, str(GATE), row, str(here), cwd=here)
    return judge(name, rc, out, want, finding)


def since(name, row, base_files, change_files, want, finding=None):
    here = repo(base_files, change_files)
    rc, out = run(sys.executable, str(GATE), row, "--base", "base", str(here), cwd=here)
    return judge(name, rc, out, want, finding)


def since_in_subdir(name, row, sub, change_files, want, finding=None):
    here = repo(
        {"README.md": "x\n"}, {f"{sub}/{k}": v for k, v in change_files.items()}
    )
    rc, out = run(
        sys.executable, str(GATE), row, "--base", "base", str(here / sub), cwd=here
    )
    return judge(name, rc, out, want, finding)


def judge(name, rc, out, want, finding):
    if rc != want:
        return f"{name}: exit {rc}, wanted {want}:\n{out}"
    if finding and finding not in out:
        return f"{name}: output does not carry {finding!r}:\n{out}"
    if want == 0 and "slop-rules: " + "refused" in out:
        return f"{name}: refused instead of passing:\n{out}"
    return None


TEST_PY = (
    "from pathlib import Path\n"
    "import re\n"
    "SCRIPT = Path(__file__).parent / 'run/gate.sh'\n"
    "{body}"
)
SWEEPS = [
    # --- tests ---
    (
        "python: an assertion over the text of a source file",
        "tests",
        {
            "test_gate.py": TEST_PY.format(
                body="text = SCRIPT.read_text()\nassert 'set -e' in text\n"
            )
        },
        1,
        "test_gate.py:5: asserts on the text of run/gate.sh",
    ),
    (
        "python: the read inside the assertion",
        "tests",
        {
            "test_gate.py": TEST_PY.format(
                body="assert 'x' in (Path('.') / 'src/app.py').read_text()\n"
            )
        },
        1,
        "test_gate.py:4: asserts on the text of src/app.py",
    ),
    (
        "python: an assertion over a result derived from the text",
        "tests",
        {
            "test_gate.py": TEST_PY.format(
                body="text = SCRIPT.read_text()\nhit = re.search('x', text)\nassert hit\n"
            )
        },
        1,
        "test_gate.py:6: asserts on the text of run/gate.sh",
    ),
    (
        "python: a file handle opened on a source file",
        "tests",
        {
            "test_gate.py": TEST_PY.format(
                body="with open('src/app.py') as f:\n    text = f.read()\nassert 'x' in text\n"
            )
        },
        1,
        "test_gate.py:6: asserts on the text of src/app.py",
    ),
    (
        "python: a unittest assertion method",
        "tests",
        {
            "test_gate.py": TEST_PY.format(
                body="def check(self):\n    self.assertIn('x', SCRIPT.read_text())\n"
            )
        },
        1,
        "test_gate.py:5: asserts on the text of run/gate.sh",
    ),
    (
        "python: the text of a JSON fixture and of Markdown is not source",
        "tests",
        {
            "test_gate.py": TEST_PY.format(
                body="assert 'x' in Path('fixtures/out.json').read_text()\n"
                "assert 'x' in Path('README.md').read_text()\n"
            )
        },
        0,
        None,
    ),
    (
        "python: another test file's text is not source",
        "tests",
        {
            "test_gate.py": TEST_PY.format(
                body="assert 'x' in Path('test_other.py').read_text()\n"
            )
        },
        0,
        None,
    ),
    (
        "python: a source file that is only run, with the output asserted",
        "tests",
        {
            "test_gate.py": TEST_PY.format(
                body="import subprocess\nproc = subprocess.run(['sh', 'run/gate.sh'], capture_output=True, text=True)\n"
                "assert 'done' in proc.stdout\n"
            )
        },
        0,
        None,
    ),
    (
        "python: a source file read and never asserted on",
        "tests",
        {
            "test_gate.py": TEST_PY.format(
                body="text = SCRIPT.read_text()\nprint(text)\n"
            )
        },
        0,
        None,
    ),
    (
        "python: a script that is not a test may read what it likes",
        "tests",
        {"tool.py": TEST_PY.format(body="assert 'x' in SCRIPT.read_text()\n")},
        0,
        None,
    ),
    (
        "shell: a grep over a source file",
        "tests",
        {"test-gate.sh": "grep -q 'set -e' \"$ROOT/run/gate.sh\"\n"},
        1,
        "test-gate.sh:1: asserts on the text of $ROOT/run/gate.sh",
    ),
    (
        "shell: a cat piped into grep",
        "tests",
        {"test-gate.sh": "cat run/gate.sh | grep -q 'set -e'\n"},
        1,
        "test-gate.sh:1: asserts on the text of run/gate.sh",
    ),
    (
        "shell: a file the test writes is a fixture",
        "tests",
        {"test-gate.sh": "printf 'x\\n' > cmd.sh\ngrep -q x cmd.sh\n"},
        0,
        None,
    ),
    (
        "shell: grep over output has no file operand",
        "tests",
        {"test-gate.sh": 'out=$(bash run/gate.sh)\necho "$out" | grep -q done\n'},
        0,
        None,
    ),
    (
        "javascript: a source file read in a test that asserts",
        "tests",
        {
            "app.test.js": "const src = fs.readFileSync('src/app.js', 'utf8');\n"
            "expect(src).toContain('x');\n"
        },
        1,
        "app.test.js:1: asserts on the text of src/app.js",
    ),
    (
        "javascript: a fixture read in a test that asserts",
        "tests",
        {
            "app.test.js": "const src = fs.readFileSync('fixtures/in.json', 'utf8');\n"
            "expect(src).toContain('x');\n"
        },
        0,
        None,
    ),
    # --- symbols ---
    (
        "symbols: a path of a directory of the tree that is not there",
        "symbols",
        {"run/new.py": "x = 1\n", "README.md": "Run `run/old.py` first.\n"},
        1,
        "README.md:1: `run/old.py` names a path that is not in the tree",
    ),
    (
        "symbols: a path that is there",
        "symbols",
        {"run/new.py": "x = 1\n", "README.md": "Run `run/new.py` first.\n"},
        0,
        None,
    ),
    (
        "symbols: absent here waives a path",
        "symbols",
        {
            "run/new.py": "x = 1\n",
            "README.md": "Run `run/old.py` (absent here) first.\n",
        },
        0,
        None,
    ),
    (
        "symbols: a fenced block is not a claim",
        "symbols",
        {"run/new.py": "x = 1\n", "README.md": "```\n`run/old.py`\n```\n"},
        0,
        None,
    ),
    (
        "symbols: a bare name, another repository's directory and a bare URL are not judged",
        "symbols",
        {
            "run/new.py": "x = 1\n",
            "README.md": "See `old.py`, `internal/git/git.go` and `github.com/owner/repo/x`.\n",
        },
        0,
        None,
    ),
    (
        "symbols: a path a script spells out is a path the tree names",
        "symbols",
        {
            ".github/ci.yml": "on: push\n",
            "run/lock.py": "CONFIG = '.github/gates.toml'\n",
            "README.md": "The caller's `.github/gates.toml` is read.\n",
        },
        0,
        None,
    ),
    (
        "symbols: a function nothing defines",
        "symbols",
        {"run/a.py": "x = 1\n", "README.md": "Call `gate_run()` here.\n"},
        1,
        "README.md:1: `gate_run()` names a function that no file of the tree has",
    ),
    (
        "symbols: a function the code defines",
        "symbols",
        {
            "run/a.py": "def gate_run():\n    pass\n",
            "README.md": "Call `gate_run()` here.\n",
        },
        0,
        None,
    ),
    (
        "symbols: a second doc naming it does not define it",
        "symbols",
        {"a.md": "Call `gate_run()` here.\n", "b.md": "Call `gate_run()` there.\n"},
        1,
        "a.md:1: `gate_run()`",
    ),
    (
        "symbols: a builtin needs no definition",
        "symbols",
        {"README.md": "It calls `print()`.\n"},
        0,
        None,
    ),
    (
        "symbols: a flag nothing defines",
        "symbols",
        {"run/a.py": "x = 1\n", "README.md": "Pass `--bogus`.\n"},
        1,
        "README.md:1: `--bogus` names a flag that no file of the tree has",
    ),
    (
        "symbols: a flag the code defines",
        "symbols",
        {
            "run/a.py": "ap.add_argument('--base')\n",
            "README.md": "Pass `--base=main`.\n",
        },
        0,
        None,
    ),
    (
        "symbols: a comment in a script",
        "symbols",
        {"run/a.sh": "# Calls `gate_run()` below.\ntrue\n"},
        1,
        "run/a.sh:1: `gate_run()`",
    ),
    (
        "symbols: a Python comment and a docstring",
        "symbols",
        {"run/a.py": '"""Names `module_one()`."""\n# and `module_two()`\nx = 1\n'},
        1,
        "run/a.py:1: `module_one()`",
    ),
    (
        "symbols: a backtick in a string of code is not prose",
        "symbols",
        {"run/a.py": "x = '`gate_run()`'\n"},
        0,
        None,
    ),
    # --- easing ---
    (
        "easing: an overshoot curve",
        "easing",
        {"a.tsx": "const t = 'transform 300ms cubic-bezier(.34,1.56,.64,1)';\n"},
        1,
        "a.tsx:1: cubic-bezier(.34,1.56,.64,1) overshoots 0..1",
    ),
    (
        "easing: four overshoot curves in the shape of a component library's are all refused",
        "easing",
        {
            "edit-sheet.tsx": "const a = 'all 400ms cubic-bezier(.34,1.56,.64,1)';\n",
            "card.tsx": "const b = 'all 400ms cubic-bezier(.68,-.55,.27,1.55)';\n",
            "insights-view.tsx": "const c = 'all 400ms cubic-bezier(.175,.885,.32,1.275)';\n",
            "tour-overlay.tsx": "const d = 'all 400ms cubic-bezier(.5,1.8,.5,1)';\n",
        },
        1,
        "4 finding(s)",
    ),
    (
        "easing: an anticipation curve below zero",
        "easing",
        {"a.css": ".a { transition: all 1s cubic-bezier(.6, -0.28, .74, .05); }\n"},
        1,
        "a.css:1: cubic-bezier(.6, -0.28, .74, .05)",
    ),
    (
        "easing: an exponential ease-out",
        "easing",
        {"a.css": ".a { transition: all 1s cubic-bezier(0.16, 1, 0.3, 1); }\n"},
        0,
        None,
    ),
    (
        "easing: named back, bounce and elastic eases and the Tailwind utility",
        "easing",
        {
            "a.css": ".a { transition-timing-function: ease-out-back; }\n",
            "b.ts": "const e = easeOutBounce;\n",
            "c.tsx": "const c = 'animate-bounce';\n",
        },
        1,
        "3 finding(s)",
    ),
    (
        "easing: a linear() stop past one is an overshoot, a plain one is not",
        "easing",
        {
            "a.css": ".a { transition-timing-function: linear(0, 1.2, 1); }\n.b { transition-timing-function: linear(0, 0.5, 1); }\n"
        },
        1,
        "1 finding(s)",
    ),
    # --- contrast ---
    (
        "contrast: grey on grey",
        "contrast",
        {"a.css": ".a {\n  color: #888888;\n  background-color: #999;\n}\n"},
        1,
        "a.css:2: color #888888 on #999 is 1.2:1, under 4.5:1",
    ),
    (
        "contrast: black on white",
        "contrast",
        {"a.css": ".a { color: #000; background: #fff; }\n"},
        0,
        None,
    ),
    (
        "contrast: 4.54:1 passes and 4.48:1 does not",
        "contrast",
        {
            "a.css": ".a { color: #767676; background: #fff; }\n.b { color: #777777; background: #fff; }\n"
        },
        1,
        "1 finding(s)",
    ),
    (
        "contrast: large text needs 3:1",
        "contrast",
        {"a.css": ".a { color: #888; background: #fff; font-size: 2rem; }\n"},
        0,
        None,
    ),
    (
        "contrast: the same pair as body text does not pass",
        "contrast",
        {"a.css": ".a { color: #888; background: #fff; font-size: 1rem; }\n"},
        1,
        "color #888 on #fff is 3.5:1, under 4.5:1",
    ),
    (
        "contrast: a disabled control is exempt",
        "contrast",
        {"a.css": "button:disabled { color: #888; background: #999; }\n"},
        0,
        None,
    ),
    (
        "contrast: oklch pairs are read",
        "contrast",
        {
            "a.css": ".low { color: oklch(0.6 0.1 250); background: oklch(0.65 0.1 250); }\n"
            ".ok { color: oklch(0.2 0.02 250); background: oklch(98% 0 0); }\n"
        },
        1,
        "color oklch(0.6 0.1 250) on oklch(0.65 0.1 250)",
    ),
    (
        "contrast: a translucent color and a variable are undecidable, not refused",
        "contrast",
        {
            "a.css": ".a { color: rgba(0,0,0,.5); background: #fff; }\n.b { color: var(--fg); background: #fff; }\n"
        },
        0,
        None,
    ),
    # --- z-index ---
    (
        "z-index: 9999, 999, a Tailwind arbitrary value and a script property",
        "z-index",
        {
            "a.css": ".a { z-index: 9999; }\n.b { z-index: 999 !important; }\n",
            "b.tsx": "const s = { zIndex: 99999 }; const c = 'z-[9999]';\n",
        },
        1,
        "4 finding(s)",
    ),
    (
        "z-index: a semantic scale and a small number",
        "z-index",
        {"a.css": ".a { z-index: var(--z-modal); }\n.b { z-index: 10; }\n"},
        0,
        None,
    ),
    # --- em dash ---
    (
        "em-dash: a UI file",
        "em-dash",
        {"a.tsx": f"const t = 'Saved {DASH} done';\n"},
        1,
        "a.tsx:1: carries an em dash",
    ),
    (
        "em-dash: a forked file is exempt",
        "em-dash",
        {"a.html": f"<!-- LOCAL FORK of upstream -->\n<p>a {DASH} b</p>\n"},
        0,
        None,
    ),
    (
        "em-dash: Markdown belongs to the documentation row",
        "em-dash",
        {"README.md": f"a {DASH} b\n"},
        0,
        None,
    ),
    # --- reduced-motion ---
    (
        "reduced-motion: motion and no file honors it",
        "reduced-motion",
        {"a.css": ".a { transition: opacity 200ms; }\n"},
        1,
        "a.css:1: adds motion and no file of the tree names prefers-reduced-motion",
    ),
    (
        "reduced-motion: another file honors it",
        "reduced-motion",
        {
            "a.css": ".a { transition: opacity 200ms; }\n",
            "b.css": "@media (prefers-reduced-motion: reduce) { * { transition: none; } }\n",
        },
        0,
        None,
    ),
    (
        "reduced-motion: the Tailwind variant honors it",
        "reduced-motion",
        {
            "a.tsx": "const c = 'transition-opacity motion-reduce:transition-none';\n",
            "b.css": ".a { animation: spin 1s; }\n",
        },
        0,
        None,
    ),
    (
        "reduced-motion: no motion, or motion switched off",
        "reduced-motion",
        {"a.css": ".a { transition: none; }\n.b { color: red; }\n"},
        0,
        None,
    ),
]

BAD_PY = TEST_PY.format(body="assert 'set -e' in SCRIPT.read_text()\n")
SINCE = [
    (
        "an old source assertion is not a new one",
        "tests",
        {"test_gate.py": BAD_PY},
        {"test_gate.py": BAD_PY + "x = 1\n"},
        0,
        None,
    ),
    (
        "a new source assertion in a changed test file",
        "tests",
        {"test_gate.py": TEST_PY.format(body="x = 1\n")},
        {"test_gate.py": BAD_PY},
        1,
        "test_gate.py:4: asserts on the text of run/gate.sh",
    ),
    (
        "a stale name already on the base is not this change's",
        "symbols",
        {"README.md": "Old `run/gone.py` line.\n", "run/a.py": "x = 1\n"},
        {"README.md": "Old `run/gone.py` line.\nNew line.\n"},
        0,
        None,
    ),
    (
        "a stale name the change adds",
        "symbols",
        {"README.md": "Old line.\n", "run/a.py": "x = 1\n"},
        {"README.md": "Old line.\nNew `run/gone.py` line.\n"},
        1,
        "README.md:2: `run/gone.py`",
    ),
    (
        "an old overshoot is not a new one",
        "easing",
        {"a.css": ".a { transition: 1s cubic-bezier(.34,1.56,.64,1); }\n"},
        {
            "a.css": ".a { transition: 1s cubic-bezier(.34,1.56,.64,1); }\n.b { color: red; }\n"
        },
        0,
        None,
    ),
    (
        "a new overshoot",
        "easing",
        {"a.css": ".b { color: red; }\n"},
        {
            "a.css": ".b { color: red; }\n.a { transition: 1s cubic-bezier(.34,1.56,.64,1); }\n"
        },
        1,
        "a.css:2:",
    ),
    (
        "an old em dash is not a new one",
        "em-dash",
        {"a.tsx": f"const t = 'a {DASH} b';\n"},
        {"a.tsx": f"const t = 'a {DASH} b';\nconst u = 'c';\n"},
        0,
        None,
    ),
]

ROWS = """
[[gate]]
id = "{row}"
inputs = {inputs}
scoped = true
tools = {{ base = 'git merge-base "$BASE_SHA" HEAD' }}
run = 'python3 "$GATES_DIR/run/slop-rules.py" {row} --base "$BASE_SHA"'
shadow_since = {since}
"""
# What each row judges: the globs a caller would give it as `inputs`.
INPUTS = {
    "tests": '["**/test_*.py", "**/test-*.sh", "**/*.test.*"]',
    "symbols": '["**/*.md"]',
    "easing": '["**/*.css"]',
    "contrast": '["**/*.css"]',
    "z-index": '["**/*.css"]',
    "em-dash": '["**/*.css"]',
    "reduced-motion": '["**/*.css"]',
}
RED = {
    "test_gate.py": BAD_PY,
    "README.md": "A stale `run/gone.py` line.\n",
    "a.css": (
        ".a { transition: 1s cubic-bezier(.34,1.56,.64,1); color: #888; background: #999; z-index: 9999; }\n"
        f"/* a {DASH} b */\n"
    ),
}


def under_the_runner():
    """Scoped rows stay out of an unrelated change, and a red one is shadow, not a block."""
    date = datetime.datetime.now(datetime.UTC).date().isoformat()
    registry = "\n".join(
        ROWS.format(row=r, inputs=i, since=date) for r, i in INPUTS.items()
    )
    base = {
        ".github/gates.toml": registry,
        "run/a.py": "x = 1\n",
        "assets/logo.txt": "a\n",
    }
    env = {**ENV, "GATES_DIR": str(ROOT), "BASE_SHA": "base"}
    red = repo(base, RED)
    store = Path(tempfile.mkdtemp(dir=workdir)) / "store"
    args = ("--base", "base", "--store", str(store))
    run_rc, _ = run(sys.executable, str(RUNNER), "run", *args, cwd=red, env=env)
    fold_rc, out = run(sys.executable, str(RUNNER), "fold", *args, cwd=red, env=env)
    wanted = [f"shadow {gid}: fail: exit 1 (in shadow since" for gid in INPUTS]
    if run_rc != 0 or fold_rc != 0 or any(w not in out for w in wanted):
        return f"red rows in their shadow window: run {run_rc}, fold {fold_rc}:\n{out}"
    other = repo(base, {"assets/logo.txt": "b\n"})
    chosen = run(
        sys.executable, str(RUNNER), "select", "--base", "base", cwd=other, env=env
    )[1]
    leaked = [gid for gid in INPUTS if f"{gid}\t" in chosen]
    if leaked:
        return f"a change to assets/logo.txt selected scoped rows {leaked}:\n{chosen}"
    return None


def main():
    failures = [sweep(*case) for case in SWEEPS]
    failures += [since(*case) for case in SINCE]
    failures.append(
        since_in_subdir(
            "a subdirectory root still judges what the diff added",
            "z-index",
            "web",
            {"a.css": ".a { z-index: 9999; }\n"},
            1,
            "z-index",
        )
    )
    failures.append(under_the_runner())
    failures = [f for f in failures if f]
    total = len(SWEEPS) + len(SINCE) + 2
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    print(f"test-slop-rules: {total - len(failures)} of {total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
