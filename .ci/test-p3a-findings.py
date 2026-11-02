#!/usr/bin/env python3
"""The P3a review reproductions, replayed against the rows that were fixed for them.

    .ci/test-p3a-findings.py

PRs 54, 57 to 64 and 67 closed findings of the P3a security reviews. Each case here
replays one of those reproductions through the row's public entry point, a run/
script on a throwaway tree or repository, and requires the refusal the fix added.
A case is named by the PR that fixed it and the behaviour it pins, so a regression
in any of them names the PR to read. The row suites hold the wider coverage; this
one is the short list of attacks that once got through.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"
# The python3 on PATH, the one the suite is run as: a sandbox can refuse to
# exec the opt symlink sys.executable names (measured 2026-10-31).
PYTHON = shutil.which("python3") or sys.executable
PLANTED = "planted-by-test-p3a-findings"
ENV = {
    **{
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("GIT_", "STUB_"))
        and k not in ("OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY", "GATES_DIR")
    },
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@invalid",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@invalid",
    "PYTHONDONTWRITEBYTECODE": "1",
}

workdir = Path(tempfile.mkdtemp())


class Link(str):
    """A file given as a Link is a symlink to this target."""


def run(*args, cwd, env=None):
    proc = subprocess.run(
        [str(a) for a in args],
        cwd=cwd,
        env=env or ENV,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def fresh():
    return Path(tempfile.mkdtemp(dir=workdir))


def put(here, files):
    for rel, text in files.items():
        path = here / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            path.unlink()
        elif isinstance(text, Link):
            path.unlink(missing_ok=True)
            path.symlink_to(text)
        else:
            path.write_text(text)


def tree(files):
    here = fresh()
    put(here, files)
    return here


def repo(base, change):
    """A repository whose `base` tag holds `base` and whose HEAD applies `change`."""
    here = fresh()
    run("git", "init", "-q", "--template=", "-b", "main", cwd=here)
    for files, tag in ((base, "base"), (change, None)):
        put(here, files)
        run("git", "add", "-A", cwd=here)
        run("git", "commit", "-qm", tag or "change", "--allow-empty", cwd=here)
        if tag:
            run("git", "tag", tag, cwd=here)
    return here


def expect(result, want, *needles, absent=()):
    rc, out = result
    missing = [n for n in needles if n not in out]
    present = [n for n in absent if n in out]
    if rc != want or missing or present:
        return f"wanted exit {want} with {missing} and without {present}, got {rc}:\n{out.strip()}"
    return None


# --- the tool rows: run/tool-gates.py ------------------------------------------------

TOOL_GATES = RUN / "tool-gates.py"
PINS = {"zizmor": "1.29.0", "actionlint": "1.7.12", "osv-scanner": "2.7.0"}
# The flags each tool must be called with, or its stub exits 99: no network, no
# configuration from the tree under review, no file hidden from the walk by it.
GUARDS = {
    "zizmor": ("--offline", "--no-config", "--no-ignores"),
    "actionlint": ("-config-file",),
    "osv-scanner": ("--offline", "--config", "--no-ignore"),
}
WORKFLOW = "on: push\npermissions: {}\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo hi\n"


def tools(rcs, banner="stub output"):
    """(env) with a guarded stub of each `name: exit status` and a fresh npm database."""
    bin_dir = fresh()
    for name, rc in rcs.items():
        shown = {
            "zizmor": f"zizmor {PINS[name]}",
            "actionlint": f"{PINS[name]}\ninstalled by stub",
            "osv-scanner": f"osv-scanner version: {PINS[name]}\nosv-scalibr version: 0",
        }[name]
        guard = "".join(
            f'case " $* " in *" {flag} "*) ;; *) echo "stub: no {flag}" >&2; exit 99 ;; esac\n'
            for flag in GUARDS[name]
        )
        (bin_dir / name).write_text(
            f"#!/bin/sh\ncase \"$1\" in --version) cat <<'EOF'\n{shown}\nEOF\nexit 0 ;; esac\n"
            f"{guard}echo '{banner}'\nexit {rc}\n"
        )
        (bin_dir / name).chmod(0o755)
    db = fresh() / "osv-scalibr/npm/all.zip"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"PK")
    stamp = time.time()
    os.utime(db, (stamp, stamp))
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY": str(db.parents[2]),
    }


def tool_gate(check, files, env):
    return run(PYTHON, TOOL_GATES, check, tree(files), cwd=workdir, env={**ENV, **env})


def lodash(version, resolved=True):
    entry = {"version": version}
    if resolved:
        entry["resolved"] = f"https://registry.npmjs.org/lodash/-/lodash-{version}.tgz"
    return entry


def npm_lock(entry):
    root = {"name": "fx", "version": "1.0.0", "dependencies": {"lodash": "4.17.15"}}
    packages = {"": root, "node_modules/lodash": entry}
    return json.dumps({"name": "fx", "lockfileVersion": 3, "packages": packages})


def npm_lock_v1(entry):
    return json.dumps({"lockfileVersion": 1, "dependencies": {"lodash": entry}})


def pr54_version_only_npm_entry_undecided():
    """An npm entry with a version and no resolved URL, which osv-scanner skips as local."""
    return expect(
        tool_gate(
            "osv",
            {"web/package-lock.json": npm_lock(lodash("4.17.15", resolved=False))},
            tools({"osv-scanner": 0}),
        ),
        2,
        "tool-gates: refused (undecided):",
        "web/package-lock.json: node_modules/lodash has a version and no resolved URL",
    )


def pr54_zizmor_obeys_no_tree_config():
    """A zizmor.yml and an inline ignore in the tree cannot silence a zizmor finding."""
    bad = WORKFLOW.replace("on: push", "on: pull_request_target # zizmor: ignore")
    return expect(
        tool_gate(
            "zizmor",
            {
                ".github/workflows/ci.yml": bad,
                "zizmor.yml": "rules:\n  dangerous-triggers:\n    ignore:\n      - ci.yml\n",
            },
            tools({"zizmor": 13}, "error[dangerous-triggers]"),
        ),
        1,
        "tool-gates: zizmor refused the workflows (exit 13)",
    )


def pr54_actionlint_obeys_no_tree_config():
    """An actionlint.yaml in the tree cannot silence an actionlint finding."""
    return expect(
        tool_gate(
            "actionlint",
            {
                ".github/workflows/ci.yml": WORKFLOW.replace(
                    "echo hi", "echo ${{ github.nope }}"
                ),
                ".github/actionlint.yaml": 'paths:\n  .github/workflows/*.yml:\n    ignore:\n      - "is not defined"\n',
            },
            tools({"actionlint": 1}, 'property "nope" is not defined'),
        ),
        1,
        'property "nope" is not defined',
    )


def pr54_osv_obeys_no_tree_config():
    """An osv-scanner.toml beside a lockfile cannot silence an osv finding."""
    return expect(
        tool_gate(
            "osv",
            {
                "web/package-lock.json": npm_lock(lodash("4.17.15")),
                "web/osv-scanner.toml": '[[PackageOverrides]]\nname = "lodash"\necosystem = "npm"\nignore = true\n',
            },
            tools({"osv-scanner": 1}, "GHSA-35jh-r3h4-6jhm"),
        ),
        1,
        "GHSA-35jh-r3h4-6jhm",
    )


def pr61_gitignored_lockfile_scanned():
    """A lockfile a .gitignore names is still put to the scanner."""
    return expect(
        tool_gate(
            "osv",
            {
                "web/package-lock.json": npm_lock(lodash("4.17.15")),
                ".gitignore": "web/\n",
            },
            tools({"osv-scanner": 1}, "GHSA-35jh-r3h4-6jhm"),
        ),
        1,
        "GHSA-35jh-r3h4-6jhm",
    )


def pr61_no_packages_with_a_lockfile_undecided():
    """The scanner's "no packages found" on a tree that holds a lockfile is not a pass."""
    return expect(
        tool_gate("osv", {"web/Cargo.lock": "x"}, tools({"osv-scanner": 128})),
        2,
        "tool-gates: refused (undecided):",
        "web/Cargo.lock",
    )


