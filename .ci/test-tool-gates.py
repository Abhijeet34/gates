#!/usr/bin/env python3
"""Regression tests for run/tool-gates.py, and its three rows in shadow under run/gate.py.

    .ci/test-tool-gates.py            stub tools; needs nothing installed
    .ci/test-tool-gates.py --real     the pinned tools on PATH, and for the osv
                                      cases OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY

The default run drives the script against stub tools that report a version and
an exit status, so it proves the script's own decisions: which pin it accepts,
which status is a finding, which is a typed refusal. Each stub exits 99 unless it
is called with the flags in GUARDS, so a call that could reach the network, or
read configuration from the tree under review, fails here. `--real` is the same
questions put to the real binaries, including a tree that plants each
configuration the tool would read; .ci/check.sh runs it only under CI, where
ci.yml installs them (README.md, "The tool rows").
"""

import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/tool-gates.py"
RUNNER = ROOT / "run/gate.py"
DB_ENV = "OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY"
CLEAN_ENV = {
    k: v for k, v in os.environ.items() if k != DB_ENV and not k.startswith("GIT_")
}
ENV = {
    **CLEAN_ENV,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@invalid",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@invalid",
    "PYTHONDONTWRITEBYTECODE": "1",
}
workdir = Path(tempfile.mkdtemp())
WORKFLOW = "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo hi\n"
LOCKFILE = '{"lockfileVersion": 3, "packages": {}}\n'


def npm_lock(entry, version="4.17.15"):
    """An npm lockfile whose one installed package is `entry`; the root entry has a version too."""
    root = {"name": "fx", "version": "1.0.0", "dependencies": {"lodash": version}}
    packages = {"": root, "node_modules/lodash": entry}
    return json.dumps({"name": "fx", "lockfileVersion": 3, "packages": packages}) + "\n"


def npm_lock_v1(entry, version="4.17.15", nested=None):
    """A lockfile v1: one `dependencies` entry, and `nested` as the map under it when given."""
    if nested:
        entry = {**entry, "dependencies": nested}
    return json.dumps({"lockfileVersion": 1, "dependencies": {"lodash": entry}}) + "\n"


def lodash(version, resolved=True):
    """The entry npm writes, or with `resolved=False` the shape osv-scanner 2.7.0 skips as local."""
    entry = {"version": version}
    if resolved:
        entry["resolved"] = f"https://registry.npmjs.org/lodash/-/lodash-{version}.tgz"
    return entry


DAY = 86400


def fresh_dir():
    return Path(tempfile.mkdtemp(dir=workdir))


