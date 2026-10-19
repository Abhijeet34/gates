#!/usr/bin/env python3
"""Regression tests for run/fakes.py, and its scoped shadow rows under run/gate.py.

    .ci/test-fakes.py

Each case writes a small tree (or a repository with a base and a change), runs one
row and requires its exit status and, for a finding, a line of its output. The
near misses matter as much as the hits: a lint that fires on a hook a test makes
executable, on an HTTP client's `.patch()` or on a `>` inside a printf format
would cost more false alarms than it prevents.
"""

import datetime
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/fakes.py"
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
TODAY = "2026-10-19"


def run(*args, cwd, env=ENV):
    proc = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def put(here, files):
    for name, text in files.items():
        path = here / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def tree(files):
    here = Path(tempfile.mkdtemp(dir=workdir))
    put(here, files)
    return here


def repo(base_files, change_files, deleted=()):
    """A repository with `base_files` committed and tagged `base`, then `change_files` on top."""
    here = Path(tempfile.mkdtemp(dir=workdir))
    run("git", "init", "-q", "--template=", "-b", "main", cwd=here)
    put(here, base_files)
    run("git", "add", "-A", cwd=here)
    run("git", "commit", "-qm", "base", cwd=here)
    run("git", "tag", "base", cwd=here)
    put(here, change_files)
    for name in deleted:
        (here / name).unlink()
    run("git", "add", "-A", cwd=here)
    run("git", "commit", "-qm", "change", cwd=here)
    return here


def judge(name, rc, out, want, finding):
    if rc != want:
        return f"{name}: exit {rc}, wanted {want}:\n{out}"
    if finding and finding not in out:
        return f"{name}: output does not carry {finding!r}:\n{out}"
    if want == 0 and "fakes: " + "refused" in out:
        return f"{name}: refused instead of passing:\n{out}"
    return None


def sweep(name, row, files, want, finding=None, *extra):
    here = tree(files)
    rc, out = run(sys.executable, str(GATE), row, *extra, str(here), cwd=here)
    return judge(name, rc, out, want, finding)


def since(name, base_files, change_files, want, finding=None, deleted=()):
    here = repo(base_files, change_files, deleted)
    rc, out = run(
        sys.executable, str(GATE), "undeclared", "--base", "base", str(here), cwd=here
    )
    return judge(name, rc, out, want, finding)


def entry(fid="gh", **keys):
    """One [[fake]] table; a key given as None is left out."""
    row = {
        "id": f'"{fid}"',
        "paths": '["tests/**"]',
        "names": '["gh"]',
        "stands_for": '"the GitHub REST API, through the gh CLI"',
        "real": '"sync.sh"',
        "real_name": '"gh"',
        "compare": '"tests/compare-gh.sh"',
        "recorded": "2026-10-15",
    }
    row.update(keys)
    return "[[fake]]\n" + "".join(f"{k} = {v}\n" for k, v in row.items() if v) + "\n"


DECLARED = {".github/fakes.toml": entry(), "sync.sh": "gh api repos\n"}
SHIM = (
    'bin=$(mktemp -d)\nprintf "#!/bin/sh\\nexit 0\\n" > "$bin/{name}"\n'
    'chmod +x "$bin/{name}"\nPATH="$bin:$PATH" ./sync.sh\n'
)
CONFIG = ".github/fakes.toml"