def pr61_lockfile_v1_version_only_undecided():
    """A lockfile v1 dependency, nested, with a version and no resolved URL."""
    entry = {**lodash("4.18.1"), "dependencies": {"b": {"version": "1.0.0"}}}
    return expect(
        tool_gate(
            "osv", {"package-lock.json": npm_lock_v1(entry)}, tools({"osv-scanner": 0})
        ),
        2,
        "dependencies > lodash > b has a version and no resolved URL",
    )


# --- the protected surface: run/permissions-diff.py ------------------------------------

PERMISSIONS_DIFF = RUN / "permissions-diff.py"
DECISION = "an automatic change may not land them: size the change L and open a class-2 decision"
PD_WORKFLOW = """name: ci
on: [pull_request]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""
FLOW_JOB = """name: ci
on: [pull_request]
permissions:
  contents: read
jobs:
  build: {runs-on: ubuntu-latest, steps: [{run: echo hi}]}
"""
ANCHORED = """name: ci
on: [pull_request]
env:
  GRANT: &grant
    contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    permissions: *grant
"""
SHELL_STAR = """name: ci
on: [pull_request]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: pick
        run: |
          case "$1" in
            a) echo a ;;
            *) echo other ;;
          esac
      - run: echo hi
"""
CLAUDE_GO = """package claude

