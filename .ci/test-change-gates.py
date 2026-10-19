#!/usr/bin/env python3
"""Regression tests for run/change-gates.py, and its scoped shadow row under run/gate.py.

    .ci/test-change-gates.py

Each case builds a throwaway repository with a base commit and a change, runs one
row and requires its exit status and a line of its output. The near misses matter
as much as the hits: a contract row that fires on a change to its own snapshot, or
a fact row that fires on a version that merely contains the old one, would cost
more false alarms than it prevents.
"""

import datetime
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/change-gates.py"
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
NO_BASE = "0" * 40


def run(*args, cwd, env=ENV):
    proc = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def repo(base_files, change_files, deleted=()):
    """A repository with `base_files` committed and tagged `base`, then `change_files` on top."""
    here = Path(tempfile.mkdtemp(dir=workdir))
    run("git", "init", "-q", "--template=", "-b", "main", cwd=here)

    def put(files):
        for name, text in files.items():
            path = here / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    put(base_files)
    run("git", "add", "-A", cwd=here)
    run("git", "commit", "-qm", "base", cwd=here)
    run("git", "tag", "base", cwd=here)
    put(change_files)
    for name in deleted:
        (here / name).unlink()
    run("git", "add", "-A", cwd=here)
    run("git", "commit", "-qm", "change", cwd=here)
    return here


def contract_toml(body):
    return f'[[contract]]\nid = "cli"\nsnapshot = "skills/cli/SKILL.md"\n{body}\n'


SOURCES = contract_toml('sources = ["src/cli.js", "bin/**"]')
TABLE = "CREATE TABLE waitlist (email TEXT NOT NULL, product TEXT NOT NULL);\n"
MIGRATE = '[[migrations]]\nid = "d1"\ndir = "migrations"\n{extra}\n'
SQLITE = MIGRATE.format(extra='dry_run = "sqlite"')
FACT = (
    '[[fact]]\nid = "pin"\nnow = "zizmor 1.29.0"\nwas = ["zizmor 1.28.0"]\n'
    'decision = "maintainer decision 2026-10-19"\n{extra}\n'
)
FACT_BEFORE = (
    '[[fact]]\nid = "pin"\nnow = "zizmor 1.28.0"\nwas = []\ndecision = "none yet"\n'
)


