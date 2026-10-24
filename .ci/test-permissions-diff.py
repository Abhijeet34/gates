#!/usr/bin/env python3
"""Regression tests for run/permissions-diff.py, and its row in shadow under run/gate.py.

    .ci/test-permissions-diff.py

Each case commits a base and one change into a fixture repository, runs the gate
on base...HEAD and requires its exit status and a line of its output. The red
cases include the five red fixtures of security design 7.2.
"""

import datetime
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/permissions-diff.py"
RUNNER = ROOT / "run/gate.py"
REFUSED = "an automatic change may not land them: size the change L and open a class-2 decision"

SETTINGS = (
    '{"sandbox": {"enabled": true, "network": {"allowedDomains": ["github.com"]}}}\n'
)
ROUTE = """[thresholds]
overflow_at = 0.85

[tiers]
A = ["anthropic", "openai-api"]
B = ["anthropic"]
C = ["anthropic"]

[repos]
gates = "A"

[vendors]
anthropic = { admitted = true }
"""
GATES_TOML = """[[gate]]
id = "secrets.range"
inputs = ["**"]
run = "true"

[[gate]]
id = "rust"
inputs = ["src/**"]
run = "cargo test"
"""
PROOF_REGISTRY = """[[gate]]
id = "secret-scan"
surface = "Security"
red = "echo red"

[[gate]]
id = "lint"
surface = "PR checks"
red = "echo red"
"""
WORKFLOW = """name: ci
on: [pull_request]
permissions:
  contents: read # the checkout only
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""
BASE = {
    "README.md": "A fixture.\n",
    ".claude/settings.json": SETTINGS,
    "system-maintenance/claude-code/config/settings.json": SETTINGS,
    ".vscode/settings.json": '{"editor.tabSize": 4}\n',
    "internal/permit/permit.go": 'package permit\n\nvar deny = []string{"Write(~/.local/bin/**)"}\n',
    "route.toml": ROUTE,
    ".github/gates.toml": GATES_TOML,
    ".ci/gate-registry.toml": PROOF_REGISTRY,
    ".github/workflows/ci.yml": WORKFLOW,
}

workdir = Path(tempfile.mkdtemp())
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


def run(*args, cwd, env=ENV):
    proc = subprocess.run(
        args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    return proc.returncode, proc.stdout + proc.stderr


def fixture(change, base=BASE):
    """A repository whose `base` tag holds `base` and whose HEAD applies `change`."""
    repo = Path(tempfile.mkdtemp(dir=workdir))
    run("git", "init", "-q", "--template=", "-b", "main", cwd=repo)
    for files, tag in ((base, "base"), (change, None)):
        for rel, text in files.items():
            path = repo / rel
            if text is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        run("git", "add", "-A", cwd=repo)
        run("git", "commit", "-qm", tag or "change", "--allow-empty", cwd=repo)
        if tag:
            run("git", "tag", tag, cwd=repo)
    return repo


def gate(change, base_ref="base", base=BASE):
    repo = fixture(change, base)
    return run(sys.executable, str(GATE), "--base", base_ref, cwd=repo)


def expect(name, change, rc, line, base_ref="base", base=BASE):
    got_rc, out = gate(change, base_ref, base)
    if got_rc != rc or line not in out:
        return f"{name}: wanted exit {rc} naming {line!r}, got exit {got_rc}:\n{out}"
    if rc == 1 and REFUSED not in out:
        return f"{name}: refused without the decision line:\n{out}"
    return None


CASES = [
    # Security design 7.2's five red fixtures.
    (
        "allowUnsandboxedCommands in a profile",
        {
            ".claude/settings.json": '{"sandbox": {"enabled": true, "allowUnsandboxedCommands": true}}\n'
        },
        1,
        "permissions-diff: .claude/settings.json changes Claude Code settings",
    ),
    (
        "a wildcard in allowedDomains",
        {
            "system-maintenance/claude-code/config/settings.json": SETTINGS.replace(
                '"github.com"', '"*"'
            )
        },
        1,
        "permissions-diff: system-maintenance/claude-code/config/settings.json changes the deployed Claude Code settings",
    ),
    (
        "a permit rule granting Write on ~/.local/bin",
        {"internal/permit/permit.go": "package permit\n\nvar deny = []string{}\n"},
        1,
        "permissions-diff: internal/permit/permit.go changes the fac permit deny overlay",
    ),
    (
        "blocks = false on secrets.range",
        {
            ".github/gates.toml": GATES_TOML.replace(
                'run = "true"', 'run = "true"\nblocks = false'
            )
        },
        1,
        "permissions-diff: .github/gates.toml changes the gate registry: row 'secrets.range' changed (blocks)",
    ),
    (
        "a vendor added to tier C",
        {
            "route.toml": ROUTE.replace(
                'C = ["anthropic"]', 'C = ["anthropic", "openai-api"]'
            )
        },
        1,
        "permissions-diff: route.toml changes the routing table: [tiers]",
    ),
    # The rest of the surface, and what is read only in part.
    (
        "a repository moved to a wider tier",
        {"route.toml": ROUTE.replace('gates = "A"', 'gates = "C"')},
        1,
        "route.toml changes the routing table: [repos]",
    ),
    (
        "a routing threshold, outside the protected tables",
        {"route.toml": ROUTE.replace("0.85", "0.9")},
        0,
        "none on the protected surface",
    ),
    (
        "an unreadable routing table",
        {"route.toml": ROUTE + "[tiers\n"},
        1,
        "route.toml changes the routing table: route.toml cannot be read as TOML",
    ),
    (
        "a workflow token widened",
        {
            ".github/workflows/ci.yml": WORKFLOW.replace(
                "contents: read", "contents: write"
            )
        },
        1,
        "permissions-diff: .github/workflows/ci.yml changes a workflow token's permissions: a permissions: block changed",
    ),
    (
        "a workflow's permissions block removed",
        {
            ".github/workflows/ci.yml": WORKFLOW.replace(
                "permissions:\n  contents: read # the checkout only\n", ""
            )
        },
        1,
        ".github/workflows/ci.yml changes a workflow token's permissions",
    ),
    (
        "a workflow step and a permissions comment edited",
        {
            ".github/workflows/ci.yml": WORKFLOW.replace("echo hi", "echo bye").replace(
                "the checkout only", "read the tree"
            )
        },
        0,
        "none on the protected surface",
    ),
    (
        "a new workflow granting write",
        {".github/workflows/release.yml": "on: [push]\npermissions: write-all\n"},
        1,
        ".github/workflows/release.yml changes a workflow token's permissions",
    ),
    (
        "a ruleset",
        {".github/ruleset-main.json": '{"enforcement": "disabled"}\n'},
        1,
        "permissions-diff: .github/ruleset-main.json changes a branch ruleset",
    ),
    (
        "an ignore added to the advisory row's config",
        {"policy/osv-scanner.toml": '[[IgnoredVulns]]\nid = "GHSA-35jh-r3h4-6jhm"\n'},
        1,
        "permissions-diff: policy/osv-scanner.toml changes the tool rows' ignore lists",
    ),
    (
        "a gate row deleted",
        {".github/gates.toml": GATES_TOML.split("\n\n")[1]},
        1,
        "row 'secrets.range' deleted",
    ),
    (
        "a gate row added",
        {
            ".github/gates.toml": GATES_TOML
            + '\n[[gate]]\nid = "docs"\ninputs = ["docs/**"]\nrun = "true"\n'
        },
        0,
        "none on the protected surface",
    ),
    (
        "a Security row's red fixture edited in the gate proof",
        {
            ".ci/gate-registry.toml": PROOF_REGISTRY.replace(
                'red = "echo red"', 'red = "true"', 1
            )
        },
        1,
        ".ci/gate-registry.toml changes the gate proof's Security rows: row 'secret-scan' changed (red)",
    ),
    (
        "a Security row moved off the Security surface",
        {".ci/gate-registry.toml": PROOF_REGISTRY.replace('"Security"', '"PR checks"')},
        1,
        "row 'secret-scan' changed (surface)",
    ),
    (
        "a non-security row in the gate proof",
        {
            ".ci/gate-registry.toml": PROOF_REGISTRY.replace(
                '"PR checks"\nred = "echo red"', '"PR checks"\nred = "true"'
            )
        },
        0,
        "none on the protected surface",
    ),
    (
        "the captain's standing instructions",
        {"memory/captain.md": "# Captain\n"},
        1,
        "permissions-diff: memory/captain.md changes the captain's standing instructions",
    ),
    (
        "the memory write guard",
        {"config/hooks/memory-write-guard.sh": "exit 0\n"},
        1,
        "config/hooks/memory-write-guard.sh changes the memory write guard",
    ),
    (
        "the gate's own surface",
        {"run/permissions-diff.py": "SURFACE = []\n"},
        1,
        "run/permissions-diff.py changes this gate's protected surface",
    ),
    (
        "the GitHub App roles",
        {"system-maintenance/github-apps/roles.json": "{}\n"},
        1,
        "changes the GitHub App roles the minter subsets",
    ),
    (
        "an editor settings file",
        {".vscode/settings.json": '{"editor.tabSize": 2}\n'},
        0,
        "permissions-diff: 1 changed path(s), none on the protected surface",
    ),
    (
        "a README edit",
        {"README.md": "A fixture, edited.\n"},
        0,
        "permissions-diff: 1 changed path(s), none on the protected surface",
    ),
    (
        "a protected file deleted",
        {".claude/settings.json": None},
        1,
        ".claude/settings.json changes Claude Code settings",
    ),
]


def no_base():
    return expect(
        "no base compares with the empty tree",
        {},
        1,
        ".claude/settings.json changes Claude Code settings",
        base_ref="0" * 40,
    )


def unknown_base():
    rc, out = gate({}, base_ref="no-such-ref")
    if rc != 2 or "could not decide" not in out:
        return (
            f"an unknown base: wanted exit 2, could not decide; got exit {rc}:\n{out}"
        )
    return None


def merge_base():
    """A protected change on the base branch after the fork is not the head's."""
    repo = fixture({"README.md": "head's edit\n"})
    run("git", "checkout", "-q", "-b", "later", "base", cwd=repo)
    (repo / ".claude/settings.json").write_text("{}\n")
    run("git", "commit", "-qam", "base moves on", cwd=repo)
    run("git", "checkout", "-q", "main", cwd=repo)
    rc, out = run(sys.executable, str(GATE), "--base", "later", cwd=repo)
    if rc != 0:
        return f"a protected change only on the base branch refused the head (exit {rc}):\n{out}"
    return None


