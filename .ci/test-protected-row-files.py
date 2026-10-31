#!/usr/bin/env python3
"""Every file a protected row of .ci/gate-registry.toml names is on run/permissions-diff.py's surface.

    .ci/test-protected-row-files.py

A protected row is one run/permissions-diff.py protects in the registry: its
surface is in SECURITY_SURFACES or its id in SECURITY_ROWS. Each file such a row
lists in `gates` or runs as `command` is committed into a fixture repository
beside this tree's registry, then edited in one change, and the gate must
refuse that change naming every one of them. A file the registry names that
the gate lets change is the control its row proves, changing unrefused.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/permissions-diff.py"
REGISTRY = ".ci/gate-registry.toml"
# Files the protected rows run that a hand-kept surface once let change, so a
# registry the suite reads wrongly cannot pass it by naming nothing.
KNOWN = [
    "run/secret-scan/verify.sh",
    "run/secret-scan/history.sh",
    "run/secret-scan/tree.sh",
    "run/lock.sh",
    "run/bump-identity",
    "run/critic.py",
    "run/critic-eval.py",
    "run/corpus.py",
    "run/lens-eval.py",
    ".ci/gitleaks/sync.sh",
]
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


def run(*args, cwd):
    proc = subprocess.run(
        args, cwd=cwd, env=ENV, stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    return proc.returncode, proc.stdout + proc.stderr


def protected_files():
    """Each file a protected row names, with the rows naming it, by the gate's own predicate."""
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "run"))
    spec = importlib.util.spec_from_file_location("permissions_diff", GATE)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    rows = tomllib.loads((ROOT / REGISTRY).read_text())["gate"]
    files = {}
    for row in rows:
        if not gate.protected(row):
            continue
        for path in [*row.get("gates", []), row.get("command")]:
            if path:
                files.setdefault(path, []).append(row["id"])
    return files


def main():
    failures = []
    files = protected_files()
    failures += [
        f"{path} is no longer named by a protected row; update KNOWN with the row"
        for path in KNOWN
        if path not in files
    ]
    repo = Path(tempfile.mkdtemp())
    try:
        run("git", "init", "-q", "--template=", "-b", "main", cwd=repo)
        (repo / ".ci").mkdir()
        shutil.copy(ROOT / REGISTRY, repo / REGISTRY)
        for path in files:
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# {path}\n")
        run("git", "add", "-A", cwd=repo)
        run("git", "commit", "-qm", "base", cwd=repo)
        for path in files:
            with open(repo / path, "a") as f:
                f.write("exit 0\n")
        run("git", "commit", "-qam", "change", cwd=repo)
        rc, out = run(sys.executable, str(GATE), "--base", "HEAD~1", cwd=repo)
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    if rc != 1:
        failures.append(f"the gate exited {rc}, not 1, on the change:\n{out}")
    named = {
        line.removeprefix("permissions-diff: ").split(" changes ", 1)[0]
        for line in out.splitlines()
        if " changes " in line
    }
    failures += [
        f"{path}, which row {', '.join(repr(r) for r in rows)} names, can change unrefused"
        for path, rows in sorted(files.items())
        if path not in named
    ]
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    passed = len(files) - sum(p not in named for p in files)
    print(
        f"test-protected-row-files: {passed} of {len(files)} protected row files refused"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
