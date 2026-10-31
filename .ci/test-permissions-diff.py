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

[[gate]]
id = "workflow-zizmor"
surface = "Infra and CI config"
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
QUOTED_KEY = WORKFLOW.replace(
    "    runs-on: ubuntu-latest\n",
    '    runs-on: ubuntu-latest\n    "permissions": write-all\n',
)
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
FLOW_JOB = """name: ci
on: [pull_request]
permissions:
  contents: read
jobs:
  build: {runs-on: ubuntu-latest, steps: [{run: echo hi}]}
"""
# A row of each kind the gate proof protects outside the Security surface.
PROTECTED_ROWS = {
    "workflow-policy": "Infra and CI config",
    "dependency-advisories": "Dependencies",
    "review-verifier": "Code review",
    "review-trivial": "Code review",
    "critic-hold": "Code review",
    "lock-imposter": "Gate pinning",
    "lock-drift": "Gate pinning",
    "signature-check": "Pre-push",
    "exposure-scan": "Pre-push",
    "commit-msg": "Pre-commit",
}
GUARDED_REGISTRY = PROOF_REGISTRY + "".join(
    f'\n[[gate]]\nid = "{gid}"\nsurface = "{surface}"\nred = "echo red"\n'
    for gid, surface in PROTECTED_ROWS.items()
)


# Rows naming the files they run: a protected one, and one on a surface the gate leaves alone.
RAN_REGISTRY = """[[gate]]
id = "secret-scan"
surface = "Security"
gates = ["run/secret-scan/tree.sh", ".github/workflows/ci.yml"]
command = "run/secret-scan/verify.sh"
red = "echo red"

[[gate]]
id = "lint"
surface = "PR checks"
gates = ["run/lint.sh"]
command = "run/lint.sh"
red = "echo red"
"""


def without_row(gid):
    rows = GUARDED_REGISTRY.split("\n\n")
    return "\n\n".join(r for r in rows if f'id = "{gid}"' not in r)