SWEEPS = [
    # --- undeclared: shell ---
    (
        "a shell test's PATH shim of git, with only gh declared",
        "undeclared",
        {**DECLARED, "tests/test-sync.sh": SHIM.format(name="git")},
        1,
        f"tests/test-sync.sh:2: the stand-in program git is not declared in {CONFIG}",
    ),
    (
        "the declared gh shim",
        "undeclared",
        {**DECLARED, "tests/test-sync.sh": SHIM.format(name="gh")},
        0,
    ),
    (
        "a declared name outside the declared paths is not declared",
        "undeclared",
        {**DECLARED, "other/test-sync.sh": SHIM.format(name="gh")},
        1,
        "other/test-sync.sh:2: the stand-in program gh",
    ),
    (
        "an executable put on PATH, written from stdin rather than a #! literal",
        "undeclared",
        {
            "tests/test-a.sh": (
                'stub=$(mktemp -d)\ncp ../bin/x "$stub/gitleaks"\n'
                'chmod +x "$stub/gitleaks"\nexport STUB_PATH="$stub:$PATH"\n'
            )
        },
        1,
        "tests/test-a.sh:3: the stand-in program gitleaks",
    ),
    (
        "a *_BIN seam pointed at a script the test made executable",
        "undeclared",
        {
            "tests/test-a.sh": (
                't=$(mktemp -d)\ncp ./x.sh "$t/crew"\nchmod 755 "$t/crew"\n'
                'CREW_STATE_BIN="$t/crew" ./run.sh\n'
            )
        },
        1,
        "tests/test-a.sh:3: the stand-in program crew",
    ),
    (
        "a hook under test made executable is no double",
        "undeclared",
        {
            "tests/test-hook.sh": (
                "cp ../hooks/pre-push .githooks/pre-push\n"
                "chmod +x .githooks/pre-push\ngit push origin main\n"
            )
        },
        0,
    ),
    (
        "a stub function in a shell test",
        "undeclared",
        {"tests/test-a.sh": 'stub_gitleaks() { cat > "$1"; }\n'},
        1,
        "tests/test-a.sh:1: the hand fake stub_gitleaks",
    ),
    (
        "a heredoc program, its name held in a variable",
        "undeclared",
        {
            "tests/test-a.sh": (
                'ASKPASS="$TMP/askpass-stub"\ncat > "$ASKPASS" <<\'EOF\'\n'
                "#!/bin/sh\nexit 0\nEOF\n"
            )
        },
        1,
        "tests/test-a.sh:2: the stand-in program askpass-stub",
    ),
    (
        "a > inside a printf format is not the redirect, and a continued line names it",
        "undeclared",
        {
            "tests/test-a.sh": (
                "printf '#!/bin/sh\\n: > \"%s/blocked\"\\n' \\\n"
                '    "$TMP" > "$BIN/date"\n'
            )
        },
        1,
        "tests/test-a.sh:1: the stand-in program date",
    ),
    (
        "a program written by a command held in a string is not seen",
        "undeclared",
        {"tests/test-a.sh": "expect 'printf \"#!/bin/sh\\n\" > bin/gh'\n"},
        0,
    ),
    (
        "a commented-out mock is not one",
        "undeclared",
        {"tests/test_a.py": '# patch("pkg.fetch")\nx = 1\n'},
        0,
    ),
    # --- undeclared: Python ---
    (
        "python: unittest.mock's patch, named by its target",
        "undeclared",
        {"tests/test_a.py": '@patch("pkg.fetch.get")\ndef test_a(m):\n    pass\n'},
        1,
        "tests/test_a.py:1: the mock pkg.fetch.get",
    ),
    (
        "python: an HTTP client's .patch() is no mock",
        "undeclared",
        {"tests/test_a.py": 'def test_a():\n    requests.patch("https://x.invalid")\n'},
        0,
    ),
    (
        "python: monkeypatch with no literal first argument is named by the call",
        "undeclared",
        {
            "tests/test_a.py": "def test_a(monkeypatch):\n    monkeypatch.setattr(m, 'x', 1)\n"
        },
        1,
        "tests/test_a.py:2: the mock monkeypatch.setattr",
    ),
    (
        "python: a Fake class, and a name that only starts with the word is not one",
        "undeclared",
        {"tests/test_a.py": "class Fakery:\n    pass\n\nclass FakeClient:\n    pass\n"},
        1,
        "tests/test_a.py:4: the hand fake FakeClient",
    ),
    (
        "python: a program written through a variable that spells its name",
        "undeclared",
        {
            "tests/test_a.py": (
                'gh = bin_dir / "gh"\ngh.write_text("#!/bin/sh\\nexit 0\\n")\n'
            )
        },
        1,
        "tests/test_a.py:2: the stand-in program gh",
    ),
    (
        "python: a conftest.py is a test file",
        "undeclared",
        {"conftest.py": "class StubStore:\n    pass\n"},
        1,
        "conftest.py:1: the hand fake StubStore",
    ),
    (
        "python: a fake in a fixtures directory is not a test's",
        "undeclared",
        {"tests/fixtures/fake_gh.py": "class FakeGh:\n    pass\n"},
        0,
    ),
    (
        "python: a vendored tree is not judged",
        "undeclared",
        {"node_modules/x/test_a.py": '@patch("a.b")\ndef test_a():\n    pass\n'},
        0,
    ),
    # --- undeclared: TypeScript and JavaScript ---
    (
        "typescript: vi.mock of a local module, with a vendor SDK declared",
        "undeclared",
        {
            CONFIG: entry("sdk", paths='["test/**"]', names='["@anthropic-ai/sdk"]'),
            "sync.sh": "gh\n",
            "test/run.test.ts": 'vi.mock("node:child_process");\n',
        },
        1,
        "test/run.test.ts:1: the mock node:child_process",
    ),
    (
        "typescript: the declared vendor SDK mock",
        "undeclared",
        {
            CONFIG: entry("sdk", paths='["test/**"]', names='["@anthropic-ai/sdk"]'),
            "sync.sh": "gh\n",
            "test/run.test.ts": 'vi.mock("@anthropic-ai/sdk");\n',
        },
        0,
    ),
    (
        "javascript: a const bound to a fake",
        "undeclared",
        {"test/a.test.js": "const fakeGh = (out) => ({ run: () => out });\n"},
        1,
        "test/a.test.js:1: the hand fake fakeGh",
    ),
    (
        "javascript: a program written into a loop's directory is named by its variable",
        "undeclared",
        {
            "test/a.test.js": (
                'for (const command of ["open"]) {\n'
                '  writeFileSync(join(dir, command), "#!/bin/sh\\n");\n}\n'
            )
        },
        1,
        "test/a.test.js:2: the stand-in program command",
    ),
    (
        "javascript: a compared literal is not the program's name",
        "undeclared",
        {
            "test/a.test.js": (
                'writeFileSync(file, process.platform === "win32" ? "@echo off" : "#!/bin/sh\\n");\n'
            )
        },
        1,
        "test/a.test.js:1: the stand-in program file",
    ),
    # --- undeclared: Go ---
    (
        "go: a fake type in a _test.go file",
        "undeclared",
        {"store_test.go": "package store\n\ntype fakeStore struct{}\n"},
        1,
        "store_test.go:3: the hand fake fakeStore",
    ),
    (
        "go: the same type outside a test file is not judged",
        "undeclared",
        {"store.go": "package store\n\ntype fakeStore struct{}\n"},
        0,
    ),
    (
        "go: testify's mock.Mock",
        "undeclared",
        {"store_test.go": "type store struct {\n\tmock.Mock\n}\n"},
        1,
        "store_test.go:2: the mock mock.Mock",
    ),
    (
        "go: a program written with os.WriteFile",
        "undeclared",
        {
            "run_test.go": (
                'func TestA(t *testing.T) {\n\tos.WriteFile(filepath.Join(dir, "node"), '
                '[]byte("#!/bin/sh\\n"), 0o700)\n}\n'
            )
        },
        1,
        "run_test.go:2: the stand-in program node",
    ),
    (
        "go: a check that output carries #! writes nothing",
        "undeclared",
        {
            "run_test.go": 'func TestA(t *testing.T) {\n\tstrings.Contains(string(b), "#!/bin/sh")\n}\n'
        },
        0,
    ),
    # --- undeclared: Rust ---
    (
        "rust: a Fake struct in the #[cfg(test)] module",
        "undeclared",
        {
            "src/lib.rs": "pub struct Clock;\n\n#[cfg(test)]\nmod tests {\n    struct FakeClock;\n}\n"
        },
        1,
        "src/lib.rs:5: the hand fake FakeClock",
    ),
    (
        "rust: the same struct above #[cfg(test)] is not a test's",
        "undeclared",
        {"src/lib.rs": "pub struct FakeClock;\n\n#[cfg(test)]\nmod tests {\n}\n"},
        0,
    ),
    (
        "rust: mockall's automock",
        "undeclared",
        {"tests/api.rs": "#[automock]\ntrait Api {}\n"},
        1,
        "tests/api.rs:1: the mock #[automock]",
    ),
    (
        "no declaration and no double",
        "undeclared",
        {"tests/test_a.py": "def test_a():\n    assert 1 + 1 == 2\n"},
        0,
        "every test double is declared (0 seen, 0 declared)",
    ),
    # --- the declaration ---
    (
        "a declaration that does not parse",
        "undeclared",
        {CONFIG: "[[fake]\n"},
        2,
        "fakes: refused (bad-declaration)",
    ),
    (
        "a misspelt optional key is refused, not defaulted",
        "stale",
        {CONFIG: entry(max_age="30")},
        2,
        "unknown ['max_age']",
        "--today",
        TODAY,
    ),
    (
        "a fake with no compare command",
        "stale",
        {CONFIG: entry(compare=None)},
        2,
        "missing ['compare']",
        "--today",
        TODAY,
    ),
    (
        "a quoted recorded date",
        "stale",
        {CONFIG: entry(recorded='"2026-10-15"')},
        2,
        "recorded must be a bare TOML date",
        "--today",
        TODAY,
    ),
    (
        "two fakes with one id",
        "counterpart",
        {CONFIG: entry() + entry()},
        2,
        "a second [[fake]] has this id",
    ),
    # --- counterpart ---
    (
        "the real file is gone",
        "counterpart",
        {CONFIG: entry(), "tests/test-sync.sh": "x\n"},
        1,
        "fakes: counterpart: gh: sync.sh does not exist, so the fake stands for nothing",
    ),
    (
        "the real file no longer names the boundary",
        "counterpart",
        {
            CONFIG: entry(),
            "sync.sh": "curl https://x.invalid\n",
            "tests/test-sync.sh": "x\n",
        },
        1,
        "fakes: counterpart: gh: sync.sh no longer names gh",
    ),
    (
        "gh-axi is not gh",
        "counterpart",
        {CONFIG: entry(), "sync.sh": "gh-axi api\n", "tests/test-sync.sh": "x\n"},
        1,
        "sync.sh no longer names gh",
    ),
    (
        "the fake's own files are gone",
        "counterpart",
        {CONFIG: entry(), "sync.sh": "gh api\n"},
        1,
        "fakes: counterpart: gh: no file matches its paths",
    ),
    (
        "a fake with its real counterpart",
        "counterpart",
        {CONFIG: entry(), "sync.sh": "gh api\n", "tests/test-sync.sh": "x\n"},
        0,
        "every declared fake has its real counterpart (1 declared)",
    ),
    (
        "no declaration file declares nothing to check",
        "counterpart",
        {"sync.sh": "gh api\n"},
        0,
    ),
    # --- stale ---
    (
        "a fake recorded 18 days ago, past its 7-day freshness",
        "stale",
        {CONFIG: entry(recorded="2026-10-01")},
        1,
        "fakes: stale: gh: recorded 2026-10-01, 18 days ago, past its 7-day freshness;"
        " re-run 'tests/compare-gh.sh' and move recorded",
        "--today",
        TODAY,
    ),
    (
        "a fake recorded exactly at its freshness",
        "stale",
        {CONFIG: entry(recorded="2026-10-12")},
        0,
        None,
        "--today",
        TODAY,
    ),
    (
        "a fake recorded one day past its freshness",
        "stale",
        {CONFIG: entry(recorded="2026-10-11")},
        1,
        "gh: recorded 2026-10-11, 8 days ago",
        "--today",
        TODAY,
    ),
    (
        "a fake recorded 8 days ago with a 14-day freshness",
        "stale",
        {CONFIG: entry(recorded="2026-10-11", max_age_days="14")},
        0,
        None,
        "--today",
        TODAY,
    ),
    (
        "a fake recorded after today",
        "stale",
        {CONFIG: entry(recorded="2026-12-01")},
        1,
        "fakes: stale: gh: recorded 2026-12-01 is after today, 2026-10-19",
        "--today",
        TODAY,
    ),
    (
        "--base belongs to undeclared only",
        "stale",
        {CONFIG: entry()},
        2,
        "--base applies to undeclared only",
        "--base",
        "HEAD",
    ),
]