# (name, row, base files, change files, deleted, base arg, want exit, a line the output must carry)
CASES = [
    (
        "a source changed and the snapshot did not",
        "contracts",
        {
            ".github/changes.toml": SOURCES,
            "skills/cli/SKILL.md": "v1\n",
            "src/cli.js": "a\n",
        },
        {"src/cli.js": "b\n"},
        (),
        "base",
        1,
        "contracts: cli: src/cli.js changed and skills/cli/SKILL.md did not",
    ),
    (
        "a source and the snapshot changed together",
        "contracts",
        {
            ".github/changes.toml": SOURCES,
            "skills/cli/SKILL.md": "v1\n",
            "src/cli.js": "a\n",
        },
        {"src/cli.js": "b\n", "skills/cli/SKILL.md": "v2\n"},
        (),
        "base",
        0,
        "every declared contract matches its snapshot",
    ),
    (
        "a glob source under bin/ is a source",
        "contracts",
        {".github/changes.toml": SOURCES, "skills/cli/SKILL.md": "v1\n"},
        {"bin/tool.js": "x\n"},
        (),
        "base",
        1,
        "bin/tool.js changed",
    ),
    (
        "a change to the snapshot alone is not a finding",
        "contracts",
        {
            ".github/changes.toml": SOURCES,
            "skills/cli/SKILL.md": "v1\n",
            "src/cli.js": "a\n",
        },
        {"skills/cli/SKILL.md": "v2\n"},
        (),
        "base",
        0,
        None,
    ),
    (
        "a file no source names is not a finding",
        "contracts",
        {".github/changes.toml": SOURCES, "skills/cli/SKILL.md": "v1\n"},
        {"src/other.js": "x\n", "README.md": "x\n"},
        (),
        "base",
        0,
        None,
    ),
    (
        "a snapshot that is not in the tree",
        "contracts",
        {".github/changes.toml": SOURCES},
        {"src/cli.js": "x\n"},
        (),
        "base",
        1,
        "the snapshot skills/cli/SKILL.md does not exist",
    ),
    (
        "a command whose output equals the snapshot",
        "contracts",
        {
            ".github/changes.toml": contract_toml(
                "command = \"printf 'usage: cli\\\\n'\""
            ),
            "skills/cli/SKILL.md": "usage: cli\n",
        },
        {"notes.txt": "x\n"},
        (),
        None,
        0,
        None,
    ),
    (
        "a command whose output differs from the snapshot",
        "contracts",
        {
            ".github/changes.toml": contract_toml(
                "command = \"printf 'usage: cli\\\\n  --new\\\\n'\""
            ),
            "skills/cli/SKILL.md": "usage: cli\n",
        },
        {"notes.txt": "x\n"},
        (),
        None,
        1,
        "differs from skills/cli/SKILL.md (first at line 2)",
    ),
    (
        "a command that fails is not a pass",
        "contracts",
        {
            ".github/changes.toml": contract_toml('command = "exit 3"'),
            "skills/cli/SKILL.md": "x\n",
        },
        {"notes.txt": "x\n"},
        (),
        None,
        2,
        "refused (tool-error): contract 'cli'",
    ),
    (
        "a command that is not installed",
        "contracts",
        {
            ".github/changes.toml": contract_toml('command = "no-such-cli --help"'),
            "skills/cli/SKILL.md": "x\n",
        },
        {"notes.txt": "x\n"},
        (),
        None,
        2,
        "refused (missing-tool)",
    ),
    (
        "sources without a base",
        "contracts",
        {".github/changes.toml": SOURCES, "skills/cli/SKILL.md": "v1\n"},
        {"src/cli.js": "x\n"},
        (),
        None,
        2,
        "refused (no-base)",
    ),
    (
        "a contract naming both sources and a command",
        "contracts",
        {
            ".github/changes.toml": contract_toml('sources = ["a"]\ncommand = "true"'),
            "skills/cli/SKILL.md": "x\n",
        },
        {"notes.txt": "x\n"},
        (),
        "base",
        2,
        "refused (bad-declaration)",
    ),
    (
        "no declaration file",
        "contracts",
        {"a.txt": "x\n"},
        {"a.txt": "y\n"},
        (),
        "base",
        2,
        "refused (undeclared)",
    ),
    (
        "a landed migration edited",
        "migrations",
        {".github/changes.toml": SQLITE, "migrations/0001_a.sql": TABLE},
        {"migrations/0001_a.sql": TABLE + "-- tweak\n"},
        (),
        "base",
        1,
        "migrations/0001_a.sql was edited",
    ),
    (
        "a landed migration deleted",
        "migrations",
        {".github/changes.toml": SQLITE, "migrations/0001_a.sql": TABLE},
        {"README.md": "x\n"},
        ("migrations/0001_a.sql",),
        "base",
        1,
        "migrations/0001_a.sql was deleted",
    ),
    (
        "a landed migration renamed",
        "migrations",
        {".github/changes.toml": SQLITE, "migrations/0001_a.sql": TABLE},
        {"migrations/0002_a.sql": TABLE},
        ("migrations/0001_a.sql",),
        "base",
        1,
        "migrations/0001_a.sql was deleted",
    ),
    (
        "a new migration that applies",
        "migrations",
        {".github/changes.toml": SQLITE, "migrations/0001_a.sql": TABLE},
        {"migrations/0002_b.sql": "ALTER TABLE waitlist ADD COLUMN note TEXT;\n"},
        (),
        "base",
        0,
        "no landed migration changed; 1 dry run(s) passed",
    ),
    (
        "a new migration that does not apply",
        "migrations",
        {".github/changes.toml": SQLITE, "migrations/0001_a.sql": TABLE},
        {"migrations/0002_b.sql": "ALTER TABLE missing ADD COLUMN note TEXT;\n"},
        (),
        "base",
        1,
        "migrations/0002_b.sql failed in a scratch database: no such table: missing",
    ),
    (
        "a migration may not attach another database",
        "migrations",
        {".github/changes.toml": SQLITE, "migrations/0001_a.sql": TABLE},
        {"migrations/0002_b.sql": "ATTACH DATABASE 'elsewhere.db' AS other;\n"},
        (),
        "base",
        1,
        "migrations/0002_b.sql failed in a scratch database",
    ),
    (
        "a file in another directory is not a migration",
        "migrations",
        {
            ".github/changes.toml": SQLITE,
            "migrations/0001_a.sql": TABLE,
            "docs/x.sql": "x\n",
        },
        {"docs/x.sql": "y\n", "docs/migrations/0001_a.sql": "z\n"},
        (),
        "base",
        0,
        None,
    ),
    (
        "a new migration under no dry run",
        "migrations",
        {
            ".github/changes.toml": MIGRATE.format(extra=""),
            "migrations/0001_a.sql": TABLE,
        },
        {"migrations/0002_b.sql": "this is not sql\n"},
        (),
        "base",
        0,
        "0 dry run(s) passed",
    ),
    (
        "a declared dry-run command that exits non-zero",
        "migrations",
        {
            ".github/changes.toml": MIGRATE.format(
                extra='dry_run = "echo broken >&2; exit 4"'
            ),
            "migrations/0001_a.sql": TABLE,
        },
        {"migrations/0002_b.sql": TABLE},
        (),
        "base",
        1,
        "the dry run 'echo broken >&2; exit 4' exited 4\n  broken",
    ),
    (
        "a declared dry-run command sees a scratch directory and the new files",
        "migrations",
        {
            ".github/changes.toml": MIGRATE.format(
                extra=(
                    'dry_run = "test -d \\"$SCRATCH\\" && test -z \\"$(ls -A \\"$SCRATCH\\")\\"'
                    ' && test \\"$NEW_MIGRATIONS\\" = migrations/0002_b.sql"'
                )
            ),
            "migrations/0001_a.sql": TABLE,
        },
        {"migrations/0002_b.sql": TABLE},
        (),
        "base",
        0,
        "1 dry run(s) passed",
    ),
    (
        "a migration change measured from no base lands everything as new",
        "migrations",
        {".github/changes.toml": SQLITE},
        {"migrations/0001_a.sql": "this is not sql\n"},
        (),
        NO_BASE,
        1,
        "migrations/0001_a.sql failed in a scratch database",
    ),
    (
        "a stale statement of a superseded fact",
        "facts",
        {
            ".github/changes.toml": FACT_BEFORE,
            "README.md": "Pinned to zizmor 1.28.0.\n",
        },
        {".github/changes.toml": FACT.format(extra="")},
        (),
        "base",
        1,
        "facts: README.md:1: still states 'zizmor 1.28.0', superseded (pin; maintainer decision 2026-10-19)",
    ),
    (
        "every statement of the old fact is swept",
        "facts",
        {
            ".github/changes.toml": FACT_BEFORE,
            "README.md": "a\nzizmor 1.28.0\n",
            "docs/a.md": "x zizmor 1.28.0 y\n",
        },
        {".github/changes.toml": FACT.format(extra="")},
        (),
        "base",
        1,
        "facts: docs/a.md:1",
    ),
    (
        "the old fact swept away",
        "facts",
        {
            ".github/changes.toml": FACT_BEFORE,
            "README.md": "Pinned to zizmor 1.28.0.\n",
        },
        {
            ".github/changes.toml": FACT.format(extra=""),
            "README.md": "Pinned to zizmor 1.29.0.\n",
        },
        (),
        "base",
        0,
        "no tracked file states a superseded fact (1 checked)",
    ),
    (
        "a file that may quote the old fact as history",
        "facts",
        {".github/changes.toml": FACT_BEFORE, "CHANGELOG.md": "zizmor 1.28.0\n"},
        {".github/changes.toml": FACT.format(extra='allow = ["CHANGELOG.md"]')},
        (),
        "base",
        0,
        None,
    ),
    (
        "a longer version that merely contains the old one",
        "facts",
        {
            ".github/changes.toml": FACT_BEFORE,
            "README.md": "zizmor 1.28.01 and zizmor 1.28.0.1 and xzizmor 1.28.0\n",
        },
        {".github/changes.toml": FACT.format(extra="")},
        (),
        "base",
        0,
        None,
    ),
    (
        "a fact superseded earlier is not swept again",
        "facts",
        {".github/changes.toml": FACT.format(extra=""), "README.md": "zizmor 1.28.0\n"},
        {"notes.txt": "x\n"},
        (),
        "base",
        0,
        "(0 checked)",
    ),
    (
        "without a base every declared fact is swept",
        "facts",
        {".github/changes.toml": FACT.format(extra=""), "README.md": "zizmor 1.28.0\n"},
        {"notes.txt": "x\n"},
        (),
        None,
        1,
        "README.md:1",
    ),
    (
        "a binary file is not read",
        "facts",
        {".github/changes.toml": FACT_BEFORE},
        {".github/changes.toml": FACT.format(extra=""), "blob.bin": "zizmor 1.28.0\0"},
        (),
        "base",
        0,
        None,
    ),
    (
        "a fact whose current value is also a past one",
        "facts",
        {".github/changes.toml": FACT_BEFORE},
        {
            ".github/changes.toml": FACT.format(extra="").replace(
                'now = "zizmor 1.29.0"', 'now = "zizmor 1.28.0"'
            )
        },
        (),
        "base",
        2,
        "refused (bad-declaration)",
    ),
    (
        "a fact with no decision",
        "facts",
        {".github/changes.toml": FACT_BEFORE},
        {
            ".github/changes.toml": FACT.format(extra="").replace(
                "decision =", "reason ="
            )
        },
        (),
        "base",
        2,
        "decision must be a non-empty string",
    ),
]