ROW = """[[gate]]
id = "permissions-diff"
inputs = ["**"]
tools = { base = 'git merge-base "$BASE_SHA" HEAD' }
run = 'python3 "$GATES_DIR/run/permissions-diff.py" --base "$BASE_SHA"'
"""


def shadow(since):
    """(run's exit, fold's exit, fold's output, the gate's log) for a widening under gate.py."""
    registry = ROW + (f"shadow_since = {since}\n" if since else "")
    repo = fixture(
        {".claude/settings.json": '{"sandbox": {"allowUnsandboxedCommands": true}}\n'},
        base={**BASE, ".github/gates.toml": registry},
    )
    store = repo.parent / f"{repo.name}-store"
    env = {**ENV, "GATES_DIR": str(ROOT)}
    _, env["BASE_SHA"] = run("git", "rev-parse", "base", cwd=repo)
    env["BASE_SHA"] = env["BASE_SHA"].strip()
    args = ("--base", "base", "--store", str(store))
    run_rc, _ = run(sys.executable, str(RUNNER), "run", *args, cwd=repo, env=env)
    fold_rc, out = run(sys.executable, str(RUNNER), "fold", *args, cwd=repo, env=env)
    logs = store / "logs"
    log = "".join(p.read_text() for p in logs.iterdir()) if logs.exists() else ""
    return run_rc, fold_rc, out, log


