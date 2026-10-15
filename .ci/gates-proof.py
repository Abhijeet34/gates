#!/usr/bin/env python3
"""Fail unless every gate in this repository is registered with a seeded defect.

Usage: gates-proof.py [repo-root] [registry]

A gate that nobody proved can refuse can silently stop refusing. This reads
.ci/gate-registry.toml and checks, per entry, that the gate and its test exist,
that the red and clean cases are still in the test, and that check.sh runs the
test; then that no gate file on disk is missing from the registry.
"""

import re
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
registry = Path(sys.argv[2]) if len(sys.argv) > 2 else root / ".ci/gate-registry.toml"
errors = []


def fail(msg):
    errors.append(msg)


def discovered():
    """Gate files on disk: workflows, hooks, and the non-test scripts under .ci."""
    found = set()
    found.update(
        str(p.relative_to(root)) for p in (root / ".github/workflows").glob("*.yml")
    )
    found.update(
        str(p.relative_to(root)) for p in (root / ".githooks").glob("*") if p.is_file()
    )
    hooks = root / "system-maintenance/git-hooks"
    for p in hooks.iterdir():
        name = p.name
        gate = (
            p.is_file() and not name.startswith("test-") and name not in ("README.md",)
        )
        if gate and p.suffix not in (".conf", ".pyc"):
            found.add(str(p.relative_to(root)))
    for p in (root / ".ci").glob("*.sh"):
        if not p.name.startswith("test-") and p.name != "check.sh":
            found.add(str(p.relative_to(root)))
    found.add(".ci/gitleaks/sync.sh")
    found.add(".gitleaks.toml")
    return found


def suite_body(check_sh, suite):
    m = re.search(
        rf"^check_{re.escape(suite)}\(\) \{{\n(.*?)^\}}", check_sh, re.S | re.M
    )
    return m.group(1) if m else None


data = tomllib.loads(registry.read_text())
check_sh = (root / ".ci/check.sh").read_text()
registered = set()
ids = set()

for g in data.get("gate", []):
    gid = g.get("id", "?")
    missing = [
        k
        for k in ("id", "surface", "gates", "test", "suite", "red", "clean")
        if not g.get(k)
    ]
    if missing:
        fail(f"{gid}: missing field(s) {', '.join(missing)}")
        continue
    if gid in ids:
        fail(f"{gid}: duplicate id")
    ids.add(gid)
    if g["red"] == g["clean"]:
        fail(f"{gid}: red and clean are the same case")
    for path in g["gates"]:
        registered.add(path)
        if not (root / path).is_file():
            fail(f"{gid}: gate file {path} does not exist")
    test = root / g["test"]
    if not test.is_file():
        fail(f"{gid}: test {g['test']} does not exist")
    else:
        text = test.read_text()
        for kind in ("red", "clean"):
            if g[kind] not in text:
                fail(f"{gid}: {kind} case {g[kind]!r} is no longer in {g['test']}")
    body = suite_body(check_sh, g["suite"])
    if body is None:
        fail(f"{gid}: check.sh has no suite function for {g['suite']!r}")
    elif g["test"] not in body:
        fail(f"{gid}: suite {g['suite']} in check.sh never runs {g['test']}")

for e in data.get("exempt", []):
    registered.add(e["path"])
    if not e.get("reason"):
        fail(f"exempt {e['path']}: no reason")
    if not (root / e["path"]).is_file():
        fail(f"exempt {e['path']}: file does not exist")

for path in sorted(discovered() - registered):
    fail(
        f"{path}: an unregistered gate; add it to {registry.name} with a red and a clean case"
    )

if not data.get("gate"):
    fail("the registry lists no gates")

if errors:
    print("\n".join(f"FAIL {e}" for e in errors), file=sys.stderr)
    sys.exit(1)
print(
    f"gates-proof: {len(ids)} gates registered, each red and clean case present, every gate file accounted for"
)