// Egress is every host a boxed run may reach through its proxy.
var Egress = []string{"api.anthropic.com"}

func timeout() int { return 30 }
"""


def registry(rows):
    return "".join(
        f'[[gate]]\nid = "{gid}"\nsurface = "{surface}"\nred = "echo red"\n\n'
        for gid, surface in rows
    )


ROWS = [
    ("secret-scan", "Security"),
    ("workflow-zizmor", "Infra and CI config"),
    ("review-verifier", "Code review"),
    ("lint", "PR checks"),
]
PD_BASE = {
    "README.md": "A fixture.\n",
    ".ci/gate-registry.toml": registry(ROWS),
    ".github/workflows/ci.yml": PD_WORKFLOW,
    ".github/workflows/flow.yml": FLOW_JOB,
    ".github/workflows/anchored.yml": ANCHORED,
    ".github/workflows/star.yml": SHELL_STAR,
    "internal/adapter/claude/claude.go": CLAUDE_GO,
}


def protected(change, rc, needle):
    """permissions-diff over `change` on PD_BASE; a refusal must carry the decision line."""
    got = run(PYTHON, PERMISSIONS_DIFF, "--base", "base", cwd=repo(PD_BASE, change))
    return expect(got, rc, needle, *((DECISION,) if rc == 1 else ()))


def pr54_tool_policy_change_protected():
    """An ignore added to the gates-owned osv-scanner policy is a protected change."""
    return protected(
        {"policy/osv-scanner.toml": '[[IgnoredVulns]]\nid = "GHSA-35jh-r3h4-6jhm"\n'},
        1,
        "permissions-diff: policy/osv-scanner.toml changes the tool rows' ignore lists",
    )


def pr57_quoted_permissions_key_named():
    """A job-level permissions key spelt in quotes, which the block reader does not parse."""
    return protected(
        {
            ".github/workflows/ci.yml": PD_WORKFLOW.replace(
                "    runs-on: ubuntu-latest\n",
                '    runs-on: ubuntu-latest\n    "permissions": write-all\n',
            )
        },
        1,
        ".github/workflows/ci.yml changes a workflow token's permissions: line 8 holds `permissions` outside a plain permissions: key",
    )


def pr57_flow_mapping_permissions_named():
    """A flow-style job mapping that gains a permissions grant."""
    return protected(
        {
            ".github/workflows/flow.yml": FLOW_JOB.replace(
                "runs-on: ubuntu-latest,",
                "runs-on: ubuntu-latest, permissions: write-all,",
            )
        },
        1,
        ".github/workflows/flow.yml changes a workflow token's permissions",
    )


def pr57_anchored_grant_named():
    """An edit to the mapping a YAML anchor lends to a job's permissions."""
    return protected(
        {
            ".github/workflows/anchored.yml": ANCHORED.replace(
                "contents: read", "contents: write"
            )
        },
        1,
        "the file uses a YAML anchor or alias (base line 4), so any edit may change a token's permissions",
    )


def pr57_security_row_off_security_surface_protected():
    """Deleting a security row the gate proof registers under another surface."""
    return protected(
        {
            ".ci/gate-registry.toml": registry(
                r for r in ROWS if r[0] != "workflow-zizmor"
            )
        },
        1,
        "changes the gate proof's security rows: row 'workflow-zizmor' deleted",
    )


def pr62_review_row_protected():
    """Deleting the review verifier's row from the gate proof."""
    return protected(
        {
            ".ci/gate-registry.toml": registry(
                r for r in ROWS if r[0] != "review-verifier"
            )
        },
        1,
        "changes the gate proof's security rows: row 'review-verifier' deleted",
    )


def pr62_review_sandbox_protected():
    """A change to the review agent that confines the verifier's reproduction."""
    return protected(
        {"run/review.py": "CONFINE = False\n"},
        1,
        "permissions-diff: run/review.py changes the review agent and its verifier's sandbox",
    )


def pr62_egress_host_protected():
    """A host added to a boxed run's egress allowlist outside its declaration."""
    return protected(
        {
            "internal/adapter/claude/claude.go": CLAUDE_GO
            + '\nfunc init() { Egress = append(Egress, "example.com") }\n'
        },
        1,
        "changes the hosts a boxed run's proxy allows: a line naming Egress changed",
    )