CLAUDE_GO = """package claude

// Egress is every host a boxed run may reach through its proxy.
var Egress = []string{"api.anthropic.com"}

func start(stream string) {
\tegress.Start(stream+".egress", Egress...)
}

func timeout() int { return 30 }
"""
DEPBASE_GO = """package repos

var Registries = map[string][]string{
\t"package-lock.json": {"registry.npmjs.org"},
\t"Cargo.lock":        {"index.crates.io", "static.crates.io"},
}

func kinds() int { return len(Registries) }
"""
COMMAND = "---\ndescription: Ship it\nallowed-tools: Bash(git status:*)\n---\nShip the branch.\n"
AGENT = "---\nname: critic\ndescription: Reads.\ntools: Read, Grep\n---\nYou read.\n"
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
    ".github/workflows/anchored.yml": ANCHORED,
    ".github/workflows/star.yml": SHELL_STAR,
    ".github/workflows/flow.yml": FLOW_JOB,
    ".claude/commands/ship.md": COMMAND,
    ".claude/agents/critic.md": AGENT,
    "internal/sandbox/toolchain_test.go": "package sandbox\n",
    "internal/adapter/claude/claude.go": CLAUDE_GO,
    "repos/depbase.go": DEPBASE_GO,
    "egress/egress_test.go": "package egress\n",
}
RAN_BASE = {
    **BASE,
    ".ci/gate-registry.toml": RAN_REGISTRY,
    "run/secret-scan/tree.sh": "gitleaks dir .\n",
    "run/secret-scan/verify.sh": "test -s report.json\n",
    "run/lint.sh": "ruff check .\n",
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
    # A workflow is read by lines, so a form the block reader does not parse is named.
    (
        "a quoted job-level permissions key added",
        {".github/workflows/ci.yml": QUOTED_KEY},
        1,
        "permissions-diff: .github/workflows/ci.yml changes a workflow token's permissions: line 8 holds `permissions` outside a plain permissions: key",
    ),
    (
        "a quoted job-level permissions key removed, a narrowing, is still named",
        {".github/workflows/ci.yml": WORKFLOW},
        1,
        "base line 8 holds `permissions` outside a plain permissions: key",
        "base",
        {**BASE, ".github/workflows/ci.yml": QUOTED_KEY},
    ),
    (
        "an escaped job-level key added",
        {
            ".github/workflows/ci.yml": WORKFLOW.replace(
                "    runs-on: ubuntu-latest\n",
                '    runs-on: ubuntu-latest\n    "permi\\u0073sions": write-all\n',
            )
        },
        1,
        "line 8 spells an escaped key, which the block reader does not parse",
    ),
    (
        "an explicit job-level key",
        {
            ".github/workflows/ci.yml": WORKFLOW.replace(
                "    runs-on: ubuntu-latest\n", "    ? runs-on\n    : ubuntu-latest\n"
            )
        },
        1,
        "line 7 spells an explicit key, which the block reader does not parse",
    ),
    (
        "the mapping an anchor names edited",
        {
            ".github/workflows/anchored.yml": ANCHORED.replace(
                "contents: read", "contents: write"
            )
        },
        1,
        ".github/workflows/anchored.yml changes a workflow token's permissions: the file uses a YAML anchor or alias (base line 4), so any edit may change a token's permissions",
    ),
    (
        "a shell *) in a run block, comment edited",
        {
            ".github/workflows/star.yml": SHELL_STAR.replace(
                "name: ci", "name: ci # edited"
            )
        },
        0,
        "",
    ),
    (
        "a shell *) in a run block, step added",
        {
            ".github/workflows/star.yml": SHELL_STAR.replace(
                "      - run: echo hi\n",
                "      - run: echo hi\n      - run: echo bye\n",
            )
        },
        0,
        "",
    ),
    (
        "a real anchor beside a run block",
        {
            ".github/workflows/star.yml": SHELL_STAR.replace(
                "      - run: echo hi\n",
                "      - run: echo hi\n        env: &e\n          A: b\n",
            )
        },
        1,
        "the file uses a YAML anchor or alias (line 16)",
    ),
    (
        "an alias right after a run block ends",
        {
            ".github/workflows/star.yml": SHELL_STAR.replace(
                "          esac\n", "          esac\n        env: *e\n"
            )
        },
        1,
        "the file uses a YAML anchor or alias (line 15)",
    ),
    (
        "a flow-style job mapping gains permissions",
        {
            ".github/workflows/flow.yml": FLOW_JOB.replace(
                "runs-on: ubuntu-latest,",
                "runs-on: ubuntu-latest, permissions: write-all,",
            )
        },
        1,
        ".github/workflows/flow.yml changes a workflow token's permissions: base line 6 opens a flow mapping, which the block reader does not parse; line 6 holds `permissions` outside a plain permissions: key",
    ),
    (
        "a flow-style job mapping edited",
        {".github/workflows/flow.yml": FLOW_JOB.replace("echo hi", "echo bye")},
        1,
        ".github/workflows/flow.yml changes a workflow token's permissions: base line 6 opens a flow mapping, which the block reader does not parse; line 6 opens a flow mapping",
    ),
    (
        "a quoted step that holds a hash and no key",
        {".github/workflows/ci.yml": WORKFLOW.replace("echo hi", 'echo "a # b"')},
        0,
        "none on the protected surface",
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
        "permissions-diff: policy/osv-scanner.toml changes the tool rows' ignore lists and the relaxation records",
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
        ".ci/gate-registry.toml changes the gate proof's security rows: row 'secret-scan' changed (red)",
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
        "a security row registered under another surface deleted",
        {".ci/gate-registry.toml": PROOF_REGISTRY.rsplit("\n\n", 1)[0] + "\n"},
        1,
        ".ci/gate-registry.toml changes the gate proof's security rows: row 'workflow-zizmor' deleted",
    ),
    (
        "the tool rows' pins",
        {"run/tool-gates.py": "PINS = {}\n"},
        1,
        "permissions-diff: run/tool-gates.py changes the tool rows' pins and flags",
    ),
    (
        "the pull request text pattern set",
        {"run/pr-text.py": "RULES = []\n"},
        1,
        "permissions-diff: run/pr-text.py changes the pull request text pattern set",
    ),
    (
        "the licence row",
        {"run/licence.py": "ALLOWED = None\n"},
        1,
        "permissions-diff: run/licence.py changes the licence row",
    ),
    (
        "the weakening guard",
        {"run/weakening.py": "KINDS = {}\n"},
        1,
        "permissions-diff: run/weakening.py changes the weakening guard",
    ),
    (
        "the relaxation records' approval check",
        {"run/record-approval.py": "APPROVER = None\n"},
        1,
        "permissions-diff: run/record-approval.py changes the relaxation records' approval check",
    ),
    (
        "a relaxation record",
        {"policy/relaxations.toml": "[[relaxation]]\n"},
        1,
        "permissions-diff: policy/relaxations.toml changes the tool rows' ignore lists and the relaxation records",
    ),
    (
        "the licence policy",
        {".github/licence-policy.toml": '[allowed]\nids = ["GPL-3.0-only"]\n'},
        1,
        "permissions-diff: .github/licence-policy.toml changes the licence policy and its declarations",
    ),
    (
        "a zizmor configuration in the tree",
        {".github/zizmor.yml": "rules: {}\n"},
        1,
        "permissions-diff: .github/zizmor.yml changes a tool's in-tree configuration",
    ),
    (
        "an actionlint configuration in the tree",
        {".github/actionlint.yaml": "paths: {}\n"},
        1,
        "permissions-diff: .github/actionlint.yaml changes a tool's in-tree configuration",
    ),
    (
        "an osv-scanner configuration beside a lockfile",
        {"web/osv-scanner.toml": "[[IgnoredVulns]]\n"},
        1,
        "permissions-diff: web/osv-scanner.toml changes a tool's in-tree configuration",
    ),
    (
        "an MCP server configuration",
        {".mcp.json": '{"mcpServers": {}}\n'},
        1,
        "permissions-diff: .mcp.json changes an agent's MCP servers",
    ),
    (
        "a command's allowed tools widened",
        {".claude/commands/ship.md": COMMAND.replace("Bash(git status:*)", "Bash(*)")},
        1,
        "permissions-diff: .claude/commands/ship.md changes a command's tool grants: a front matter key that can grant authority changed",
    ),
    (
        "a command's description and body edited",
        {
            ".claude/commands/ship.md": COMMAND.replace(
                "Ship it", "Ship the branch"
            ).replace("Ship the branch.", "Push it.")
        },
        0,
        "none on the protected surface",
    ),
    (
        "an agent's tools widened",
        {".claude/agents/critic.md": AGENT.replace("Read, Grep", "Read, Grep, Bash")},
        1,
        "permissions-diff: .claude/agents/critic.md changes an agent's tool grants",
    ),
    (
        "an agent given a permission mode",
        {
            ".claude/agents/critic.md": AGENT.replace(
                "---\nYou", "permissionMode: bypassPermissions\n---\nYou"
            )
        },
        1,
        ".claude/agents/critic.md changes an agent's tool grants",
    ),
    (
        "an agent's front matter left unclosed",
        {".claude/agents/critic.md": AGENT.replace("---\nYou", "You")},
        1,
        ".claude/agents/critic.md changes an agent's tool grants: .claude/agents/critic.md: its front matter is not closed by a --- line",
    ),
    (
        "a new skill granting tools",
        {
            ".claude/skills/deploy/SKILL.md": "---\nname: deploy\nallowed-tools: Bash\n---\nDeploy.\n"
        },
        1,
        ".claude/skills/deploy/SKILL.md changes a skill's tool grants",
    ),
    (
        "a deployed agent copy's tools widened",
        {
            "system-maintenance/claude-code/config/agents/critic.md": AGENT.replace(
                "Grep", "Bash"
            )
        },
        1,
        "system-maintenance/claude-code/config/agents/critic.md changes an agent's tool grants",
    ),
    (
        "the worker sandbox's toolchain reads",
        {"internal/sandbox/toolchain.go": "package sandbox\n"},
        1,
        "permissions-diff: internal/sandbox/toolchain.go changes what the worker sandbox grants: the source changed",
    ),
    (
        "a worker sandbox test",
        {"internal/sandbox/toolchain_test.go": "package sandbox\n\nfunc init() {}\n"},
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
    *(
        (
            f"the {gid} row deleted from the gate proof",
            {".ci/gate-registry.toml": without_row(gid)},
            1,
            f".ci/gate-registry.toml changes the gate proof's security rows: row '{gid}' deleted",
            "base",
            {**BASE, ".ci/gate-registry.toml": GUARDED_REGISTRY},
        )
        for gid in PROTECTED_ROWS
    ),
    (
        "a Code review row's red fixture edited in the gate proof",
        {
            ".ci/gate-registry.toml": GUARDED_REGISTRY.replace(
                'id = "review-verifier"\nsurface = "Code review"\nred = "echo red"',
                'id = "review-verifier"\nsurface = "Code review"\nred = "true"',
            )
        },
        1,
        "row 'review-verifier' changed (red)",
        "base",
        {**BASE, ".ci/gate-registry.toml": GUARDED_REGISTRY},
    ),
    (
        "the review agent and its verifier",
        {"run/review.py": "CONFINE = False\n"},
        1,
        "permissions-diff: run/review.py changes the review agent and its verifier's sandbox",
    ),
    (
        "the gate runner whose globs this gate matches with",
        {"run/gate.py": "def glob_re(g): pass\n"},
        1,
        "permissions-diff: run/gate.py changes the gate runner and the globs this gate matches with",
    ),
    (
        "the secret scan",
        {"run/secret-scan.sh": "exit 0\n"},
        1,
        "permissions-diff: run/secret-scan.sh changes the secret scan",
    ),
    (
        "a script a security row lists in its gates",
        {"run/secret-scan/tree.sh": "exit 0\n"},
        1,
        "permissions-diff: run/secret-scan/tree.sh changes a file the gate proof's security row 'secret-scan' runs",
        "base",
        RAN_BASE,
    ),
    (
        "the command a security row runs",
        {"run/secret-scan/verify.sh": "exit 0\n"},
        1,
        "permissions-diff: run/secret-scan/verify.sh changes a file the gate proof's security row 'secret-scan' runs",
        "base",
        RAN_BASE,
    ),
    (
        "a security row's workflow edited beside its permissions",
        {
            ".github/workflows/ci.yml": WORKFLOW.replace(
                "run: echo hi", "run: echo skipped"
            )
        },
        1,
        "permissions-diff: .github/workflows/ci.yml changes a file the gate proof's security row 'secret-scan' runs",
        "base",
        RAN_BASE,
    ),
    (
        "a security row's script, dropped from the row in the same change",
        {
            "run/secret-scan/tree.sh": "exit 0\n",
            ".ci/gate-registry.toml": RAN_REGISTRY.replace(
                '"run/secret-scan/tree.sh", ', ""
            ),
        },
        1,
        "permissions-diff: run/secret-scan/tree.sh changes a file the gate proof's security row 'secret-scan' runs",
        "base",
        RAN_BASE,
    ),
    (
        "a script only an unprotected row runs",
        {"run/lint.sh": "exit 0\n"},
        0,
        "permissions-diff: 1 changed path(s), none on the protected surface",
        "base",
        RAN_BASE,
    ),
    (
        "a base gate registry that cannot be read",
        {"README.md": "A fixture, edited.\n"},
        2,
        "permissions-diff: could not decide: .ci/gate-registry.toml cannot be read as TOML",
        "base",
        {**BASE, ".ci/gate-registry.toml": "[[gate]\n"},
    ),
    (
        "the secret scan's rules",
        {".gitleaks.toml": "[allowlist]\npaths = ['.*']\n"},
        1,
        "permissions-diff: .gitleaks.toml changes the secret scan's rules and allowlists",
    ),
    (
        "the workflow policy",
        {".ci/test-workflow-policy.sh": "exit 0\n"},
        1,
        "permissions-diff: .ci/test-workflow-policy.sh changes the workflow policy",
    ),
    (
        "the dependency advisories entry point",
        {"run/dependency-advisories.sh": "exit 0\n"},
        1,
        "permissions-diff: run/dependency-advisories.sh changes the dependency advisories row",
    ),
    (
        "the dependency advisories judge",
        {"run/dependency-advisories/judge.sh": "exit 0\n"},
        1,
        "permissions-diff: run/dependency-advisories/judge.sh changes the dependency advisories row",
    ),
    (
        "a Claude Code hook script added",
        {".claude/hooks/x.sh": "exit 0\n"},
        1,
        "permissions-diff: .claude/hooks/x.sh changes a Claude Code hook script",
    ),
    (
        "a deployed Claude Code hook script",
        {"system-maintenance/claude-code/config/hooks/guard.sh": "exit 0\n"},
        1,
        "changes a Claude Code hook script",
    ),
    (
        "a host added to a boxed run's egress",
        {
            "internal/adapter/claude/claude.go": CLAUDE_GO.replace(
                '"api.anthropic.com"}', '"api.anthropic.com", "example.com"}'
            )
        },
        1,
        "permissions-diff: internal/adapter/claude/claude.go changes the hosts a boxed run's proxy allows: the Egress declaration changed",
    ),
    (
        "a boxed run's egress assigned outside its declaration",
        {
            "internal/adapter/claude/claude.go": CLAUDE_GO
            + '\nfunc init() { Egress = append(Egress, "example.com") }\n'
        },
        1,
        "changes the hosts a boxed run's proxy allows: a line naming Egress changed",
    ),
    (
        "a boxed run's egress moved into a var group",
        {
            "internal/adapter/claude/claude.go": CLAUDE_GO.replace(
                'var Egress = []string{"api.anthropic.com"}',
                'var (\n\tEgress = []string{"api.anthropic.com", "example.com"}\n)',
            )
        },
        1,
        "the Egress declaration changed",
    ),
    (
        "an edit beside a boxed run's egress",
        {"internal/adapter/claude/claude.go": CLAUDE_GO.replace("30", "60")},
        0,
        "none on the protected surface",
    ),
    (
        "a host added to a base build's registries",
        {
            "repos/depbase.go": DEPBASE_GO.replace(
                '"static.crates.io"}', '"static.crates.io", "example.com"}'
            )
        },
        1,
        "permissions-diff: repos/depbase.go changes the hosts a base build's proxy allows: the Registries declaration changed",
    ),
    (
        "an edit beside a base build's registries",
        {"repos/depbase.go": DEPBASE_GO + "\nfunc none() {}\n"},
        0,
        "none on the protected surface",
    ),
    (
        "the egress proxy",
        {"egress/egress.go": "package egress\n"},
        1,
        "permissions-diff: egress/egress.go changes the boxed run's egress proxy: the source changed",
    ),
    (
        "an egress proxy test",
        {"egress/egress_test.go": "package egress\n\nfunc init() {}\n"},
        0,
        "none on the protected surface",
    ),
    (
        "a protected file deleted",
        {".claude/settings.json": None},
        1,
        ".claude/settings.json changes Claude Code settings",
    ),
]


# A branch ruleset as an inline job left it, before the job moved into a called workflow.
RULESET = """{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "rules": [
    { "type": "deletion" },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "checks" },
          { "context": "tests kept" },
          { "context": "secret scan" }
        ]
      }
    }
  ]
}
"""
RENAMED = RULESET.replace('"secret scan"', '"secrets / secret scan"')
RENAME_RECORD = """[[relaxation]]
id = "required-context-follows-called-workflow"
ratified = 2026-10-25
by = "maintainer decision"
kinds = ["context-removed"]
repos = ["Abhijeet34/*"]
context_to = '^[a-z0-9-]+ / {from}$'
"""
MAIN_JSON = ".github/rulesets/main.json"
RULESET_BASE = {**BASE, MAIN_JSON: RULESET}


def ruleset_gate(change, policy, repo="Abhijeet34/fixture", base=RULESET_BASE):
    """The gate with GATES_DIR holding `policy` as its relaxation records (None: unset)."""
    repo_dir = fixture(change, base)
    env = {k: v for k, v in ENV.items() if k not in ("GATES_DIR", "GITHUB_REPOSITORY")}
    if policy is not None:
        gates = repo_dir.parent / f"{repo_dir.name}-gates"
        (gates / "policy").mkdir(parents=True)
        (gates / "policy/relaxations.toml").write_text(policy)
        env["GATES_DIR"] = str(gates)
    args = ["--base", "base"] + (["--repo", repo] if repo else [])
    return run(sys.executable, str(GATE), *args, cwd=repo_dir, env=env)


RULESET_CASES = [
    (
        "a context rename a recorded policy covers",
        {MAIN_JSON: RENAMED},
        RENAME_RECORD,
        "Abhijeet34/fixture",
        0,
        f"permissions-diff: {MAIN_JSON} renames required context 'secret scan' to"
        " 'secrets / secret scan', which matches recorded policy"
        " required-context-follows-called-workflow",
    ),
    (
        "a context rename with no record",
        {MAIN_JSON: RENAMED},
        "",
        "Abhijeet34/fixture",
        1,
        f"permissions-diff: {MAIN_JSON} changes a branch ruleset: required context"
        " 'secret scan' removed, and no recorded policy covers a replacement",
    ),
    (
        "a context removed outright, with the rename record",
        {MAIN_JSON: RULESET.replace(',\n          { "context": "secret scan" }', "")},
        RENAME_RECORD,
        "Abhijeet34/fixture",
        1,
        "required context 'secret scan' removed, and no recorded policy covers a replacement",
    ),
    (
        "a context renamed to one the record's pattern does not match",
        {MAIN_JSON: RULESET.replace('"secret scan"', '"scan"')},
        RENAME_RECORD,
        "Abhijeet34/fixture",
        1,
        "required context 'secret scan' removed, and no recorded policy covers a replacement",
    ),
    (
        "a covered rename beside another ruleset change",
        {MAIN_JSON: RENAMED.replace('"active"', '"disabled"')},
        RENAME_RECORD,
        "Abhijeet34/fixture",
        1,
        f"{MAIN_JSON} changes a branch ruleset: a change beside the required context rename",
    ),
    (
        "a covered rename beside a context added",
        {
            MAIN_JSON: RENAMED.replace(
                '{ "context": "checks" },',
                '{ "context": "checks" },\n          { "context": "lint" },',
            )
        },
        RENAME_RECORD,
        "Abhijeet34/fixture",
        1,
        "a change beside the required context rename",
    ),
    (
        "a context added alone",
        {
            MAIN_JSON: RULESET.replace(
                '"checks" },', '"checks" },\n { "context": "lint" },'
            )
        },
        RENAME_RECORD,
        "Abhijeet34/fixture",
        1,
        "a change other than a required context rename",
    ),
    (
        "a covered rename in a repository outside the record's scope",
        {MAIN_JSON: RENAMED},
        RENAME_RECORD,
        "someone/else",
        1,
        "no recorded policy covers a replacement",
    ),
    (
        "a covered rename with no repository named",
        {MAIN_JSON: RENAMED},
        RENAME_RECORD,
        None,
        1,
        "no recorded policy covers a replacement",
    ),
    (
        "a covered rename with no GATES_DIR",
        {MAIN_JSON: RENAMED},
        None,
        "Abhijeet34/fixture",
        1,
        "no recorded policy covers a replacement",
    ),
    (
        "a non-JSON file beside the rulesets, with the rename record",
        {".github/rulesets/README.md": "The rulesets.\n"},
        RENAME_RECORD,
        "Abhijeet34/fixture",
        1,
        "permissions-diff: .github/rulesets/README.md changes a branch ruleset: any change",
    ),
    (
        "a ruleset that is not JSON",
        {MAIN_JSON: RENAMED + "}"},
        RENAME_RECORD,
        "Abhijeet34/fixture",
        1,
        f"{MAIN_JSON} cannot be read as JSON",
    ),
    (
        "a relaxation file that fails its schema",
        {MAIN_JSON: RENAMED},
        RENAME_RECORD.replace("kinds", "kind"),
        "Abhijeet34/fixture",
        2,
        "could not decide",
    ),
]


def expect_ruleset(name, change, policy, repo, rc, line):
    got_rc, out = ruleset_gate(change, policy, repo)
    if got_rc != rc or line not in out:
        return f"{name}: wanted exit {rc} naming {line!r}, got exit {got_rc}:\n{out}"
    return None


def callers_own_record():
    """A record in the tree under review grants nothing: only GATES_DIR's file is read."""
    base = {**RULESET_BASE, "policy/relaxations.toml": RENAME_RECORD}
    rc, out = ruleset_gate({MAIN_JSON: RENAMED}, "", base=base)
    if rc != 1 or "no recorded policy covers a replacement" not in out:
        return f"a caller's own record: wanted exit 1, got exit {rc}:\n{out}"
    return None


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
    failures += [expect_ruleset(*case) for case in RULESET_CASES]
    failures += [
        f()
        for f in (
            no_base,
            unknown_base,
            merge_base,
            in_shadow,
            established_blocks,
            keyed_by_base,
            callers_own_record,
        )
    ]
    failures = [f for f in failures if f]
    total = len(CASES) + len(RULESET_CASES) + 7
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
