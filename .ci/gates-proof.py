#!/usr/bin/env python3
"""Fail unless every gate in this repository is proved, by execution, able to fail.

Usage: gates-proof.py [--static] [--gate ID] [repo-root] [registry]

A gate that nobody proved can refuse can silently stop refusing. Per entry of
.ci/gate-registry.toml this runs the gate on its `red` input and requires a
non-zero verdict naming `finding`, then on its `clean` input and requires a
pass, then on `red` again with the gate's `command` replaced by one that exits
0 and requires that neutered gate to let it through: a case whose verdict does
not come from the command it claims to prove is itself refused. It also checks
that each gate's test exists and its check.sh suite runs it, and that no gate
file on disk is missing from the registry. `--static` stops before executing
anything; `--gate` executes one entry only.
"""

import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--static", action="store_true")
ap.add_argument("--gate")
ap.add_argument("root", nargs="?", default=".")
ap.add_argument("registry", nargs="?")
args = ap.parse_args()
root = Path(args.root).resolve()
registry = Path(args.registry) if args.registry else root / ".ci/gate-registry.toml"
FIELDS = ("id", "surface", "gates", "test", "suite", "command")
CASE_FIELDS = ("red", "clean", "run", "finding")
TIMEOUT = 180
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


def strip_comments(text):
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def suite_body(check_sh, suite):
    m = re.search(
        rf"^check_{re.escape(suite)}\(\) \{{\n(.*?)^\}}", check_sh, re.S | re.M
    )
    return m.group(1) if m else None


def runs(body, test):
    pat = rf"^\s*(?:(?:bash|sh|python3?)\s+)?(?:\./)?{re.escape(test)}(?:\s|$)"
    return re.search(pat, body, re.M) is not None


def step_block(lines, step):
    """(first, end) line indexes of the `run: |` body of the step named `step`."""
    names = [i for i, line in enumerate(lines) if line.strip() == f"- name: {step}"]
    if len(names) != 1:
        return None
    for i in range(names[0] + 1, len(lines)):
        if lines[i].lstrip().startswith("- "):
            return None
        if re.fullmatch(r"\s*run: \|\s*", lines[i]):
            break
    else:
        return None
    first = i + 1
    body = [ln for ln in lines[first:] if ln.strip()]
    indent = len(body[0]) - len(body[0].lstrip()) if body else 0
    end = first
    while end < len(lines) and (
        not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) >= indent
    ):
        end += 1
    while end > first and not lines[end - 1].strip():
        end -= 1
    return (first, end, indent) if end > first else None


def resolve(tree, command, scratch):
    """The executable path of `command` in `tree`: the file, or its extracted step body."""
    path, _, step = command.partition("#")
    if not step:
        return tree / path
    lines = (tree / path).read_text().splitlines()
    first, end, indent = step_block(lines, step)
    body = scratch / "step.sh"
    body.write_text("\n".join(ln[indent:] for ln in lines[first:end]) + "\n")
    return body


def neuter(tree, command):
    """Replace `command` in `tree` with one that exits 0 having checked nothing."""
    path, _, step = command.partition("#")
    target = tree / path
    if step:
        lines = target.read_text().splitlines()
        first, end, indent = step_block(lines, step)
        lines[first:end] = [" " * indent + "true"]
        target.write_text("\n".join(lines) + "\n")
    elif target.suffix == ".py":
        target.write_text("#!/usr/bin/env python3\nraise SystemExit(0)\n")
    elif target.suffix == ".toml":
        target.write_text("")
    else:
        target.write_text("#!/bin/sh\nexit 0\n")


def execute(gate, kind, tree):
    """Run one case; (setup completed, exit status, output of the run)."""
    with tempfile.TemporaryDirectory(prefix=f"gates-proof-{gate['id']}-") as tmp:
        scratch = Path(tmp)
        work = scratch / "work"
        work.mkdir()
        script = "\n".join(
            [
                "set -e",
                gate[kind],
                ': > "$PROOF_SCRATCH/setup-done"',
                "set +e",
                'exec >"$PROOF_SCRATCH/out" 2>&1',
                gate["run"],
            ]
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
        }
        noreply = "gates-proof@users.noreply.github.com"
        env.update(
            ROOT=str(tree),
            GATE=str(resolve(tree, gate["command"], scratch)),
            PROOF_SCRATCH=str(scratch),
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_CONFIG_SYSTEM="/dev/null",
            GIT_AUTHOR_NAME="gates-proof",
            GIT_AUTHOR_EMAIL=noreply,
            GIT_COMMITTER_NAME="gates-proof",
            GIT_COMMITTER_EMAIL=noreply,
            PYTHONDONTWRITEBYTECODE="1",
        )
        try:
            proc = subprocess.run(
                ["bash", "-c", script],
                cwd=work,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=TIMEOUT,
            )
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            return True, 124, f"timed out after {TIMEOUT}s"
        if not (scratch / "setup-done").exists():
            setup = (proc.stdout + proc.stderr).decode(errors="replace")
            return False, rc, setup
        return True, rc, (scratch / "out").read_text(errors="replace")


