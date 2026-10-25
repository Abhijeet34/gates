#!/usr/bin/env python3
"""Regression tests for run/weakening.py and the record in policy/relaxations.toml.

    .ci/test-weakening.py

Each case commits a base and one change into a fixture repository, runs the guard
on base...HEAD with GATES_DIR set to this checkout (or to a fixture gates holding
a policy of its own), and requires its exit status and a line of its output.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/weakening.py"
REPO = "Abhijeet34/treadling"
SHARED = "Abhijeet34/gates/.github/workflows/shared-secret-scan.yml"
PIN = "3d3c42e5aac5ba805825da76410c181273ba90b1"
LITERAL = r"/^Abhijeet34\/gates\/\.github\/workflows\/shared-[a-z-]+\.yml@main$/"
REFUSED = "weakening: refused:"

RULESET = """{
  "rules": [
    {"type": "required_status_checks", "parameters": {"required_status_checks": [
      {"context": "checks"}, {"context": "secret scan"}
    ]}}
  ]
}
"""
CI = """name: ci
on:
  pull_request:
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - run: npm test
  release:
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-24.04
    steps:
      - run: echo release
  checks:
    name: checks
    needs: [test]
    if: ${{ always() }}
    runs-on: ubuntu-24.04
    steps:
      - run: echo ok
"""
SCAN = """name: secret scan
on:
  pull_request:
jobs:
  secrets:
    name: secret scan
    runs-on: ubuntu-24.04
    steps:
      - run: gitleaks dir .
"""
CALLED = """name: secret scan
on:
  pull_request:
jobs:
  secrets:
    uses: Abhijeet34/gates/.github/workflows/shared-secret-scan.yml@main
"""
SUPPLY_TEST = """import { test } from 'node:test';
import assert from 'node:assert/strict';

