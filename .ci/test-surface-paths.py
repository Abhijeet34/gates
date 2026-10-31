#!/usr/bin/env python3
"""Every repository-specific glob of permissions-diff's SURFACE matches a tracked file.

    .ci/test-surface-paths.py

A glob rooted at `**/`, or at a path any caller may hold (GENERIC), protects
whatever repository runs the gate. Every other glob names one repository's
layout, and REPOSITORY says which; once that repository moves the file, the
glob matches nothing and the surface silently loses it, so this suite fails.

This repository's files are read from `git ls-files`. Another repository's are
read from `.ci/fixtures/surface-paths/<repo>.txt`, its `git ls-files` output
taken at the commit whose `.github/gates.lock` moves to a new gates commit, so
the fixture is refreshed with the gates pin. A repository listed in AWAITING
has no fixture yet and its globs go unchecked, named on every run; a fixture
for it, or a repository with neither, fails.

The checks run first over fixed cases, so a check that stops refusing fails.
"""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/permissions-diff.py"
FIXTURES = ROOT / ".ci/fixtures/surface-paths"
SELF = "gates"

sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "run"))
from gate import glob_re  # noqa: E402

# Globs at a path any caller may hold, so no one repository's file list judges them.
GENERIC = {
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".github/ruleset*.json",
    ".github/rulesets/**",
    ".github/gates.toml",
    ".github/licence-policy.toml",
}

# The repository whose layout each remaining glob names.
REPOSITORY = {
    "internal/sandbox/profile.go": "factory",
    "internal/sandbox/*.go": "factory",
    "internal/permit/permit.go": "factory",
    "route.toml": "factory",
    "internal/adapter/claude/claude.go": "factory",
    "repos/depbase.go": "factory",
    "egress/*.go": "factory",
    "outbound/outbound.go": "factory",
    ".ci/gate-registry.toml": SELF,
    "policy/**": SELF,
    "run/tool-gates.py": SELF,
    "run/pr-text.py": SELF,
    "run/licence.py": SELF,
    "run/weakening.py": SELF,
    "run/record-approval.py": SELF,
    "run/review.py": SELF,
    "run/gate.py": SELF,
    "run/secret-scan.sh": SELF,
    ".gitleaks.toml": SELF,
    ".ci/test-workflow-policy.sh": SELF,
    "run/dependency-advisories.sh": SELF,
    "run/dependency-advisories/**": SELF,
    "run/permissions-diff.py": SELF,
}

# Repositories with no fixture yet, and why: their globs are not checked.
AWAITING = {
    "factory": "its file list is not readable from a gates checkout; the fixture"
    " and the globs that follow its move into internal/domain and"
    " internal/platform land together",
}


def surface_globs(source):
    """The glob of every SURFACE entry, read from the source before it is compiled."""
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "SURFACE" for t in node.targets)
            and isinstance(node.value, ast.List)
        ):
            return [ast.literal_eval(e.elts[0]) for e in node.value.elts]
    raise SystemExit(f"{GATE}: no SURFACE list")


def problems(globs, repository, awaiting, files):
    """Each failure: a glob with no repository, a stale map entry, or a glob matching nothing.

    `files(repo)` is that repository's tracked paths, or None when it has no fixture.
    """
    out, notes = [], []
    specific = [g for g in globs if not g.startswith("**/") and g not in GENERIC]
    for g in specific:
        if g not in repository:
            out.append(f"{g} names no repository: add it to REPOSITORY or GENERIC")
    for g in sorted(set(repository) - set(globs)):
        out.append(f"{g} is in REPOSITORY but not on SURFACE")
    for repo in sorted({repository[g] for g in specific if g in repository}):
        names = files(repo)
        if repo in awaiting:
            if names is not None:
                out.append(f"{repo} has a fixture but is still in AWAITING")
            else:
                notes.append(f"{repo} not checked: {awaiting[repo]}")
            continue
        if names is None:
            fixture = FIXTURES.relative_to(ROOT) / f"{repo}.txt"
            out.append(f"{repo} has no fixture at {fixture}")
            continue
        for g in specific:
            if repository.get(g) == repo:
                rx = glob_re(g)
                if not any(rx.match(n) for n in names):
                    out.append(f"{g} matches no tracked file of {repo}")
    return out, notes


def tracked(repo):
    if repo == SELF:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
        ).stdout
        return [p for p in out.decode().split("\0") if p]
    path = FIXTURES / f"{repo}.txt"
    if not path.exists():
        return None
    lines = path.read_text().splitlines()
    return [ln for ln in lines if ln and not ln.startswith("#")]


def cases():
    """The checks on fixed input: every expected verdict they did not give."""
    lists = {"a": ["src/x.go", "lib/y.py"], "b": ["route.toml"]}
    files = lists.get
    want = [
        # (globs, repository, awaiting, the failure each must produce, or none)
        (["src/*.go", "**/z.json"], {"src/*.go": "a"}, {}, None),
        (["src/*.go"], {"src/*.go": "a"}, {}, None),
        (
            ["moved/*.go"],
            {"moved/*.go": "a"},
            {},
            "moved/*.go matches no tracked file of a",
        ),
        (["src/**"], {"src/**": "a"}, {}, None),
        (["src/x.go"], {}, {}, "src/x.go names no repository"),
        (
            ["src/x.go"],
            {"src/x.go": "a", "gone": "a"},
            {},
            "gone is in REPOSITORY but not on SURFACE",
        ),
        (["x"], {"x": "c"}, {}, "c has no fixture"),
        (["x"], {"x": "c"}, {"c": "pending"}, None),
        (
            ["x"],
            {"x": "b"},
            {"b": "pending"},
            "b has a fixture but is still in AWAITING",
        ),
        ([".github/workflows/*.yml"], {}, {}, None),
    ]
    missed = []
    for globs, repository, awaiting, expect in want:
        got, _ = problems(globs, repository, awaiting, files)
        if expect is None and got:
            missed.append(f"{globs}: expected a pass, got {got}")
        elif expect is not None and not any(expect in g for g in got):
            missed.append(f"{globs}: expected {expect!r}, got {got}")
    return missed


def main():
    missed = cases()
    for m in missed:
        print(f"FAIL case {m}", file=sys.stderr)
    found, notes = problems(
        surface_globs(GATE.read_text()), REPOSITORY, AWAITING, tracked
    )
    for n in notes:
        print(f"test-surface-paths: {n}")
    for f in found:
        print(f"FAIL {f}", file=sys.stderr)
    if missed or found:
        return 1
    print(
        "test-surface-paths: every checked repository-specific glob matches"
        " a tracked file"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