def pr62_hook_script_protected():
    """A Claude Code hook script added to the tree."""
    return protected(
        {".claude/hooks/x.sh": "exit 0\n"},
        1,
        "permissions-diff: .claude/hooks/x.sh changes a Claude Code hook script",
    )


def pr67_alias_after_run_block_named():
    """An alias right after a run block ends is still read as one."""
    return protected(
        {
            ".github/workflows/star.yml": SHELL_STAR.replace(
                "          esac\n", "          esac\n        env: *e\n"
            )
        },
        1,
        "the file uses a YAML anchor or alias (line 15)",
    )


def pr67_shell_star_in_run_block_not_alias():
    """A shell `*)` inside a run block is not an alias, so an unrelated step is no finding."""
    return protected(
        {
            ".github/workflows/star.yml": SHELL_STAR.replace(
                "      - run: echo hi\n",
                "      - run: echo hi\n      - run: echo bye\n",
            )
        },
        0,
        "",
    )


# --- the licence row: run/licence.py -------------------------------------------------

LICENCE = RUN / "licence.py"
POLICY = """allowed = ["MIT", "Apache-2.0", "ISC"]
review = ["MPL-2.0", "LGPL-2.1-or-later"]
refused = ["GPL-3.0-only", "AGPL-3.0-only", "GPL-3.0-or-later"]
vendored = ["vendor/**"]
"""
PNPM9 = """lockfileVersion: '9.0'

importers:

  .:
    dependencies:
      gpl-lib:
        specifier: ^1.0.0
        version: 1.0.0

packages:

  gpl-lib@1.0.0:
    resolution: {integrity: sha512-BBBB}

snapshots:

  gpl-lib@1.0.0: {}
"""


def licence(files, policy=POLICY):
    here = tree({".github/licence-policy.toml": policy, **files})
    run("git", "init", "-q", cwd=here)
    # node_modules is installed, never tracked: the row refuses a tree that supplies it.
    (here / ".git/info").mkdir(exist_ok=True)
    (here / ".git/info/exclude").write_text("node_modules/\n")
    run("git", "add", "-A", cwd=here)
    return run(PYTHON, LICENCE, here, cwd=here, env={**ENV, "TMPDIR": str(fresh())})


def npm_licences(**licences):
    packages = {"": {"name": "app", "version": "1.0.0", "license": "UNLICENSED"}}
    for name, lic in licences.items():
        packages[f"node_modules/{name}"] = {"version": "1.0.0", "license": lic}
    return json.dumps({"name": "app", "lockfileVersion": 3, "packages": packages})


def pr58_lower_case_licence_id_refused():
    """A refused licence spelt in lower case is still refused."""
    return expect(
        licence({"package-lock.json": npm_licences(gpl="gpl-3.0-only", ok="mit")}),
        1,
        "npm:gpl 1.0.0: gpl-3.0-only is refused",
    )


def pr58_reviewed_entry_accepts_only_its_expression():
    """A [reviewed] acceptance of MPL-2.0 does not cover the dependency's new licence."""
    policy = (
        POLICY
        + '[reviewed]\n"npm:mpl" = { accepts = "MPL-2.0", reason = "used unmodified" }\n'
    )
    return expect(
        licence({"package-lock.json": npm_licences(mpl="LGPL-2.1-or-later")}, policy),
        1,
        "[reviewed] accepts MPL-2.0, not LGPL-2.1-or-later",
    )


def pr58_pnpm_decoy_manifest_not_read():
    """A package.json elsewhere in node_modules claiming a GPL package is MIT."""
    store = "web/node_modules/.pnpm/gpl-lib@1.0.0/node_modules/gpl-lib/package.json"
    return expect(
        licence(
            {
                "web/pnpm-lock.yaml": PNPM9,
                store: json.dumps(
                    {
                        "name": "gpl-lib",
                        "version": "1.0.0",
                        "license": "GPL-3.0-or-later",
                    }
                ),
                "web/node_modules/decoy/package.json": json.dumps(
                    {"name": "gpl-lib", "version": "1.0.0", "license": "MIT"}
                ),
            }
        ),
        1,
        "npm:gpl-lib 1.0.0: GPL-3.0-or-later is refused",
    )


def pr58_pnpm_manifest_outside_tree_not_read():
    """A pnpm store entry that is a symlink out of the tree is not read."""
    outside = fresh()
    put(
        outside,
        {
            "node_modules/gpl-lib/package.json": json.dumps(
                {"name": "gpl-lib", "version": "1.0.0", "license": "MIT"}
            )
        },
    )
    return expect(
        licence(
            {
                "web/pnpm-lock.yaml": PNPM9,
                "web/node_modules/.pnpm/gpl-lib@1.0.0": Link(outside),
            }
        ),
        1,
        "npm:gpl-lib 1.0.0: not installed, so no package.json to read",
    )