for (const ref of refs()) {
  assert.match(ref, /@[0-9a-f]{40}$/);
}
"""
EXEMPTION = f"  if ({LITERAL}.test(ref)) continue\n"
BASE = {
    "README.md": "A fixture.\n",
    ".github/rulesets/main.json": RULESET,
    ".github/workflows/ci.yml": CI,
    ".github/workflows/secret-scan.yml": SCAN,
    "test/architecture/supply-chain.test.ts": SUPPLY_TEST,
    "test/unit/parse.test.ts": "test('parses', () => {});\n",
}


def with_exemption(test=EXEMPTION):
    return SUPPLY_TEST.replace(
        "for (const ref of refs()) {\n", "for (const ref of refs()) {\n" + test
    )


# PR 114's shape: an inline job moved into gates' shared workflow at @main, and the
# architecture test that requires a commit pin taught to skip exactly that ref.
PR114 = {
    ".github/workflows/secret-scan.yml": CALLED,
    "test/architecture/supply-chain.test.ts": with_exemption(),
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
ENV.pop("GITHUB_REPOSITORY", None)


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


def gates_with(policy):
    """A gates checkout whose policy file is `policy`, or none at all when it is None."""
    gates = Path(tempfile.mkdtemp(dir=workdir))
    if policy is not None:
        (gates / "policy").mkdir()
        (gates / "policy/relaxations.toml").write_text(policy)
    return gates


NO_POLICY = ""


def expect(name, change, rc, lines, repo=REPO, base=BASE, gates=ROOT):
    work = fixture(change, base)
    env = {**ENV, "GATES_DIR": str(gates)}
    args = ["--base", "base", "--tests", "test/architecture/**"]
    if repo:
        args += ["--repo", repo]
    got_rc, out = run(sys.executable, str(GATE), *args, cwd=work, env=env)
    lines = [lines] if isinstance(lines, str) else lines
    missing = [line for line in lines if line not in out]
    if got_rc != rc or missing:
        return f"{name}: wanted exit {rc} naming {missing!r}, got exit {got_rc}:\n{out}"
    return None


def record(**over):
    rec = {
        "id": '"r"',
        "ratified": "2026-10-25",
        "by": '"maintainer decision"',
        "kinds": '["moving-ref"]',
        "repos": '["Abhijeet34/*"]',
        "ref": "'^x@main$'",
        **over,
    }
    body = "".join(f"{k} = {v}\n" for k, v in rec.items() if v is not None)
    return "[[relaxation]]\n" + body


MATCHED = "matches recorded policy first-party-shared-at-main"
CASES = [
    (
        "PR 114's shape with no record",
        PR114,
        1,
        [
            f"weakening: moving-ref at .github/workflows/secret-scan.yml:6: {SHARED}@main",
            "weakening: test-exemption at test/architecture/supply-chain.test.ts:5: if (",
            "refused: 2 weakening(s) match no recorded policy",
        ],
        REPO,
        BASE,
        gates_with(NO_POLICY),
    ),
    (
        "PR 114's shape with the record",
        PR114,
        0,
        [
            f"weakening: moving-ref at .github/workflows/secret-scan.yml:6 {MATCHED}",
            f"weakening: test-exemption at test/architecture/supply-chain.test.ts:5 {MATCHED}",
            "weakening: every weakening matches a recorded policy",
        ],
    ),
    (
        "a widened literal",
        {
            **PR114,
            "test/architecture/supply-chain.test.ts": with_exemption(
                r"  if (/^Abhijeet34\/.*@main$/.test(ref)) continue" + "\n"
            ),
        },
        1,
        [
            r"test-exemption at test/architecture/supply-chain.test.ts:5: if (/^Abhijeet34\/.*@main$/.test(ref)) continue",
            "refused: 1 weakening(s)",
        ],
    ),
    (
        "the recorded literal combined with another condition",
        {
            "test/architecture/supply-chain.test.ts": with_exemption(
                f"  if ({LITERAL}.test(ref) || ref.length > 0) continue\n"
            )
        },
        1,
        "test-exemption at test/architecture/supply-chain.test.ts:5:",
    ),
    (
        "a third-party moving ref beside the recorded one",
        {
            **PR114,
            ".github/workflows/ci.yml": CI.replace(
                f"actions/checkout@{PIN} # v7.0.1", "actions/checkout@v7"
            ),
        },
        1,
        [
            "moving-ref at .github/workflows/ci.yml:10: actions/checkout@v7",
            "refused: 1 weakening(s)",
        ],
    ),
    (
        "a repository outside the record's scope",
        PR114,
        1,
        [
            f"moving-ref at .github/workflows/secret-scan.yml:6: {SHARED}@main",
            "refused: 2 weakening(s)",
        ],
        "someone/treadling",
    ),
    (
        "a `# v1` commit pin turned back into @main, with the record",
        {".github/workflows/secret-scan.yml": CALLED},
        1,
        f"moving-ref at .github/workflows/secret-scan.yml:6: {SHARED}@main replaces a commit pin",
        REPO,
        {
            **BASE,
            ".github/workflows/secret-scan.yml": CALLED.replace(
                "@main", f"@{PIN} # v1"
            ),
        },
    ),
    (
        "a caller planting its own policy file",
        {
            **PR114,
            "policy/relaxations.toml": record(
                kinds='["moving-ref", "test-exemption"]',
                repos='["Abhijeet34/*"]',
                ref="'.*'",
            ),
        },
        1,
        "refused: 2 weakening(s)",
        REPO,
        BASE,
        gates_with(NO_POLICY),
    ),
    (
        "an unchanged moving ref and a moved exemption",
        {"README.md": "edited\n"},
        0,
        "weakening: no weakening",
        REPO,
        {
            **BASE,
            ".github/workflows/secret-scan.yml": CALLED,
            "test/architecture/supply-chain.test.ts": with_exemption(),
        },
    ),
    (
        "Node's skip option",
        {
            "test/architecture/supply-chain.test.ts": SUPPLY_TEST
            + "test('pins', { skip: process.platform === 'win32' }, () => {});\n"
        },
        1,
        "test-exemption at test/architecture/supply-chain.test.ts:7: test('pins', { skip:",
    ),
    (
        "Node's skip option after another option",
        {
            "test/architecture/supply-chain.test.ts": SUPPLY_TEST
            + "test('pins', { timeout: 5, skip: true }, () => {});\n"
        },
        1,
        "test-exemption at test/architecture/supply-chain.test.ts:7:",
    ),
    (
        "an it.skip and a Rust #[ignore]",
        {
            "test/architecture/supply-chain.test.ts": SUPPLY_TEST
            + "it.skip('pins', () => {});\n",
            "test/architecture/sandbox.rs": "#[test]\n#[ignore]\nfn denies() {}\n",
        },
        1,
        [
            "test-exemption at test/architecture/sandbox.rs:2: #[ignore]",
            "test-exemption at test/architecture/supply-chain.test.ts:7: it.skip(",
        ],
    ),
    (
        "a skip in a test the row does not name",
        {"test/unit/parse.test.ts": "test.skip('parses', () => {});\n"},
        0,
        "weakening: no weakening",
    ),
    (
        "a skip spelled in a comment",
        {
            "test/architecture/supply-chain.test.ts": SUPPLY_TEST
            + "// we never continue past a bad ref\n"
        },
        0,
        "weakening: no weakening",
    ),
    (
        "a required context removed",
        {
            ".github/rulesets/main.json": RULESET.replace(
                '{"context": "secret scan"}', '{"context": "secrets / secret scan"}'
            )
        },
        1,
        'context-removed at rulesets: {"from": "secret scan", "to": ["secrets / secret scan"]}',
    ),
    (
        "a context rename a recorded policy covers",
        {
            ".github/rulesets/main.json": RULESET.replace(
                '{"context": "secret scan"}', '{"context": "secrets / secret scan"}'
            )
        },
        0,
        "context-removed at rulesets matches recorded policy r",
        REPO,
        BASE,
        gates_with(
            record(
                kinds='["context-removed"]',
                ref=None,
                context_to="'^[a-z0-9-]+ / {from}$'",
            )
        ),
    ),
    (
        "a context removed outright, with a rename policy",
        {
            ".github/rulesets/main.json": RULESET.replace(
                ', {"context": "secret scan"}', ""
            )
        },
        1,
        'context-removed at rulesets: {"from": "secret scan", "to": []}',
        REPO,
        BASE,
        gates_with(
            record(
                kinds='["context-removed"]',
                ref=None,
                context_to="'^[a-z0-9-]+ / {from}$'",
            )
        ),
    ),
    (
        "an unreadable ruleset",
        {".github/rulesets/main.json": RULESET + "}"},
        1,
        "context-removed at .github/rulesets/main.json: the ruleset cannot be read as JSON",
    ),
    (
        "paths-ignore on a workflow a required context runs",
        {
            ".github/workflows/secret-scan.yml": SCAN.replace(
                "  pull_request:\n", "  pull_request:\n    paths-ignore: ['docs/**']\n"
            )
        },
        1,
        "workflow-condition at .github/workflows/secret-scan.yml:4: paths-ignore: paths-ignore: ['docs/**']",
    ),
    (
        "continue-on-error on a job the aggregate needs",
        {
            ".github/workflows/ci.yml": CI.replace(
                "      - run: npm test\n",
                "      - run: npm test\n        continue-on-error: true\n",
            )
        },
        1,
        "workflow-condition at .github/workflows/ci.yml:12: job test: continue-on-error: true",
    ),
    (
        "a job if: on a required job",
        {
            ".github/workflows/secret-scan.yml": SCAN.replace(
                "    name: secret scan\n",
                "    name: secret scan\n    if: github.actor != 'dependabot[bot]'\n",
            )
        },
        1,
        "workflow-condition at .github/workflows/secret-scan.yml:7: job secrets: if: github.actor != 'dependabot[bot]'",
    ),
    (
        "if: always() and conditions on a job no required context names",
        {
            ".github/workflows/ci.yml": CI.replace(
                "    if: ${{ always() }}\n", "    if: always()\n"
            ).replace(
                "      - run: echo release\n",
                "      - run: echo release\n        continue-on-error: true\n",
            )
        },
        0,
        "weakening: no weakening",
    ),
    (
        "a workflow step edited beside an existing job if:",
        {".github/workflows/ci.yml": CI.replace("npm test", "npm run test")},
        0,
        "weakening: no weakening",
    ),
]

SCHEMA = [
    ("an unknown key", record(scope='"all"'), "unknown key ['scope']"),
    ("a bad regex", record(ref="'^x(@main$'"), "bad pattern"),
    ("an unknown kind", record(kinds='["anything"]'), "`kinds` must list"),
    (
        "a matcher no kind uses",
        record(context_to="'{from}'"),
        "`context_to` matches none",
    ),
    ("a missing matcher", record(ref=None), "`ref` missing"),
    ("no ratified date", record(ratified='"soon"'), "`ratified` must be a date"),
    ("a repository glob without an owner", record(repos='["*"]'), "`repos` must list"),
    ("an unknown table", "[grant]\nall = true\n", "unknown table ['grant']"),
]


def schema(name, policy, line):
    """A policy file that breaks the schema stops the guard before it judges anything."""
    return expect(
        f"schema, {name}",
        {"README.md": "edited\n"},
        2,
        ["weakening: could not decide: policy/relaxations.toml", line],
        gates=gates_with(policy),
    )


def undecided():
    failures = [
        expect("no policy file", {}, 2, "could not decide", gates=gates_with(None)),
        expect(
            "no repository",
            {},
            2,
            "no --repo and GITHUB_REPOSITORY is unset",
            repo=None,
        ),
    ]
    work = fixture({})
    env = {k: v for k, v in ENV.items() if k != "GATES_DIR"}
    rc, out = run(
        sys.executable, str(GATE), "--base", "base", "--repo", REPO, cwd=work, env=env
    )
    if rc != 2 or "GATES_DIR is unset" not in out:
        failures.append(f"no GATES_DIR: wanted exit 2, got {rc}:\n{out}")
    return [f for f in failures if f]


def main():
    failures = [expect(*case) for case in CASES]
    failures += [schema(*case) for case in SCHEMA]
    failures += undecided()
    failures = [f for f in failures if f]
    total = len(CASES) + len(SCHEMA) + 3
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    print(f"test-weakening: {total - len(failures)} of {total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