def run(*args, cwd, env=ENV):
    proc = subprocess.run(
        args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    return proc.returncode, proc.stdout + proc.stderr


def tree(files):
    root = fresh_dir()
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


# The flags a call must carry: --offline keeps the network out, the rest keep the tree
# under review from supplying the tool's configuration or hiding a file from the walk.
GUARDS = {
    "zizmor": ("--offline", "--no-config", "--no-ignores"),
    "actionlint": ("-config-file",),
    "osv-scanner": ("--offline", "--config", "--no-ignore"),
}


def stub(bin_dir, name, version, rc, banner="stub output"):
    """A tool that reports `version`, and otherwise prints `banner` and exits `rc`."""
    shown = {
        "zizmor": f"zizmor {version}",
        "actionlint": f"{version}\ninstalled by stub",
        "osv-scanner": f"osv-scanner version: {version}\nosv-scalibr version: 0",
    }[name]
    guard = "".join(
        f'case " $* " in *" {flag} "*) ;; *) echo "stub: no {flag}" >&2; exit 99 ;; esac\n'
        for flag in GUARDS[name]
    )
    path = bin_dir / name
    path.write_text(
        f"#!/bin/sh\ncase \"$1\" in --version) cat <<'EOF'\n{shown}\nEOF\nexit 0 ;; esac\n"
        f"{guard}echo '{banner}'\nexit {rc}\n"
    )
    path.chmod(0o755)


def database(ages=None):
    """A database directory with one all.zip per ecosystem, each as many days old as given."""
    folder = fresh_dir()
    for eco, age_days in (ages or {"npm": 0}).items():
        zipfile = folder / "osv-scalibr" / eco / "all.zip"
        zipfile.parent.mkdir(parents=True)
        zipfile.write_bytes(b"PK")
        stamp = time.time() - age_days * DAY
        os.utime(zipfile, (stamp, stamp))
    return folder


def stubbed(tools, db=None):
    """(PATH, extra env) with each `name: (version, rc[, banner])` of `tools` as a stub."""
    bin_dir = fresh_dir()
    for name, spec in tools.items():
        stub(bin_dir, name, *spec)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    if db:
        env[DB_ENV] = str(db)
    return env


def gate(check, files, env):
    root = tree(files)
    return run(
        sys.executable, str(GATE), check, str(root), cwd=workdir, env={**ENV, **env}
    )


WF = {".github/workflows/ci.yml": WORKFLOW}
LOCK = {"package-lock.json": LOCKFILE}
LODASH_RED = npm_lock(lodash("4.17.15"))
LOCK_RED = {"package-lock.json": LODASH_RED}
NO_TOOLS = {"PATH": str(fresh_dir())}


def refused(kind):
    return f"tool-gates: refused ({kind}):"


def package_sources():
    spec = importlib.util.spec_from_file_location("tool_gates", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PACKAGE_SOURCES


# One case per name osv-scanner reads: a `*` becomes a stem so the pattern is a real file name.
SOURCE_CASES = [
    (
        f"osv exit 128 on a tree holding only {pattern} is undecided",
        "osv",
        {pattern.replace("*", "x"): "x"},
        stubbed({"osv-scanner": ("2.7.0", 128)}, database()),
        2,
        pattern.replace("*", "x"),
    )
    for pattern in package_sources()
]


# name, check, files, environment, wanted exit, wanted output
STUB_CASES = [
    ("zizmor missing", "zizmor", WF, NO_TOOLS, 2, refused("missing-tool")),
    ("actionlint missing", "actionlint", WF, NO_TOOLS, 2, refused("missing-tool")),
    (
        "osv-scanner missing",
        "osv",
        LOCK,
        {**NO_TOOLS, DB_ENV: str(database())},
        2,
        refused("missing-tool"),
    ),
    (
        "zizmor missing with no workflows",
        "zizmor",
        {},
        NO_TOOLS,
        2,
        refused("missing-tool"),
    ),
    (
        "zizmor of another version",
        "zizmor",
        WF,
        stubbed({"zizmor": ("1.30.0", 0)}),
        2,
        "zizmor reports 1.30.0, the pin is 1.29.0",
    ),
    (
        "actionlint of another version",
        "actionlint",
        WF,
        stubbed({"actionlint": ("1.7.13", 0)}),
        2,
        "actionlint reports 1.7.13, the pin is 1.7.12",
    ),
    (
        "osv-scanner of another version",
        "osv",
        LOCK,
        stubbed({"osv-scanner": ("2.6.0", 0)}, database()),
        2,
        "osv-scanner reports 2.6.0, the pin is 2.7.0",
    ),
    (
        "zizmor with no findings passes",
        "zizmor",
        WF,
        stubbed({"zizmor": ("1.29.0", 0)}),
        0,
        "1 workflow file(s), no findings",
    ),
    (
        "zizmor exit 13 is a finding",
        "zizmor",
        WF,
        stubbed({"zizmor": ("1.29.0", 13, "error[dangerous-triggers]")}),
        1,
        "tool-gates: zizmor refused the workflows (exit 13)",
    ),
    (
        "zizmor exit 14 carries its output",
        "zizmor",
        WF,
        stubbed({"zizmor": ("1.29.0", 14, "error[dangerous-triggers]")}),
        1,
        "error[dangerous-triggers]",
    ),
    (
        "zizmor exit 1 is its own error, not a finding",
        "zizmor",
        WF,
        stubbed({"zizmor": ("1.29.0", 1, "fatal: no audit was performed")}),
        2,
        refused("tool-error"),
    ),
    (
        "zizmor with no workflow files says so",
        "zizmor",
        {"README.md": "x"},
        stubbed({"zizmor": ("1.29.0", 13)}),
        0,
        "no workflow files under .github/workflows, nothing to check",
    ),
    (
        "actionlint with no findings passes",
        "actionlint",
        WF,
        stubbed({"actionlint": ("1.7.12", 0)}),
        0,
        "no findings",
    ),
    (
        "actionlint exit 1 is a finding",
        "actionlint",
        WF,
        stubbed({"actionlint": ("1.7.12", 1, "ci.yml:1:1: bad")}),
        1,
        "tool-gates: actionlint refused the workflows (exit 1)",
    ),
    (
        "actionlint exit 3 is its own error, not a finding",
        "actionlint",
        WF,
        stubbed({"actionlint": ("1.7.12", 3, "could not read")}),
        2,
        refused("tool-error"),
    ),
    (
        "osv with the variable unset",
        "osv",
        LOCK,
        stubbed({"osv-scanner": ("2.7.0", 0)}),
        2,
        refused("missing-database"),
    ),
    (
        "osv with an empty database directory",
        "osv",
        LOCK,
        stubbed({"osv-scanner": ("2.7.0", 0)}, fresh_dir()),
        2,
        "holds no all.zip",
    ),
    (
        "osv with a database 8 days old",
        "osv",
        LOCK,
        stubbed({"osv-scanner": ("2.7.0", 0)}, database({"npm": 8})),
        2,
        refused("stale-database"),
    ),
    (
        "osv with one of two databases 8 days old",
        "osv",
        LOCK,
        stubbed({"osv-scanner": ("2.7.0", 0)}, database({"npm": 0, "crates.io": 8})),
        2,
        "crates.io/all.zip is 8.0 days old, the limit is 7",
    ),
    (
        "osv with two fresh databases counts both",
        "osv",
        LOCK,
        stubbed({"osv-scanner": ("2.7.0", 0)}, database({"npm": 0, "crates.io": 1})),
        0,
        "no known vulnerabilities (2 database file(s))",
    ),
    (
        "osv with a database 6 days old passes",
        "osv",
        LOCK,
        stubbed({"osv-scanner": ("2.7.0", 0)}, database({"npm": 6})),
        0,
        "no known vulnerabilities",
    ),
    (
        "osv exit 1 is a finding",
        "osv",
        LOCK,
        stubbed({"osv-scanner": ("2.7.0", 1, "GHSA-xxxx")}, database()),
        1,
        "tool-gates: osv-scanner refused the tree: known vulnerabilities",
    ),
    (
        "osv exit 128 says nothing was scanned",
        "osv",
        {"README.md": "x"},
        stubbed({"osv-scanner": ("2.7.0", 128)}, database()),
        0,
        "no package source in the tree, nothing to check",
    ),
    (
        "osv exit 128 on a tree that holds a lockfile is undecided, not a pass",
        "osv",
        LOCK,
        stubbed({"osv-scanner": ("2.7.0", 128)}, database()),
        2,
        refused("undecided"),
    ),
    (
        "osv exit 128 names the nested lockfile it found",
        "osv",
        {"web/Cargo.lock": "x"},
        stubbed({"osv-scanner": ("2.7.0", 128)}, database()),
        2,
        "web/Cargo.lock",
    ),
    (
        "osv reads a lockfile that a .gitignore names",
        "osv",
        {**LOCK_RED, ".gitignore": "package-lock.json\n"},
        stubbed({"osv-scanner": ("2.7.0", 1, "GHSA-xxxx")}, database()),
        1,
        "known vulnerabilities",
    ),
    (
        "osv reads a lockfile in a folder that a .gitignore names",
        "osv",
        {"web/package-lock.json": LODASH_RED, ".gitignore": "web/\n"},
        stubbed({"osv-scanner": ("2.7.0", 1, "GHSA-xxxx")}, database()),
        1,
        "known vulnerabilities",
    ),
    (
        "osv exit 127 naming a missing ecosystem",
        "osv",
        LOCK,
        stubbed(
            {"osv-scanner": ("2.7.0", 127, "no offline version of the OSV database")},
            database(),
        ),
        2,
        refused("missing-database"),
    ),
    (
        "osv exit 127 of another cause",
        "osv",
        LOCK,
        stubbed({"osv-scanner": ("2.7.0", 127, "panic")}, database()),
        2,
        refused("tool-error"),
    ),
    (
        "osv refuses a version-only npm entry as undecided",
        "osv",
        {"package-lock.json": npm_lock(lodash("4.17.15", resolved=False))},
        stubbed({"osv-scanner": ("2.7.0", 0)}, database()),
        2,
        refused("undecided"),
    ),
    (
        "osv refuses a lockfile v1 dependency with no resolved URL as undecided",
        "osv",
        {"package-lock.json": npm_lock_v1(lodash("4.17.15", resolved=False))},
        stubbed({"osv-scanner": ("2.7.0", 0)}, database()),
        2,
        "package-lock.json: dependencies > lodash has a version and no resolved URL",
    ),
    (
        "osv refuses a nested lockfile v1 dependency with no resolved URL",
        "osv",
        {
            "package-lock.json": npm_lock_v1(
                lodash("4.18.1"), nested={"b": {"version": "1.0.0"}}
            )
        },
        stubbed({"osv-scanner": ("2.7.0", 0)}, database()),
        2,
        "dependencies > lodash > b has a version and no resolved URL",
    ),
    (
        "osv passes a lockfile v1 dependency with its resolved URL",
        "osv",
        {
            "package-lock.json": npm_lock_v1(
                lodash("4.18.1"), nested={"b": lodash("1.0.0")}
            )
        },
        stubbed({"osv-scanner": ("2.7.0", 0)}, database()),
        0,
        "no known vulnerabilities",
    ),
    (
        "osv names the lockfile and the entry it cannot decide",
        "osv",
        {"web/package-lock.json": npm_lock(lodash("4.17.15", resolved=False))},
        stubbed({"osv-scanner": ("2.7.0", 0)}, database()),
        2,
        "web/package-lock.json: node_modules/lodash has a version and no resolved URL",
    ),
    (
        "osv refuses a version-only entry in npm-shrinkwrap.json, which it also reads",
        "osv",
        {"npm-shrinkwrap.json": npm_lock(lodash("4.17.15", resolved=False))},
        stubbed({"osv-scanner": ("2.7.0", 0)}, database()),
        2,
        refused("undecided"),
    ),
    (
        "osv refuses a nested package with no resolved URL",
        "osv",
        {
            "package-lock.json": json.dumps(
                {"packages": {"node_modules/a/node_modules/b": {"version": "1.0.0"}}}
            )
        },
        stubbed({"osv-scanner": ("2.7.0", 0)}, database()),
        2,
        refused("undecided"),
    ),
    (
        "osv refuses a lockfile it cannot parse as undecided",
        "osv",
        {"package-lock.json": "{"},
        stubbed({"osv-scanner": ("2.7.0", 0)}, database()),
        2,
        refused("undecided"),
    ),
    (
        "osv passes an npm entry with its resolved URL",
        "osv",
        {"package-lock.json": npm_lock(lodash("4.18.1"), "4.18.1")},
        stubbed({"osv-scanner": ("2.7.0", 0)}, database()),
        0,
        "no known vulnerabilities",
    ),
    (
        "osv passes a workspace link, which has no version to judge",
        "osv",
        {
            "package-lock.json": json.dumps(
                {
                    "packages": {
                        "": {"name": "fx", "version": "1.0.0"},
                        "node_modules/a": {"resolved": "packages/a", "link": True},
                        "packages/a": {"version": "1.0.0"},
                    }
                }
            )
        },
        stubbed({"osv-scanner": ("2.7.0", 0)}, database()),
        0,
        "no known vulnerabilities",
    ),
    ("an unknown check is a usage error", "nope", {}, {}, 2, "Usage: tool-gates.py"),
]


def expect(name, check, files, env, want, finding):
    rc, out = gate(check, files, env)
    if rc != want:
        return f"{name}: exit {rc}, wanted {want}:\n{out}"
    if finding not in out:
        return f"{name}: output does not carry {finding!r}:\n{out}"
    return None


def stamp_cases():
    """The cache-key probe prints the day and each database file, and refuses without one."""
    folder = database({"npm": 0, "crates.io": 0})
    rc, out = gate("osv-stamp", {}, {DB_ENV: str(folder)})
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    lines = out.splitlines()
    first = None
    if rc != 0 or lines[:1] != [today] or len(lines) != 3:
        first = f"osv-stamp: wanted the day and two files, got {rc}:\n{out}"
    rc, out = gate("osv-stamp", {}, {})
    second = None
    if rc != 2 or refused("missing-database") not in out or today in out:
        second = f"osv-stamp without a database: got {rc}:\n{out}"
    return [first, second]


ROWS = """
[[gate]]
id = "workflow-zizmor"
inputs = [".github/workflows/**"]
tools = {{ zizmor = "zizmor --version" }}
run = 'python3 "$GATES_DIR/run/tool-gates.py" zizmor .'
{shadow}
[[gate]]
id = "workflow-actionlint"
inputs = [".github/workflows/**"]
tools = {{ actionlint = "actionlint --version" }}
run = 'python3 "$GATES_DIR/run/tool-gates.py" actionlint .'
{shadow}
[[gate]]
id = "advisories-osv"
inputs = ["**/*lock.json", "**/*lock.yaml", "**/*.lock", "**/go.mod", "**/requirements*.txt"]
tools = {{ osv = "osv-scanner --version", database = 'python3 "$GATES_DIR/run/tool-gates.py" osv-stamp' }}
run = 'python3 "$GATES_DIR/run/tool-gates.py" osv .'
{shadow}"""
IDS = ("workflow-zizmor", "workflow-actionlint", "advisories-osv")


def shadow(since):
    """(run's exit, fold's exit, fold's output) for a tree all three tools refuse, under gate.py."""
    registry = ROWS.format(shadow=f"shadow_since = {since}\n" if since else "")
    repo = tree({**WF, **LOCK, ".github/gates.toml": registry})
    env = {
        **ENV,
        "GATES_DIR": str(ROOT),
        **stubbed(
            {
                "zizmor": ("1.29.0", 13),
                "actionlint": ("1.7.12", 1),
                "osv-scanner": ("2.7.0", 1),
            },
            database(),
        ),
    }
    run("git", "init", "-q", "--template=", "-b", "main", cwd=repo)
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-qm", "base", cwd=repo)
    run("git", "tag", "base", cwd=repo)
    (repo / "package-lock.json").write_text(LOCKFILE + "\n")
    (repo / ".github/workflows/ci.yml").write_text(WORKFLOW + "\n")
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-qm", "change", cwd=repo)
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


# --real: the same questions put to the pinned binaries on PATH.
BAD_WORKFLOW = "on:\n  pull_request_target:\npermissions: {}\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo hi\n"
BAD_EXPRESSION = WORKFLOW.replace("echo hi", "echo ${{ github.nope }}")
ZIZMOR_IGNORE = "rules:\n  dangerous-triggers:\n    ignore:\n      - ci.yml\n"
INLINE_IGNORE = BAD_WORKFLOW.replace(
    "pull_request_target:", "pull_request_target: # zizmor: ignore[dangerous-triggers]"
)
OSV_IGNORE = '[[PackageOverrides]]\nname = "lodash"\necosystem = "npm"\nignore = true\n'
ACTIONLINT_IGNORE = (
    'paths:\n  .github/workflows/*.yml:\n    ignore:\n      - "is not defined"\n'
)
CARGO_LOCK = (
    '[[package]]\nname = "time"\nversion = "0.1.45"\n'
    'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
)


def real_cases(seen):
    wf = ".github/workflows/ci.yml"
    db = os.environ.get(DB_ENV)
    if not db:
        return [f"--real needs {DB_ENV} to name a database directory"]
    npm_only = fresh_dir() / "osv-scalibr" / "npm"
    npm_only.mkdir(parents=True)
    (npm_only / "all.zip").symlink_to(next(Path(db).rglob("npm/all.zip")).resolve())
    env = {DB_ENV: db}
    cases = [
        (
            "zizmor passes a clean workflow",
            "zizmor",
            {wf: WORKFLOW},
            {},
            0,
            "no findings",
        ),
        (
            "zizmor refuses pull_request_target",
            "zizmor",
            {wf: BAD_WORKFLOW},
            {},
            1,
            "dangerous-triggers",
        ),
        (
            "actionlint passes a clean workflow",
            "actionlint",
            {wf: WORKFLOW},
            {},
            0,
            "no findings",
        ),
        (
            "actionlint refuses an unknown context property",
            "actionlint",
            {wf: BAD_EXPRESSION},
            {},
            1,
            'property "nope" is not defined',
        ),
        (
            "osv refuses lodash 4.17.15",
            "osv",
            {"package-lock.json": LODASH_RED},
            env,
            1,
            "GHSA-35jh-r3h4-6jhm",
        ),
        (
            "osv refuses a version-only entry as undecided, where 2.7.0 scans it clean",
            "osv",
            {"package-lock.json": npm_lock(lodash("4.17.15", resolved=False))},
            env,
            2,
            refused("undecided"),
        ),
        (
            "osv refuses lodash 4.17.15 with a config file at the root",
            "osv",
            {"package-lock.json": LODASH_RED, "osv-scanner.toml": OSV_IGNORE},
            env,
            1,
            "GHSA-35jh-r3h4-6jhm",
        ),
        (
            "osv refuses lodash 4.17.15 with a config file beside a nested lockfile",
            "osv",
            {
                "web/package-lock.json": LODASH_RED,
                "web/osv-scanner.toml": OSV_IGNORE,
            },
            env,
            1,
            "GHSA-35jh-r3h4-6jhm",
        ),
        (
            "osv refuses lodash 4.17.15 when a .gitignore names the lockfile",
            "osv",
            {
                "package-lock.json": LODASH_RED,
                ".gitignore": "package-lock.json\n",
                # osv-scanner reads .gitignore only where it finds a git repository.
                ".git/HEAD": "ref: refs/heads/main\n",
            },
            env,
            1,
            "GHSA-35jh-r3h4-6jhm",
        ),
        (
            "osv refuses lodash 4.17.15 when a .gitignore names the lockfile's folder",
            "osv",
            {
                "web/package-lock.json": LODASH_RED,
                ".gitignore": "web/\n",
                ".git/HEAD": "ref: refs/heads/main\n",
            },
            env,
            1,
            "GHSA-35jh-r3h4-6jhm",
        ),
        (
            "osv refuses a lockfile v1 dependency with no resolved URL, where 2.7.0 scans it clean",
            "osv",
            {"package-lock.json": npm_lock_v1(lodash("4.17.15", resolved=False))},
            env,
            2,
            refused("undecided"),
        ),
        (
            "osv refuses lodash 4.17.15 in a lockfile v1",
            "osv",
            {"package-lock.json": npm_lock_v1(lodash("4.17.15"))},
            env,
            1,
            "GHSA-35jh-r3h4-6jhm",
        ),
        (
            "zizmor refuses pull_request_target with a config file at the root",
            "zizmor",
            {wf: BAD_WORKFLOW, "zizmor.yml": ZIZMOR_IGNORE},
            {},
            1,
            "dangerous-triggers",
        ),
        (
            "zizmor refuses pull_request_target with a config file under .github",
            "zizmor",
            {wf: BAD_WORKFLOW, ".github/zizmor.yml": ZIZMOR_IGNORE},
            {},
            1,
            "dangerous-triggers",
        ),
        (
            "zizmor refuses pull_request_target with an inline ignore comment",
            "zizmor",
            {wf: INLINE_IGNORE},
            {},
            1,
            "dangerous-triggers",
        ),
        (
            "actionlint refuses an unknown context property with an ignore config file",
            "actionlint",
            {
                wf: BAD_EXPRESSION,
                ".github/actionlint.yaml": ACTIONLINT_IGNORE,
                # actionlint reads a project's config only where it finds a git repository.
                ".git/HEAD": "ref: refs/heads/main\n",
            },
            {},
            1,
            'property "nope" is not defined',
        ),
        (
            "osv passes lodash 4.18.1",
            "osv",
            {"package-lock.json": npm_lock(lodash("4.18.1"), "4.18.1")},
            env,
            0,
            "no known vulnerabilities",
        ),
        (
            "osv refuses a Cargo.lock when only npm is downloaded",
            "osv",
            {"Cargo.lock": CARGO_LOCK},
            {DB_ENV: str(npm_only.parent.parent)},
            2,
            refused("missing-database"),
        ),
        (
            "osv says nothing was scanned",
            "osv",
            {"README.md": "x"},
            env,
            0,
            "nothing to check",
        ),
    ]
    results = []
    for case in cases:
        t0 = time.monotonic()
        results.append(expect(*case))
        seen.append((case[0], time.monotonic() - t0))
    return results


def main():
    real = "--real" in sys.argv[1:]
    seen = []
    if real:
        results = real_cases(seen)
    else:
        results = [expect(*case) for case in STUB_CASES + SOURCE_CASES]
        results += stamp_cases()
        results += [f() for f in (in_shadow, established_blocks)]
    failures = [r for r in results if r]
    total = len(results)
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    for name, seconds in seen:
        print(f"  {seconds:5.2f} s  {name}")
    label = "real" if real else "stub"
    print(f"test-tool-gates ({label}): {total - len(failures)} of {total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