def expect(name, row, base_files, change_files, deleted, base, want, finding):
    here = repo(base_files, change_files, deleted)
    argv = [sys.executable, str(GATE), row, str(here)]
    if base:
        argv[3:3] = ["--base", base]
    rc, out = run(*argv, cwd=here)
    if rc != want:
        return f"{name}: exit {rc}, wanted {want}:\n{out}"
    if finding and finding not in out:
        return f"{name}: output does not carry {finding!r}:\n{out}"
    return None


ROWS = """
[[gate]]
id = "contracts"
inputs = ["src/cli.js", "skills/cli/SKILL.md", ".github/changes.toml"]
scoped = true
tools = {{ base = 'git merge-base "$BASE_SHA" HEAD' }}
run = 'python3 "$GATES_DIR/run/change-gates.py" contracts --base "$BASE_SHA"'
shadow_since = {since}

[[gate]]
id = "migrations"
inputs = ["migrations/**", ".github/changes.toml"]
scoped = true
tools = {{ base = 'git merge-base "$BASE_SHA" HEAD' }}
run = 'python3 "$GATES_DIR/run/change-gates.py" migrations --base "$BASE_SHA"'
shadow_since = {since}

[[gate]]
id = "facts"
inputs = [".github/changes.toml"]
scoped = true
tools = {{ base = 'git merge-base "$BASE_SHA" HEAD' }}
run = 'python3 "$GATES_DIR/run/change-gates.py" facts --base "$BASE_SHA"'
shadow_since = {since}

[[gate]]
id = "docs"
inputs = ["docs/**"]
run = "true"
"""