def tail(text, n=8):
    lines = text.strip().splitlines()[-n:]
    return "".join(f"\n       | {line}" for line in lines)


def prove(gate):
    """Problems found executing one gate's cases, and how many cases ran."""
    gid, problems = gate["id"], []
    ok, rc, out = execute(gate, "red", root)
    if not ok:
        problems.append(f"{gid}: red input setup failed (exit {rc}){tail(out)}")
    elif rc == 0:
        problems.append(f"{gid}: red input was not refused{tail(out)}")
    elif gate["finding"] not in out:
        problems.append(
            f"{gid}: red input refused (exit {rc}) without naming {gate['finding']!r}{tail(out)}"
        )
    ok, rc, out = execute(gate, "clean", root)
    if not ok:
        problems.append(f"{gid}: clean input setup failed (exit {rc}){tail(out)}")
    elif rc != 0:
        problems.append(f"{gid}: clean input was refused (exit {rc}){tail(out)}")
    with tempfile.TemporaryDirectory(prefix=f"gates-proof-{gid}-mutant-") as tmp:
        mutant = Path(tmp) / "tree"
        shutil.copytree(
            root,
            mutant,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", "__pycache__"),
        )
        neuter(mutant, gate["command"])
        ok, rc, out = execute(gate, "red", mutant)
    if not ok:
        problems.append(
            f"{gid}: red input setup failed against the neutered gate (exit {rc}){tail(out)}"
        )
    elif rc != 0:
        problems.append(
            f"{gid}: with {gate['command']} neutered the red input is still refused (exit {rc}),"
            f" so the case does not prove that command{tail(out)}"
        )
    return problems, 3


data = tomllib.loads(registry.read_text())
check_sh = strip_comments((root / ".ci/check.sh").read_text())
registered = set()
ids = set()
provable = []

for g in data.get("gate", []):
    gid = g.get("id", "?")
    missing = [k for k in FIELDS + CASE_FIELDS if not g.get(k)]
    if missing:
        fail(f"{gid}: missing field(s) {', '.join(missing)}")
        continue
    if gid in ids:
        fail(f"{gid}: duplicate id")
    ids.add(gid)
    if g["red"] == g["clean"]:
        fail(f"{gid}: red and clean are the same input")
    for path in g["gates"]:
        registered.add(path)
        if not (root / path).is_file():
            fail(f"{gid}: gate file {path} does not exist")
    path, _, step = g["command"].partition("#")
    if path not in g["gates"]:
        fail(f"{gid}: command {g['command']} is not one of its gates")
    elif not (root / path).is_file():
        pass
    elif step and not step_block((root / path).read_text().splitlines(), step):
        fail(f"{gid}: no single step named {step!r} with a `run: |` body in {path}")
    else:
        provable.append(g)
    if not (root / g["test"]).is_file():
        fail(f"{gid}: test {g['test']} does not exist")
    body = suite_body(check_sh, g["suite"])
    if body is None:
        fail(f"{gid}: check.sh has no suite function for {g['suite']!r}")
    elif not runs(body, g["test"]):
        fail(f"{gid}: suite {g['suite']} in check.sh never runs {g['test']}")

# The suite has to run the full proof: a flag added there would leave it green
# having executed nothing.
body = suite_body(check_sh, "gates-proof") or ""
if not re.search(r"^\s*python3 \.ci/gates-proof\.py(\s+\|\||\s*$)", body, re.M):
    fail("check.sh suite gates-proof does not run `python3 .ci/gates-proof.py` bare")

for e in data.get("exempt", []):
    registered.add(e["path"])
    if not e.get("reason"):
        fail(f"exempt {e['path']}: no reason")
    if not (root / e["path"]).is_file():
        fail(f"exempt {e['path']}: file does not exist")

for path in sorted(discovered() - registered):
    fail(
        f"{path}: an unregistered gate; add it to {registry.name} with a red and a clean input"
    )

if not data.get("gate"):
    fail("the registry lists no gates")

cases = 0
if not args.static:
    chosen = [g for g in provable if args.gate in (None, g["id"])]
    if args.gate and not chosen:
        fail(f"--gate {args.gate}: no provable gate has that id")
    with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        for g, (problems, n) in zip(chosen, pool.map(prove, chosen), strict=True):
            errors.extend(problems)
            cases += n
            print(f"{'FAIL' if problems else 'ok  '} {g['id']}")
    # Zero cases is a proof that examined nothing, never a pass.
    if not cases:
        fail("no case was executed")

if errors:
    print("\n".join(f"FAIL {e}" for e in errors), file=sys.stderr)
    sys.exit(1)
if args.static:
    print(f"gates-proof: {len(ids)} gates registered, every gate file accounted for")
else:
    print(
        f"gates-proof: {len(ids)} gates registered, {cases} cases executed:"
        " each red input refused naming its finding, each clean input passed,"
        " each neutered gate let its red input through"
    )
