#!/usr/bin/env python3
"""Regression tests for run/gate.py: selection, the verdict cache and the fold.

    .ci/test-gate-runner.py

Each case builds a fixture repository with a Rust, a migration, an SQL-lint and
a docs gate, drives the runner through its command line and returns why it
failed, or None. Every case must pass against run/gate.py, and then fail
against a copy with the one control that case exists for removed, so a case
that would pass without its control is itself refused.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "run/gate.py"

REGISTRY = """
[[gate]]
id = "rust"
inputs = ["src/**", "Cargo.toml", "Cargo.lock"]
tools = { cargo = "cargo --version" }
run = 'echo run >> "$RUNS/rust"'

[[gate]]
id = "migrations"
inputs = ["migrations/**"]
run = 'echo run >> "$RUNS/migrations"'

[[gate]]
id = "sql-lint"
inputs = ["**/*.sql"]
run = 'echo run >> "$RUNS/sql-lint"'

[[gate]]
id = "docs"
inputs = ["docs/**"]
run = 'echo run >> "$RUNS/docs"'
"""
ALL = ["rust", "migrations", "sql-lint", "docs"]
FILES = {
    ".github/gates.toml": REGISTRY,
    "src/lib.rs": "pub fn one() -> u8 { 1 }\n",
    "Cargo.toml": '[package]\nname = "fixture"\n',
    "Cargo.lock": "version = 3\n",
    "migrations/2026/0001_init.sql": "create table t (id int);\n",
    "docs/index.md": "# Fixture\n",
    "README.md": "A fixture.\n",
}

workdir = Path(tempfile.mkdtemp())


class Fixture:
    def __init__(self, runner):
        self.runner = runner
        self.dir = Path(tempfile.mkdtemp(dir=workdir))
        self.repo, self.store, self.runs, self.bin = (
            self.dir / n for n in ("repo", "store", "runs", "bin")
        )
        for d in (self.runs, self.bin):
            d.mkdir()
        self.cargo("1.80.0")
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "RUNS": str(self.runs),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@invalid",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@invalid",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.git(
            "init", "-q", "--template=", "-b", "main", str(self.repo), cwd=self.dir
        )
        self.base = self.commit(FILES)

    def cargo(self, version, rc=0):
        stub = self.bin / "cargo"
        stub.write_text(f"#!/bin/sh\necho 'cargo {version} (stub)'\nexit {rc}\n")
        stub.chmod(0o755)

    def git(self, *args, cwd=None):
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            env=self.env,
            capture_output=True,
            text=True,
        )
        if proc.returncode:
            raise RuntimeError(f"git {' '.join(args)}: {proc.stderr}")
        return proc.stdout.strip()

    def commit(self, files):
        for rel, text in files.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-qm", "change")
        return self.git("rev-parse", "HEAD")

    def gate(self, *args):
        proc = subprocess.run(
            [sys.executable, str(self.runner), *args],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def selected(self, base):
        rc, out = self.gate("select", "--base", base)
        return (
            rc,
            [line.split("\t")[0] for line in out.splitlines() if "\t" in line],
            out,
        )

    def runs_of(self, gid):
        path = self.runs / gid
        return len(path.read_text().splitlines()) if path.exists() else 0

    def records(self):
        path = self.store / "records.jsonl"
        return (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )


def unknown_path_selects_all(runner):
    f = Fixture(runner)
    f.commit(
        {
            "src/lib.rs": "pub fn one() -> u8 { 2 }\n",
            "notes/plan.txt": "named by no gate\n",
        }
    )
    rc, ids, out = f.selected(f.base)
    if rc != 0 or ids != ALL:
        return f"a change to notes/plan.txt (and src/lib.rs) selected {ids}, not {ALL} (exit {rc}): {out.strip()}"
    return None


def migration_change_selects_migration_gate(runner):
    f = Fixture(runner)
    f.commit({"migrations/2026/0002_add_index.sql": "create index i on t (id);\n"})
    rc, ids, out = f.selected(f.base)
    if rc != 0 or "migrations" not in ids:
        return f"a change under migrations/ selected {ids}, without the migration gate (exit {rc})"
    # Selecting everything would hide a matcher that missed the migration gate.
    if ids != ["migrations", "sql-lint"]:
        return f"a change under migrations/ selected {ids}, not exactly migrations and sql-lint"
    rc, out = f.gate("fold", "--base", f.base, "--store", str(f.store))
    if rc == 0 or "FAIL migrations: no verdict for this head" not in out:
        return f"the fold of a migration change with no migration verdict was not refused (exit {rc}): {out.strip()}"
    return None


def readme_change_reuses_rust_verdict(runner):
    f = Fixture(runner)
    rust = f.commit({"src/lib.rs": "pub fn one() -> u8 { 2 }\n"})
    rc, out = f.gate("run", "--base", f.base, "--store", str(f.store))
    if rc != 0 or f.runs_of("rust") != 1:
        return (
            f"the Rust change did not run the Rust gate once (exit {rc}): {out.strip()}"
        )
    readme = f.commit({"README.md": "A fixture, reworded.\n"})
    rc, out = f.gate("run", "--base", rust, "--store", str(f.store))
    if rc != 0 or f.runs_of("rust") != 1:
        return f"a README-only change ran the Rust gate again ({f.runs_of('rust')} runs, exit {rc}): {out.strip()}"
    rec = [r for r in f.records() if r["gate"] == "rust" and r["head"] == readme]
    if len(rec) != 1 or rec[0]["reused_from"] != rust or rec[0]["verdict"] != "pass":
        return f"the README head's Rust record is not a pass reused from the Rust head: {rec}"
    rc, out = f.gate("fold", "--base", rust, "--store", str(f.store))
    if rc != 0 or "pass rust (reused from" not in out:
        return f"the fold of the README head did not pass on the reused verdict (exit {rc}): {out.strip()}"
    return None


def tool_bump_changes_key(runner):
    f = Fixture(runner)
    first, again = f.gate("key", "rust"), f.gate("key", "rust")
    if first[0] != 0 or first != again:
        return f"the key is not stable for an unchanged tree and tool: {first} then {again}"
    f.cargo("1.81.0")
    bumped = f.gate("key", "rust")
    if bumped[0] != 0 or bumped[1] == first[1]:
        return f"bumping cargo 1.80.0 to 1.81.0 left the Rust key at {first[1].strip()}"
    return None


def absent_verdict_refuses(runner):
    f = Fixture(runner)
    head = f.commit(
        {
            "src/lib.rs": "pub fn one() -> u8 { 2 }\n",
            "migrations/2026/0002.sql": "select 1;\n",
        }
    )
    f.store.mkdir()
    line = {
        "schema": 1,
        "gate": "rust",
        "head": head,
        "verdict": "pass",
        "key": "sha256:x",
        "reused_from": None,
    }
    (f.store / "records.jsonl").write_text(json.dumps(line) + "\n")
    rc, out = f.gate("fold", "--base", f.base, "--store", str(f.store))
    if rc == 0 or "FAIL migrations: no verdict for this head" not in out:
        return f"a fold with no migration record was not refused naming it (exit {rc}): {out.strip()}"
    return None


def unprobeable_tool_is_error(runner):
    f = Fixture(runner)
    f.commit({"src/lib.rs": "pub fn one() -> u8 { 2 }\n"})
    f.cargo("1.80.0", rc=1)
    rc, out = f.gate("run", "--base", f.base, "--store", str(f.store))
    verdicts = [r["verdict"] for r in f.records()]
    if rc == 0 or verdicts != ["error"] or f.runs_of("rust") != 0:
        return f"a cargo that cannot report its version gave {verdicts} (exit {rc}, {f.runs_of('rust')} runs)"
    rc, out = f.gate("fold", "--base", f.base, "--store", str(f.store))
    if rc == 0 or "FAIL rust: error: rust: tool probe 'cargo' exited 1" not in out:
        return f"the fold of an error record was not refused naming it (exit {rc}): {out.strip()}"
    return None


def error_after_pass_refuses(runner):
    f = Fixture(runner)
    f.commit({"src/lib.rs": "pub fn one() -> u8 { 2 }\n"})
    rc, out = f.gate("run", "--base", f.base, "--store", str(f.store))
    if rc != 0:
        return f"the first run did not pass (exit {rc}): {out.strip()}"
    f.cargo("1.80.0", rc=1)
    f.gate("run", "--base", f.base, "--store", str(f.store))
    rc, out = f.gate("fold", "--base", f.base, "--store", str(f.store))
    if rc == 0 or "FAIL rust: error" not in out:
        return f"a pass followed by an error at the same head folded to {out.strip()!r} (exit {rc})"
    return None


def damaged_record_refuses(runner):
    f = Fixture(runner)
    f.commit({"src/lib.rs": "pub fn one() -> u8 { 2 }\n"})
    rc, out = f.gate("run", "--base", f.base, "--store", str(f.store))
    if rc != 0:
        return f"the run did not pass (exit {rc}): {out.strip()}"
    with open(f.store / "records.jsonl", "a") as h:
        h.write('{"schema": 1, "gate": "rust", "verdict": "err\n')
    rc, out = f.gate("fold", "--base", f.base, "--store", str(f.store))
    if rc == 0 or "records.jsonl:2:" not in out:
        return f"a truncated record after a pass folded to {out.strip()!r} (exit {rc})"
    return None


# (case, control it proves, text in run/gate.py, the text with the control removed)
CASES = [
    (
        unknown_path_selects_all,
        "rule 1, a path no gate names selects every gate",
        'return dict.fromkeys(everyone, f"{path} is named by no gate")',
        "continue",
    ),
    (
        migration_change_selects_migration_gate,
        "`**` spanning directories",
        'out.append(".*")\n            i += 2',
        'out.append("[^/]*")\n            i += 2',
    ),
    (
        readme_change_reuses_rust_verdict,
        "a key over the gate's inputs only",
        "if matches(row, path):\n                entries.append",
        "if path:\n                entries.append",
    ),
    (
        tool_bump_changes_key,
        "tool probe output in the key",
        '"tools": tools,',
        '"tools": {},',
    ),
    (
        absent_verdict_refuses,
        "an absent record refusing",
        "rec = latest.get(gid)\n",
        'rec = latest.get(gid) or {"verdict": "pass"}\n',
    ),
    (
        unprobeable_tool_is_error,
        "a failed tool probe recording error",
        "if proc.returncode:",
        "if False:",
    ),
    (
        error_after_pass_refuses,
        "the latest record for a head deciding",
        'latest[rec["gate"]] = rec',
        'latest.setdefault(rec["gate"], rec)',
    ),
    (
        damaged_record_refuses,
        "a damaged record refusing the fold",
        "            if strict:\n                raise Undecided",
        "            if False:\n                raise Undecided",
    ),
]


def main():
    failed = 0
    source = RUNNER.read_text()
    for case, control, old, new in CASES:
        why = case(RUNNER)
        if why:
            failed = 1
            print(f"FAIL {case.__name__}: {why}")
            continue
        print(f"ok   {case.__name__}")
        if source.count(old) != 1:
            failed = 1
            print(
                f"FAIL without {control}: the mutation matches {source.count(old)} places in run/gate.py, not 1"
            )
            continue
        mutant = Path(tempfile.mkdtemp(dir=workdir)) / "gate.py"
        mutant.write_text(source.replace(old, new))
        why = case(mutant)
        if why:
            quoted = " | ".join(why.splitlines())
            print(f"ok   without {control}, {case.__name__} goes red: {quoted}")
        else:
            failed = 1
            print(
                f"FAIL without {control}, {case.__name__} still passes, so the case does not prove it"
            )
    print("\ngate runner tests " + ("FAILED" if failed else "passed"))
    return failed


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
