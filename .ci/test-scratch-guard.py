#!/usr/bin/env python3
"""Regression tests for run/scratch-guard.py, and its row in shadow under run/gate.py.

    .ci/test-scratch-guard.py

Each case runs the guard over a command in a fresh tree whose system temporary
directory is a private one, so another process writing to /tmp cannot move the
result. A command is a shell snippet; `$LEAK` names that private temporary
directory, which is where a run that ignores TMPDIR would write.
"""

import datetime
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/scratch-guard.py"
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


def run(*args, cwd, env=ENV):
    proc = subprocess.run(
        args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    return proc.returncode, proc.stdout + proc.stderr


def fresh():
    """(a tree with one tracked-looking file, a private system temporary directory)."""
    top = Path(tempfile.mkdtemp(dir=workdir))
    (top / "tree").mkdir()
    (top / "tmp").mkdir()
    (top / "tree/keep.txt").write_text("keep\n")
    return top / "tree", top / "tmp"


# (name, snippet, guard options, want exit, a line the output must carry)
CASES = [
    ("a run that writes only to its TMPDIR", 'echo x > "$TMPDIR/scratch"', [], 0, None),
    (
        "a file left in the tree",
        "echo x > leak.txt",
        [],
        1,
        "left behind: leak.txt (tree)",
    ),
    (
        "a file left in a new directory",
        "mkdir -p a/b && echo x > a/b/f",
        [],
        1,
        "left behind: a/b/f (tree)",
    ),
    (
        "a file left in an existing directory",
        "echo x > tree-new.txt",
        [],
        1,
        "left behind: tree-new.txt (tree)",
    ),
    (
        "an entry left in the system temporary directory",
        'echo x > "$LEAK/stray"',
        [],
        1,
        "left behind: stray (temp)",
    ),
    (
        "a directory left in the system temporary directory",
        'mkdir "$LEAK/dir.abc"',
        [],
        1,
        "left behind: dir.abc (temp)",
    ),
    (
        "--tree-only ignores the system temporary directory",
        'echo x > "$LEAK/stray"',
        ["--tree-only"],
        0,
        None,
    ),
    (
        "--tree-only still reads the tree",
        "echo x > leak.txt",
        ["--tree-only"],
        1,
        "left behind: leak.txt (tree)",
    ),
    (
        "caches and build output are not scratch",
        "mkdir -p __pycache__ node_modules target && echo x | tee __pycache__/a node_modules/b target/c >/dev/null",
        [],
        0,
        None,
    ),
    (
        "changing or removing a file is not a new path",
        "echo more >> keep.txt && rm keep.txt",
        [],
        0,
        None,
    ),
    ("the command's own failure passes through", "exit 3", [], 3, None),
    (
        "a leak is reported when the command also fails",
        "echo x > leak.txt; exit 3",
        [],
        1,
        "left behind: leak.txt",
    ),
    ("a command that is not found", None, [], 2, "could not run"),
]


def expect(name, snippet, options, want, finding):
    tree, tmp = fresh()
    env = {**ENV, "TMPDIR": str(tmp), "LEAK": str(tmp)}
    command = ["sh", "-c", snippet] if snippet else ["/nonexistent/command"]
    rc, out = run(
        sys.executable, str(GATE), *options, "--", *command, cwd=tree, env=env
    )
    if rc != want:
        return f"{name}: exit {rc}, wanted {want}:\n{out}"
    if finding and finding not in out:
        return f"{name}: output does not carry {finding!r}:\n{out}"
    left = sorted(p.name for p in tmp.glob("scratch-guard.*"))
    if left:
        return f"{name}: the guard's own directory was left under the temporary directory: {left}"
    return None


def own_directory_is_used_and_removed():
    tree, tmp = fresh()
    env = {**ENV, "TMPDIR": str(tmp)}
    snippet = 'case "$TMPDIR" in "$LEAK"/scratch-guard.*) echo inside ;; *) echo outside ;; esac'
    rc, out = run(
        sys.executable,
        str(GATE),
        "--",
        "sh",
        "-c",
        snippet,
        cwd=tree,
        env={**env, "LEAK": str(tmp.resolve())},
    )
    if rc != 0 or "inside" not in out:
        return f"the command's TMPDIR was not a directory made under the system temporary one (exit {rc}): {out}"
    return None


def temp_inside_the_tree():
    """A fixture's TMPDIR under the tree must not read as the command leaving files in the tree."""
    tree, _ = fresh()
    inner = tree / ".tmp"
    inner.mkdir()
    env = {**ENV, "TMPDIR": str(inner)}
    rc, out = run(
        sys.executable,
        str(GATE),
        "--",
        "sh",
        "-c",
        'echo x > "$TMPDIR/scratch"',
        cwd=tree,
        env=env,
    )
    return (
        None
        if rc == 0
        else f"a TMPDIR inside the tree counted as a leak (exit {rc}): {out}"
    )


ROW = """
[[gate]]
id = "scratch"
inputs = ["**"]
run = 'python3 "$GATES_DIR/run/scratch-guard.py" --tree-only -- sh -c "echo x > .leak"'
shadow_since = {since}
"""


def under_the_runner():
    root = Path(tempfile.mkdtemp(dir=workdir))
    repo, store = root / "repo", root / "store"
    (repo / ".github").mkdir(parents=True)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    (repo / ".github/gates.toml").write_text(ROW.format(since=today))
    (repo / "a.txt").write_text("one\n")
    env = {**ENV, "GATES_DIR": str(ROOT)}
    run("git", "init", "-q", "--template=", "-b", "main", cwd=repo)
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-qm", "base", cwd=repo)
    run("git", "tag", "base", cwd=repo)
    (repo / "a.txt").write_text("two\n")
    run("git", "commit", "-qam", "change", cwd=repo)
    args = ("--base", "base", "--store", str(store))
    run_rc, _ = run(sys.executable, str(RUNNER), "run", *args, cwd=repo, env=env)
    fold_rc, out = run(sys.executable, str(RUNNER), "fold", *args, cwd=repo, env=env)
    if run_rc or fold_rc or "shadow scratch: fail: exit 1 (in shadow since" not in out:
        return (
            f"a leaking row in its shadow window: run {run_rc}, fold {fold_rc}:\n{out}"
        )
    return None


def main():
    failures = [expect(*case) for case in CASES]
    failures += [
        own_directory_is_used_and_removed(),
        temp_inside_the_tree(),
        under_the_runner(),
    ]
    failures = [f for f in failures if f]
    total = len(CASES) + 3
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    print(f"test-scratch-guard: {total - len(failures)} of {total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