def under_the_runner():
    """Scoped rows stay out of an unrelated change, and a red one is shadow, not a block."""
    since = datetime.datetime.now(datetime.UTC).date().isoformat()
    declared = "\n".join([SOURCES, SQLITE, FACT_BEFORE])
    base = {
        ".github/gates.toml": ROWS.format(since=since),
        ".github/changes.toml": declared,
        "skills/cli/SKILL.md": "v1\n",
        "src/cli.js": "a\n",
        "migrations/0001_a.sql": TABLE,
        "README.md": "Pinned to zizmor 1.28.0.\n",
        "docs/a.md": "x\n",
    }
    env = {**ENV, "GATES_DIR": str(ROOT), "BASE_SHA": "base"}
    red = repo(
        base,
        {
            "src/cli.js": "b\n",
            "migrations/0001_a.sql": TABLE + "-- tweak\n",
            ".github/changes.toml": declared.replace(
                FACT_BEFORE, FACT.format(extra="")
            ),
        },
    )
    store = Path(tempfile.mkdtemp(dir=workdir)) / "store"
    args = ("--base", "base", "--store", str(store))
    run_rc, _ = run(sys.executable, str(RUNNER), "run", *args, cwd=red, env=env)
    fold_rc, out = run(sys.executable, str(RUNNER), "fold", *args, cwd=red, env=env)
    wanted = [
        f"shadow {gid}: fail: exit 1 (in shadow since"
        for gid in ("contracts", "migrations", "facts")
    ]
    if run_rc != 0 or fold_rc != 0 or any(w not in out for w in wanted):
        return f"three red rows in their shadow window: run {run_rc}, fold {fold_rc}:\n{out}"
    other = repo(base, {"src/app.py": "x\n"})
    chosen = run(
        sys.executable, str(RUNNER), "select", "--base", "base", cwd=other, env=env
    )[1]
    if any(f"{gid}\t" in chosen for gid in ("contracts", "migrations", "facts")):
        return f"a change to src/app.py selected a scoped row:\n{chosen}"
    if "docs\t" not in chosen:
        return f"a change to src/app.py did not select the unscoped docs row:\n{chosen}"
    return None


def main():
    failures = [expect(*case) for case in CASES]
    failures += [under_the_runner()]
    failures = [f for f in failures if f]
    total = len(CASES) + 1
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    print(f"test-change-gates: {total - len(failures)} of {total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