# --- the change rows: run/change-gates.py, fakes.py, slop-rules.py, mutation.py -------

CHANGE_GATES = RUN / "change-gates.py"
TABLE = "CREATE TABLE waitlist (email TEXT NOT NULL, product TEXT NOT NULL);\n"
MIGRATE = '[[migrations]]\nid = "d1"\ndir = "migrations"\n{extra}\n'


def contract(body):
    return f'[[contract]]\nid = "cli"\nsnapshot = "skills/cli/SKILL.md"\n{body}\n'


def change_gate(row, base, change, env=None):
    here = repo(base, change)
    return run(PYTHON, CHANGE_GATES, row, "--base", "base", here, cwd=here, env=env)


def pr59_migrations_declaration_read_at_base():
    """A change that moves the declared migrations directory cannot unwatch an edit."""
    return expect(
        change_gate(
            "migrations",
            {
                ".github/changes.toml": MIGRATE.format(extra=""),
                "migrations/0001_a.sql": TABLE,
            },
            {
                ".github/changes.toml": MIGRATE.format(extra="").replace(
                    'dir = "migrations"', 'dir = "elsewhere"'
                ),
                "migrations/0001_a.sql": TABLE + "-- tweak\n",
            },
        ),
        1,
        "migrations: d1: migrations/0001_a.sql was edited",
    )


def pr59_contract_command_read_at_base():
    """A change that rewrites the contract's declared command cannot match its own snapshot."""
    return expect(
        change_gate(
            "contracts",
            {
                ".github/changes.toml": contract('command = "echo usage: cli --new"'),
                "skills/cli/SKILL.md": "usage: cli\n",
            },
            {".github/changes.toml": contract('command = "echo usage: cli"')},
        ),
        1,
        "contracts: cli: the output of 'echo usage: cli --new' differs",
    )


def pr59_snapshot_symlink_out_of_tree_refused():
    """A contract snapshot replaced by a symlink to a file outside the tree."""
    outside = fresh() / "snapshot.md"
    outside.write_text("usage: cli\n")
    return expect(
        change_gate(
            "contracts",
            {
                ".github/changes.toml": contract('command = "echo usage: cli"'),
                "skills/cli/SKILL.md": "usage: cli\n",
            },
            {"skills/cli/SKILL.md": Link(outside)},
        ),
        2,
        "refused (outside-tree): skills/cli/SKILL.md resolves outside the tree",
    )


def pr59_declared_command_gets_no_credentials():
    """A declared command is repository code, so it sees no token from the runner."""
    got = change_gate(
        "contracts",
        {
            ".github/changes.toml": contract(
                'command = \'printf "%s" "${GATES_FIXTURE_TOKEN:-}"\''
            ),
            "skills/cli/SKILL.md": "",
        },
        {"README.md": "x\n"},
        env={**ENV, "GATES_FIXTURE_TOKEN": PLANTED},
    )
    return expect(got, 0, "every declared contract matches its snapshot")


def fake_entry(recorded):
    return (
        '[[fake]]\nid = "gh"\npaths = ["tests/**"]\nnames = ["gh"]\n'
        'stands_for = "the GitHub REST API, through the gh CLI"\nreal = "sync.sh"\n'
        f'real_name = "gh"\ncompare = "tests/compare-gh.sh"\nrecorded = {recorded}\n'
    )


def pr59_fake_freshness_read_at_base():
    """A change that re-dates a stale fake's recording cannot pass the stale row."""
    here = repo(
        {".github/fakes.toml": fake_entry("2026-10-01"), "sync.sh": "gh api repos\n"},
        {".github/fakes.toml": fake_entry("2026-10-19")},
    )
    return expect(
        run(
            PYTHON,
            RUN / "fakes.py",
            "stale",
            "--base",
            "base",
            "--today",
            "2026-10-19",
            here,
            cwd=here,
        ),
        1,
        "gh: recorded 2026-10-01, 18 days ago, past its 7-day freshness",
    )


def pr59_quoted_file_name_judged():
    """A changed file whose name holds a quote is still judged by the slop rows."""
    here = repo({"README.md": "x\n"}, {'say "hi".css': ".a { z-index: 9999; }\n"})
    return expect(
        run(PYTHON, RUN / "slop-rules.py", "z-index", "--base", "base", here, cwd=here),
        1,
        'slop-rules: z-index: say "hi".css:1:',
    )