def in_shadow():
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    run_rc, fold_rc, out, log = shadow(today)
    want = f"shadow permissions-diff: fail: exit 1 (in shadow since {today}: 1 of 50 runs, 0 of 14 days)"
    if run_rc or fold_rc or want not in out:
        return f"a shadow row: wanted run and fold to exit 0 with {want!r}, got {run_rc}, {fold_rc}:\n{out}"
    if ".claude/settings.json changes Claude Code settings" not in log:
        return f"a shadow row's log does not name the path:\n{log}"
    return None


def established_blocks():
    """The same row with no shadow window blocks, so the window is what holds it back."""
    run_rc, fold_rc, out, _ = shadow(None)
    if run_rc != 1 or fold_rc != 1 or "FAIL permissions-diff: fail: exit 1" not in out:
        return f"an established row: wanted run and fold to refuse, got {run_rc}, {fold_rc}:\n{out}"
    return None


def keyed_by_base():
    """The verdict depends on the base, so the row's key must too."""
    repo = fixture({"README.md": "edited\n"}, base={**BASE, ".github/gates.toml": ROW})
    keys = set()
    for ref in ("base", "HEAD"):
        _, sha = run("git", "rev-parse", ref, cwd=repo)
        env = {**ENV, "GATES_DIR": str(ROOT), "BASE_SHA": sha.strip()}
        _, out = run(
            sys.executable, str(RUNNER), "key", "permissions-diff", cwd=repo, env=env
        )
        keys.add(out.strip())
    if len(keys) != 2:
        return f"the row's key did not change with its base: {keys}"
    return None


def main():
    failures = [expect(*case) for case in CASES]
    failures += [
        f()
        for f in (
            no_base,
            unknown_base,
            merge_base,
            in_shadow,
            established_blocks,
            keyed_by_base,
        )
    ]
    failures = [f for f in failures if f]
    total = len(CASES) + 6
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    print(f"test-permissions-diff: {total - len(failures)} of {total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