SINCE = [
    (
        "a double already on the base is not the change's",
        {"tests/test_a.py": '@patch("a.b")\ndef test_a():\n    pass\n'},
        {"tests/test_a.py": '@patch("a.b")\ndef test_a():\n    pass\n\nX = 1\n'},
        0,
    ),
    (
        "a double the change adds",
        {"tests/test_a.py": "def test_a():\n    pass\n"},
        {
            "tests/test_a.py": 'def test_a():\n    pass\n\n@patch("a.b")\ndef test_b():\n    pass\n'
        },
        1,
        "tests/test_a.py:4: the mock a.b",
    ),
    (
        "deleting a declaration sweeps every test file",
        {
            CONFIG: entry(names='["gh"]'),
            "sync.sh": "gh\n",
            "tests/test-sync.sh": SHIM.format(name="gh"),
        },
        {CONFIG: entry("other", names='["claude"]')},
        1,
        "tests/test-sync.sh:2: the stand-in program gh",
    ),
    (
        "a tracked vendored tree is not judged",
        {"README.md": "x\n"},
        {"vendor/x/test_a.py": '@patch("a.b")\ndef test_a():\n    pass\n'},
        0,
    ),
    (
        "a declaration the change keeps sweeps nothing more",
        {
            CONFIG: entry(),
            "sync.sh": "gh\n",
            "tests/test-sync.sh": SHIM.format(name="gh"),
            "tests/test_b.py": "x = 1\n",
        },
        {"tests/test_b.py": "x = 2\n"},
        0,
    ),
]