def pr59_mutation_run_testing_nothing_refused():
    """Added code whose mutation run tests no mutant is not a pass."""
    here = fresh()
    got = run(
        "bash",
        ROOT / ".ci/fixtures/mutation/build.sh",
        "--stub",
        "typescript",
        "strong",
        cwd=here,
    )
    if got[0]:
        return f"the mutation fixture did not build: {got[1]}"
    run("git", "tag", "-f", "base", cwd=here)
    with (here / "src/math.ts").open("a") as f:
        f.write("export const LIMIT = 10;\n")
    run("git", "add", "-A", cwd=here)
    run("git", "commit", "-qm", "add a constant", cwd=here)
    env = {**ENV, "PATH": f"{here / '.stub'}:{ENV['PATH']}"}
    return expect(
        run(
            PYTHON,
            RUN / "mutation.py",
            "typescript",
            "--base",
            "base",
            here,
            cwd=workdir,
            env=env,
        ),
        2,
        "mutation: refused (nothing-mutated):",
    )


# --- the review row: run/review.py ---------------------------------------------------

REVIEW = RUN / "review.py"
REVIEW_FIXTURES = ROOT / ".ci/fixtures/review"
REVIEW_ENV = {
    **ENV,
    "ANTHROPIC_API_KEY": PLANTED,
    "GH_TOKEN": PLANTED,
    # macOS only: names the interpreter review.py re-runs as the one the suite runs
    # as, not the opt symlink a sandbox can refuse to exec (see PYTHON above).
    "PYTHONEXECUTABLE": PYTHON,
}
NO_SANDBOX = "the reproducing command's sandbox would not start"


class Review:
    """A review fixture: the floor-division change, its stub claude and a verdict store."""

    def __init__(self):
        self.dir = fresh()
        self.repo, self.store = self.dir / "repo", self.dir / "store"
        self.repo.mkdir()
        rc, out = run(
            "bash",
            REVIEW_FIXTURES / "build.sh",
            "--replies",
            "reproduced",
            "floor",
            cwd=self.repo,
            env=REVIEW_ENV,
        )
        if rc:
            raise RuntimeError(f"the review fixture did not build: {out}")
        self.stub = self.repo / ".stub"

    def reply(self, seat, answer, model="claude-sonnet-5-5"):
        env = json.loads(
            (REVIEW_FIXTURES / "replies/reproduced/finder.json").read_text()
        )
        env["result"] = json.dumps(answer)
        env["modelUsage"] = {model: env["modelUsage"]["claude-sonnet-5-5"]}
        (self.stub / "replies" / f"{seat}.json").write_text(json.dumps(env))

    def review(self, *which, **over):
        env = {**REVIEW_ENV, "PATH": f"{self.stub}:{REVIEW_ENV['PATH']}", **over}
        return run(
            PYTHON,
            REVIEW,
            *(which or ("--gate", "code-review")),
            "--base",
            "base",
            "--author",
            "claude-opus-5-5",
            "--store",
            self.store,
            cwd=self.repo,
            env=env,
        )

    def verifier_saw(self):
        log = self.stub / "calls.jsonl"
        calls = log.read_text().splitlines() if log.exists() else []
        return json.loads(calls[-1])["prompt"] if calls else ""


def probed(probe):
    """(fixture, review result, what the verifier was shown) for a finding reproduced by `probe`."""
    f = Review()
    f.reply(
        "finder",
        {
            "findings": [
                {
                    "severity": "error",
                    "path": "calc.py",
                    "line": 5,
                    "quote": "    return part // whole * 100",
                    "claim": "floor division truncates every share below 100% to 0",
                    "reproduce": probe(f),
                }
            ]
        },
    )
    f.reply("verifier-F1", {"reproduced": False, "reason": "no"}, "claude-opus-5-5")
    result = f.review()
    return f, result, f.verifier_saw()


def attempt(command, marker):
    """A probe printing `marker=open` when `command` succeeds, else `marker=denied`."""
    # The quotes split each verdict word, so the command text shown to the
    # verifier never contains the verdict it prints.
    return f'{command} && echo "{marker}=""open" || echo "{marker}=""denied"; exit 1'


def denied(result, seen, marker):
    """Why the probe was not confined: denied inside the sandbox, or never run outside it.

    Where the sandbox cannot start (inside another one, say) the fix refuses the
    review as an error rather than run the command unconfined, and that is the
    refusal this accepts; .ci/test-review.py requires the sandbox itself.
    """
    rc, out = result
    if rc == 2 and NO_SANDBOX in out:
        if f"{marker}=open" in seen:
            return f"the probe ran: {seen[-300:]}"
        return None
    why = expect(result, 0, "downgraded")
    if not why and f"{marker}=denied" not in seen:
        why = f"the reproducing command was not denied {marker}: {seen[-300:]}"
    return why