def default_today():
    """With no --today the row reads today in UTC, so a fake recorded today is fresh."""
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    here = tree({CONFIG: entry(recorded=today)})
    rc, out = run(sys.executable, str(GATE), "stale", str(here), cwd=here)
    return judge("a fake recorded today, with no --today", rc, out, 0, None)


def without_siblings():
    """The script copied alone cannot load the script it reads, which is no finding."""
    lone = Path(tempfile.mkdtemp(dir=workdir)) / "run"
    lone.mkdir()
    shutil.copy(GATE, lone / GATE.name)
    shutil.copy(RUNNER, lone / RUNNER.name)
    here = tree({"tests/test_a.py": '@patch("a.b")\n'})
    rc, out = run(
        sys.executable, str(lone / GATE.name), "undeclared", str(here), cwd=here
    )
    if rc != 2 or "refused (tool-error)" not in out:
        return (
            f"a missing slop-rules.py: exit {rc}, wanted 2 refused (tool-error):\n{out}"
        )
    return None


ROWS = """
[[gate]]
id = "fakes-{row}"
inputs = {inputs}
scoped = true
{tools}
run = 'python3 "$GATES_DIR/run/fakes.py" {row}{args}'
shadow_since = {since}
"""
# What each row judges: the globs a caller would give it as `inputs`.
INPUTS = {
    "undeclared": (
        '["tests/**", ".github/fakes.toml"]',
        "tools = { base = 'git merge-base \"$BASE_SHA\" HEAD' }",
        ' --base "$BASE_SHA"',
    ),
    "counterpart": ('["tests/**", "sync.sh", ".github/fakes.toml"]', "", ""),
    "stale": (
        '["tests/**", "sync.sh", ".github/fakes.toml"]',
        "tools = { today = 'date -u +%F' }",
        "",
    ),
}