def pr60_reproduction_cannot_write_store():
    """A finding's reproducing command cannot append a forged pass to the verdict store."""
    f, result, seen = probed(
        lambda f: attempt(f'echo forged >> "{f.store}/records.jsonl"', "store")
    )
    records = f.store / "records.jsonl"
    if records.exists() and "forged" in records.read_text().splitlines():
        return "the reproducing command wrote to the verdict store"
    return denied(result, seen, "store")


def pr60_reproduction_has_no_network():
    """A finding's reproducing command cannot open a connection."""
    try:
        server = socket.create_server(("127.0.0.1", 0))
    except OSError:
        # This process may not listen (inside another sandbox, say), so nothing
        # would answer the probe: only a review that refused to run it proves anything.
        server = None
    port = server.getsockname()[1] if server else 1
    connect = f"import socket; socket.create_connection(('127.0.0.1', {port}), 5)"
    try:
        _, result, seen = probed(lambda f: attempt(f'python3 -c "{connect}"', "net"))
    finally:
        if server:
            server.close()
    if not server and NO_SANDBOX not in result[1]:
        return (
            f"no listener could be opened and the reproduction ran: {result[1][-300:]}"
        )
    return denied(result, seen, "net")


def pr60_unconfirmed_trivial_claim_errors():
    """A change declared trivial with no router to confirm it is an error, not a pass."""
    f = Review()
    # The tools the row runs, and no claude: a PATH entry this process cannot list
    # may still hold one, so the PATH is built rather than filtered.
    tools = [shutil.which(t) for t in ("git", "bash", "tar")]
    path = os.pathsep.join(
        dict.fromkeys(
            str(Path(t).parent)
            for t in (PYTHON, *tools, "/usr/bin/true", "/bin/sh")
            if t and not shutil.which("claude", path=str(Path(t).parent))
        )
    )
    return expect(
        f.review("--class", "trivial", PATH=path),
        2,
        "review: code-review: error: not convened: the change is declared trivial"
        " and no router answer confirmed it",
    )


def pr60_reply_naming_no_model_errors():
    """A seat's reply that names no model is an error, not an independent review."""
    f = Review()
    f.reply("finder", {"findings": []})
    path = f.stub / "replies/finder.json"
    env = json.loads(path.read_text())
    del env["modelUsage"]
    path.write_text(json.dumps(env))
    return expect(
        f.review(), 2, "asked claude-sonnet-5-5 but was answered by no named model"
    )


def pr63_reproduction_sees_no_sibling_temp():
    """A finding's reproducing command cannot read another run's temporary directory."""
    sibling = fresh()
    (sibling / "note").write_text(PLANTED)
    _, result, seen = probed(lambda f: attempt(f'cat "{sibling}/note"', "temp"))
    if PLANTED in seen:
        return (
            f"the reproducing command read a sibling temporary directory: {seen[-300:]}"
        )
    return denied(result, seen, "temp")


def pr63_reproduction_reaches_no_local_socket():
    """A finding's reproducing command cannot connect to a local Unix socket."""
    here = fresh()
    # Bound and reached by a relative name: a long TMPDIR overruns a socket path.
    cwd = os.getcwd()
    with socket.socket(socket.AF_UNIX) as server:
        os.chdir(here)
        try:
            server.bind("s")
        finally:
            os.chdir(cwd)
        server.listen()
        connect = "import socket; socket.socket(socket.AF_UNIX).connect('s')"
        _, result, seen = probed(
            lambda f: attempt(f'cd "{here}" && python3 -c "{connect}"', "sock")
        )
    return denied(result, seen, "sock")


# --- the gate runner: run/gate.py ----------------------------------------------------

GATE = RUN / "gate.py"
FORGE = """k=$(python3 "$GATE_PY" key test)
printf '{"schema": 1, "gate": "test", "head": "%s", "verdict": "pass", "key": "%s", "reused_from": null, "confined": true}\\n' "$(git rev-parse HEAD)" "$k" >> "$STORE/records.jsonl" || true
"""
PLANT = """[[gate]]
id = "plant"
inputs = ["**"]
run = '''
{plant}'''

[[gate]]
id = "test"
inputs = ["**"]
run = 'echo run >> "$RUNS/test"; exit 1'
"""