def under_the_runner():
    """Scoped rows stay out of an unrelated change, and a red one is shadow, not a block."""
    date = datetime.datetime.now(datetime.UTC).date().isoformat()
    registry = "\n".join(
        ROWS.format(row=r, inputs=i, tools=t, args=a, since=date)
        for r, (i, t, a) in INPUTS.items()
    )
    base = {".github/gates.toml": registry, "assets/logo.txt": "a\n"}
    red = {
        CONFIG: entry(recorded="2026-01-01"),
        "sync.sh": "curl\n",
        "tests/test-sync.sh": SHIM.format(name="git"),
    }
    env = {**ENV, "GATES_DIR": str(ROOT), "BASE_SHA": "base"}
    here = repo(base, red)
    store = Path(tempfile.mkdtemp(dir=workdir)) / "store"
    args = ("--base", "base", "--store", str(store))
    run_rc, _ = run(sys.executable, str(RUNNER), "run", *args, cwd=here, env=env)
    fold_rc, out = run(sys.executable, str(RUNNER), "fold", *args, cwd=here, env=env)
    wanted = [f"shadow fakes-{r}: fail: exit 1 (in shadow since" for r in INPUTS]
    if run_rc != 0 or fold_rc != 0 or any(w not in out for w in wanted):
        return f"red rows in their shadow window: run {run_rc}, fold {fold_rc}:\n{out}"
    other = repo(base, {"assets/logo.txt": "b\n"})
    chosen = run(
        sys.executable, str(RUNNER), "select", "--base", "base", cwd=other, env=env
    )[1]
    leaked = [r for r in INPUTS if f"fakes-{r}\t" in chosen]
    if leaked:
        return f"a change to assets/logo.txt selected scoped rows {leaked}:\n{chosen}"
    return None


def main():
    failures = [sweep(*case) for case in SWEEPS]
    failures += [since(*case) for case in SINCE]
    failures += [default_today(), without_siblings(), under_the_runner()]
    failures = [f for f in failures if f]
    total = len(SWEEPS) + len(SINCE) + 3
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    print(f"test-fakes: {total - len(failures)} of {total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