class Runner:
    """A repository whose `plant` row runs `plant` before the always-failing `test` row."""

    def __init__(self, plant, **env):
        self.dir = fresh()
        self.repo, self.store, self.runs = (
            self.dir / n for n in ("repo", "store", "runs")
        )
        # The store exists before the first run, so a row's write to it can land.
        for d in (self.runs, self.store):
            d.mkdir()
        self.env = {
            **ENV,
            "RUNS": str(self.runs),
            "GATE_PY": str(GATE),
            "STORE": str(self.store),
            **env,
        }
        run("git", "init", "-q", "--template=", "-b", "main", self.repo, cwd=self.dir)
        put(self.repo, {"README.md": "A fixture.\n"})
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD")
        put(self.repo, {".github/gates.toml": PLANT.format(plant=plant)})
        self.git("add", "-A")
        self.git("commit", "-qm", "rows")

    def git(self, *args):
        return run("git", *args, cwd=self.repo, env=self.env)[1].strip()

    def gate(self, *args, store=None):
        return run(
            PYTHON,
            GATE,
            *args,
            "--base",
            self.base,
            "--store",
            store or self.store,
            cwd=self.repo,
            env=self.env,
        )

    def runs_of(self, gid):
        path = self.runs / gid
        return len(path.read_text().splitlines()) if path.exists() else 0


def pr64_row_cannot_plant_a_pass():
    """A row that appends a pass for another row to the verdict store gets it never reused."""
    f = Runner(FORGE)
    for n in (1, 2):
        rc, out = f.gate("run")
        if f.runs_of("test") != n or "pass test" in out:
            return f"run {n}: a pass a row planted for test was reused ({f.runs_of('test')} runs, exit {rc}): {out.strip()}"
    return expect(f.gate("fold"), 1, "FAIL test: fail: exit 1")


def pr64_row_cannot_repoint_the_store():
    """A row that repoints the store's symlink leaves the run undecided and recording nothing."""
    f = Runner('ln -sfn "$OTHER" "$LINK"\n')
    first, other, link = f.dir / "first", f.dir / "other", f.dir / "link"
    first.mkdir()
    other.mkdir()
    link.symlink_to(first)
    f.env.update(OTHER=str(other), LINK=str(link), STORE=str(link))
    why = expect(
        f.gate("run", store=link), 2, "is not the store it was when plant started"
    )
    if not why and any(p.is_file() for p in (*first.iterdir(), *other.iterdir())):
        why = "a run whose row repointed the store recorded at the new path"
    return why


CASES = [
    pr54_version_only_npm_entry_undecided,
    pr54_zizmor_obeys_no_tree_config,
    pr54_actionlint_obeys_no_tree_config,
    pr54_osv_obeys_no_tree_config,
    pr54_tool_policy_change_protected,
    pr57_quoted_permissions_key_named,
    pr57_flow_mapping_permissions_named,
    pr57_anchored_grant_named,
    pr57_security_row_off_security_surface_protected,
    pr58_lower_case_licence_id_refused,
    pr58_reviewed_entry_accepts_only_its_expression,
    pr58_pnpm_decoy_manifest_not_read,
    pr58_pnpm_manifest_outside_tree_not_read,
    pr59_migrations_declaration_read_at_base,
    pr59_contract_command_read_at_base,
    pr59_snapshot_symlink_out_of_tree_refused,
    pr59_declared_command_gets_no_credentials,
    pr59_fake_freshness_read_at_base,
    pr59_quoted_file_name_judged,
    pr59_mutation_run_testing_nothing_refused,
    pr60_reproduction_cannot_write_store,
    pr60_reproduction_has_no_network,
    pr60_unconfirmed_trivial_claim_errors,
    pr60_reply_naming_no_model_errors,
    pr61_gitignored_lockfile_scanned,
    pr61_no_packages_with_a_lockfile_undecided,
    pr61_lockfile_v1_version_only_undecided,
    pr62_review_row_protected,
    pr62_review_sandbox_protected,
    pr62_egress_host_protected,
    pr62_hook_script_protected,
    pr63_reproduction_sees_no_sibling_temp,
    pr63_reproduction_reaches_no_local_socket,
    pr64_row_cannot_plant_a_pass,
    pr64_row_cannot_repoint_the_store,
    pr67_alias_after_run_block_named,
    pr67_shell_star_in_run_block_not_alias,
]


def main():
    failed = 0
    for case in CASES:
        name = case.__name__.replace("_", " ", 1).replace("_", "-")
        try:
            why = case()
        except Exception as e:  # a crashed case is a failed one, named
            why = f"crashed: {e!r}"
        if why:
            failed += 1
            print(f"FAIL {name}: {case.__doc__}\n  {why}")
        else:
            print(f"ok   {name}: {case.__doc__}")
    print(f"test-p3a-findings: {len(CASES) - failed} of {len(CASES)} cases passed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
