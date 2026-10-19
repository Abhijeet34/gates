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
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
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
        "a fresh fake whose last compare found drift",
        "stale",
        {
            CONFIG: entry(
                drift='"2026-10-18: tests/repo.json (GET /repos/o/r): removed .name (string)"'
            )
        },
        1,
        "fakes: stale: gh: its last compare found drift (2026-10-18: tests/repo.json"
        " (GET /repos/o/r): removed .name (string)); update the recording or the code,"
        " then re-run 'tests/compare-gh.sh'",
        "--today",
        TODAY,
    ),
    (
        "an empty drift is refused, not read as none",
        "stale",
        {CONFIG: entry(drift='""')},
        2,
        "drift must be a non-empty string",
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


# --- compare: a GitHub fake against a reply from the recorded server ---------

SERVER = ROOT / ".ci/fixtures/gh-recorded-server.py"
REPO = {
    "id": 1,
    "full_name": "o/r",
    "description": None,
    "topics": [],
    "owner": {"login": "o", "type": "User"},
}


def github(fid="gh", replays=(("/repos/o/r", "tests/fixtures/repo.json"),), **keys):
    """One [[fake]] with a [[fake.github]] per (request, recording[, status])."""
    tables = "".join(
        f'[[fake.github]]\nrequest = "{r[0]}"\nrecording = "{r[1]}"\n'
        + (f"status = {r[2]}\n" if len(r) > 2 else "")
        + "\n"
        for r in replays
    )
    keys.setdefault("compare", f'"fakes.py compare --id {fid}"')
    return entry(fid, **keys) + tables


def serve(here, replies, *extra, env=ENV, api="$GH_RECORDED_API"):
    """compare run on `here` against the recorded server; (exit, output, requests logged)."""
    spool = Path(tempfile.mkdtemp(dir=workdir))
    (spool / "replies.json").write_text(json.dumps(replies))
    log = spool / "log.jsonl"
    command = f'g="$1"; shift; exec "$0" "$g" compare --api "{api}" "$@"'
    rc, out = run(
        sys.executable,
        str(SERVER),
        str(spool / "replies.json"),
        "--log",
        str(log),
        "--",
        "sh",
        "-c",
        command,
        sys.executable,
        str(GATE),
        *extra,
        str(here),
        cwd=here,
        env=env,
    )
    logged = (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )
    return rc, out, logged


def reply(body, status=200, headers=None):
    return {"status": status, "body": body, "headers": headers or {}}


def recorded(body=REPO, config=None):
    return {
        CONFIG: config or github(),
        "tests/fixtures/repo.json": body if isinstance(body, str) else json.dumps(body),
    }


COMPARES = [
    (
        "new values in the same shape: an id, a null filled in, an empty list filled",
        recorded(),
        {"/repos/o/r": reply({**REPO, "id": 9, "description": "d", "topics": ["a"]})},
        0,
        "fakes: every declared GitHub fake has the shape of its real reply (1 replies, 1 fakes)",
    ),
    (
        "a field removed, a field added and a nested field retyped, in key order",
        recorded(),
        {
            "/repos/o/r": reply(
                {
                    "id": 1,
                    "node_id": "R_1",
                    "description": None,
                    "topics": [],
                    "owner": {"login": "o", "type": 3},
                }
            )
        },
        1,
        "fakes: compare: gh: tests/fixtures/repo.json (GET /repos/o/r): removed .full_name (string)\n"
        "fakes: compare: gh: tests/fixtures/repo.json (GET /repos/o/r): added .node_id (string)\n"
        "fakes: compare: gh: tests/fixtures/repo.json (GET /repos/o/r): retyped .owner.type from string to number\n"
        "fakes: 3 finding(s)",
    ),
    (
        "a field retyped inside the items of a listed reply",
        recorded(
            [{"number": 1, "labels": [{"name": "bug"}]}, {"number": 2, "labels": []}]
        ),
        {"/repos/o/r": reply([{"number": 3, "labels": [{"name": 7}]}])},
        1,
        "(GET /repos/o/r): retyped .[].labels[].name from string to number",
    ),
    (
        "a reply larger than one read is read whole",
        recorded([{"number": 1, "title": "t"}]),
        {"/repos/o/r": reply([{"number": n, "title": "t" * 64} for n in range(4000)])},
        0,
    ),
    (
        "a key only some items carry is a value, not shape",
        recorded([{"number": 1, "draft": True}, {"number": 2}]),
        {"/repos/o/r": reply([{"number": 3}])},
        0,
    ),
    (
        "a key every recorded item carries that some real items lack",
        recorded([{"number": 1, "draft": True}, {"number": 2, "draft": False}]),
        {"/repos/o/r": reply([{"number": 3, "draft": True}, {"number": 4}])},
        1,
        "(GET /repos/o/r): removed .[].draft from some items (boolean)",
    ),
    (
        "an object retyped as an array",
        recorded(),
        {"/repos/o/r": reply({**REPO, "owner": [1]})},
        1,
        "retyped .owner from object to array",
    ),
    (
        "a key that is not a bare word is quoted",
        recorded({"a b": 1}),
        {"/repos/o/r": reply({"a b": "1"})},
        1,
        'retyped .["a b"] from number to string',
    ),
    (
        "another status is drift and its body is not compared",
        recorded(),
        {},
        1,
        "(GET /repos/o/r): status 200 recorded, 404 now",
    ),
    (
        "a recorded 404 that still answers 404",
        recorded(
            {"message": "Not Found", "documentation_url": "u"},
            github(replays=(("/repos/o/gone", "tests/fixtures/repo.json", 404),)),
        ),
        {"/repos/o/gone": reply({"message": "Gone", "documentation_url": "v"}, 404)},
        0,
    ),
    (
        "a redirect is reported as its status, not followed",
        recorded(),
        {
            "/repos/o/r": reply({}, 301, {"Location": "/repositories/1"}),
            "/repositories/1": reply(REPO),
        },
        1,
        "status 200 recorded, 301 now",
    ),
    (
        "403 with no requests left is a rate limit, not drift",
        recorded(),
        {
            "/repos/o/r": reply(
                {"message": "rate limit"},
                403,
                {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1760000000"},
            )
        },
        2,
        "fakes: refused (rate-limited): GET /repos/o/r: status 403, resets at 1760000000",
    ),
    (
        "429 is a rate limit",
        recorded(),
        {"/repos/o/r": reply({"message": "slow down"}, 429)},
        2,
        "fakes: refused (rate-limited)",
    ),
    (
        "403 with requests left is drift",
        recorded(),
        {
            "/repos/o/r": reply(
                {"message": "Forbidden"}, 403, {"x-ratelimit-remaining": "59"}
            )
        },
        1,
        "status 200 recorded, 403 now",
    ),
    (
        "a 5xx could not decide",
        recorded(),
        {"/repos/o/r": reply({"message": "bad gateway"}, 502)},
        2,
        "fakes: refused (network): GET /repos/o/r: status 502",
    ),
    (
        "a reply that is not JSON could not decide",
        recorded(),
        {"/repos/o/r": reply("<html>captive portal</html>")},
        2,
        "fakes: refused (network): GET /repos/o/r: the reply is not JSON",
    ),
    (
        "a missing recording",
        {CONFIG: github()},
        {"/repos/o/r": reply(REPO)},
        2,
        "fakes: refused (bad-declaration): .github/fakes.toml: [[fake]] 'gh': recording"
        " tests/fixtures/repo.json does not exist",
    ),
    (
        "a recording that is not JSON",
        recorded("{not json"),
        {"/repos/o/r": reply(REPO)},
        2,
        "recording tests/fixtures/repo.json is not JSON",
    ),
    (
        "a declared method is refused: only GET is ever sent",
        recorded(
            config=github(
                replays=(("POST /repos/o/r/issues", "tests/fixtures/repo.json"),)
            )
        ),
        {},
        2,
        "request 'POST /repos/o/r/issues' must be a GET path such as /repos/o/r",
    ),
    (
        "a request that could name another host",
        recorded(
            config=github(replays=(("//evil.invalid/x", "tests/fixtures/repo.json"),))
        ),
        {},
        2,
        "request '//evil.invalid/x' must be a GET path",
    ),
    (
        "a request that climbs out of its path",
        recorded(
            config=github(replays=(("/repos/../user", "tests/fixtures/repo.json"),))
        ),
        {},
        2,
        "request '/repos/../user' must be a GET path",
    ),
    (
        "a recording outside the tree",
        recorded(config=github(replays=(("/repos/o/r", "../repo.json"),))),
        {},
        2,
        "recording '../repo.json' must be a path inside the tree",
    ),
    (
        "a quoted status",
        recorded(
            config=github(
                replays=(("/repos/o/r", "tests/fixtures/repo.json", '"200"'),)
            )
        ),
        {},
        2,
        "status '200' must be an HTTP status",
    ),
    (
        "an unknown replay key is refused",
        recorded(config=github() + 'method = "GET"\n'),
        {},
        2,
        "[[fake.github]] missing [], unknown ['method']",
    ),
    (
        "no GitHub fake declared compares nothing, which is no pass",
        {CONFIG: entry()},
        {},
        2,
        "fakes: refused (nothing-to-compare): no [[fake]] with a [[fake.github]] replay declared",
    ),
    (
        "--id naming no GitHub fake",
        recorded(),
        {},
        2,
        "no [[fake]] with a [[fake.github]] replay with id 'other'",
        "--id",
        "other",
    ),
    (
        "--api must be an http origin",
        recorded(),
        {},
        2,
        "must be an http or https origin",
        "--api",
        "file:///etc",
    ),
]


def compares():
    failures = []
    for name, files, replies, want, *rest in COMPARES:
        finding, *extra = rest or [None]
        here = tree(files)
        rc, out, _ = serve(here, replies, *extra)
        failures.append(judge(name, rc, out, want, finding))
    return failures


def unreachable():
    """Nothing listening is a network refusal, never drift and never a pass."""
    here = tree(recorded())
    rc, out = run(
        sys.executable,
        str(GATE),
        "compare",
        "--api",
        "http://127.0.0.1:9",
        str(here),
        cwd=here,
    )
    return judge(
        "nothing listening", rc, out, 2, "fakes: refused (network): GET /repos/o/r:"
    )


def only_get_and_no_token():
    """Every request is a GET, and the CI job's token goes to api.github.com only."""
    here = tree(recorded())
    env = {**ENV, "GITHUB_ACTIONS": "true", "GITHUB_TOKEN": "fixture-token"}
    rc, out, logged = serve(here, {"/repos/o/r": reply(REPO)}, env=env)
    wanted = [{"method": "GET", "path": "/repos/o/r", "authorization": None}]
    if rc != 0 or logged != wanted:
        return f"requests to another origin: exit {rc}, logged {logged}:\n{out}"
    return None


def kept_reply():
    """--out keeps the fresh reply byte for byte at its recording's path."""
    here = tree(recorded())
    out_dir = Path(tempfile.mkdtemp(dir=workdir))
    body = {**REPO, "id": 42}
    rc, out, _ = serve(here, {"/repos/o/r": reply(body)}, "--out", str(out_dir))
    kept = out_dir / "tests/fixtures/repo.json"
    if rc != 0 or not kept.is_file() or json.loads(kept.read_text()) != body:
        return f"--out: exit {rc}, kept {kept.is_file()}:\n{out}"
    return None


def first_recording():
    """A replay with no recording yet is refused, and --out still keeps the reply to record."""
    here = tree({CONFIG: github()})
    out_dir = Path(tempfile.mkdtemp(dir=workdir))
    rc, out, _ = serve(here, {"/repos/o/r": reply(REPO)}, "--out", str(out_dir))
    kept = out_dir / "tests/fixtures/repo.json"
    if rc != 2 or "does not exist" not in out or not kept.is_file():
        return (
            f"a first recording through --out: exit {rc}, kept {kept.is_file()}:\n{out}"
        )
    return None


def one_by_id():
    """--id compares one fake and asks for nothing else."""
    config = github() + github(
        "issues", (("/repos/o/r/issues", "tests/fixtures/issues.json"),)
    )
    here = tree({**recorded(config=config), "tests/fixtures/issues.json": "[]"})
    rc, out, logged = serve(here, {"/repos/o/r/issues": reply([])}, "--id", "issues")
    if rc != 0 or [r["path"] for r in logged] != ["/repos/o/r/issues"]:
        return f"--id issues: exit {rc}, logged {logged}:\n{out}"
    return None


def drift_beside_a_refusal():
    """One fake that drifts and one that cannot be fetched: exit 1, and only the drifted one is written."""
    config = github("a", (("/a", "tests/fixtures/repo.json"),)) + github(
        "b", (("/b", "tests/fixtures/repo.json"),)
    )
    here = tree(recorded(config=config))
    before = (here / CONFIG).read_text()
    replies = {"/a": reply({"message": "down"}, 503), "/b": reply({"id": 1})}
    rc, out, _ = serve(here, replies, "--write", "--today", TODAY)
    after = tomllib.loads((here / CONFIG).read_text())["fake"]
    if (
        rc != 1
        or "fakes: refused (network): GET /a: status 503" not in out
        or "fakes: compare: b: tests/fixtures/repo.json (GET /b): removed .full_name"
        not in out
        or "drift" in after[0]
        or not after[1]
        .get("drift", "")
        .startswith(f"{TODAY}: tests/fixtures/repo.json (GET /b)")
    ):
        return f"a drift beside a refusal: exit {rc}:\n{out}\n{before}\n{after}"
    return None


def drift_marks_stale():
    """compare --write gives a drifted fake its drift, stale refuses it, and a match clears it."""
    comment = "# the GitHub fake of sync.sh\n"
    other = entry("claude", names='["claude"]', recorded="2026-10-14")
    config = comment + github(recorded="2026-10-15  # last pass") + other
    here = tree({**recorded(config=config), "sync.sh": "gh api repos\n"})
    drifted = {"/repos/o/r": reply({**REPO, "owner": {"login": "o", "type": 3}})}
    steps = []
    rc, out, _ = serve(here, drifted, "--write", "--today", TODAY)
    steps.append(("compare on drift", rc, 1, out))
    rc, out = run(
        sys.executable, str(GATE), "stale", "--today", TODAY, str(here), cwd=here
    )
    steps.append(("stale after drift", rc, 1, out))
    finding = (
        "fakes: stale: gh: its last compare found drift (2026-10-19: tests/fixtures/repo.json"
        " (GET /repos/o/r): retyped .owner.type from string to number)"
    )
    if finding not in out:
        return f"stale after a drift does not name it:\n{out}"
    (here / "tests/fixtures/repo.json").write_text(
        json.dumps({**REPO, "owner": {"login": "o", "type": 0}})
    )
    rc, out, _ = serve(here, drifted, "--write", "--today", TODAY)
    steps.append(("compare after the recording follows", rc, 0, out))
    rc, out = run(
        sys.executable, str(GATE), "stale", "--today", TODAY, str(here), cwd=here
    )
    steps.append(("stale after the match", rc, 0, out))
    for label, got, want, text in steps:
        if got != want:
            return f"{label}: exit {got}, wanted {want}:\n{text}"
    text = (here / CONFIG).read_text()
    if (
        not text.startswith(comment)
        or "recorded = 2026-10-19  # last pass\n" not in text
        or "drift" in text
        or other not in text
    ):
        return f"--write changed more than recorded and drift:\n{text}"
    return None


def unwritable_layout():
    """A declaration --write cannot edit by line is refused and left as it was."""
    inline = (
        'fake = [{ id = "gh", paths = ["tests/**"], names = ["gh"], stands_for = "s",'
        ' real = "sync.sh", compare = "c", recorded = 2026-10-15,'
        ' github = [{ request = "/repos/o/r", recording = "tests/fixtures/repo.json" }] }]\n'
    )
    here = tree(recorded(config=inline))
    rc, out, _ = serve(here, {"/repos/o/r": reply(REPO)}, "--write", "--today", TODAY)
    if rc != 2 or "refused (tool-error): cannot rewrite [[fake]] 'gh'" not in out:
        return f"an inline declaration under --write: exit {rc}:\n{out}"
    if (here / CONFIG).read_text() != inline:
        return "an inline declaration was rewritten after a refusal"
    return None


def compare_under_the_runner():
    """The caller's compare row: scoped to the declaration and the recordings, red is shadow."""
    date = datetime.datetime.now(datetime.UTC).date().isoformat()
    registry = (
        '[[gate]]\nid = "fakes-compare"\n'
        'inputs = [".github/fakes.toml", "tests/fixtures/**"]\nscoped = true\n'
        'run = \'python3 "$GATES_DIR/run/fakes.py" compare --api "$GH_RECORDED_API"\'\n'
        f"shadow_since = {date}\n"
    )
    base = {".github/gates.toml": registry, "assets/logo.txt": "a\n", **recorded()}
    here = repo(base, {"tests/fixtures/repo.json": json.dumps({**REPO, "id": "1"})})
    store = Path(tempfile.mkdtemp(dir=workdir)) / "store"
    spool = Path(tempfile.mkdtemp(dir=workdir))
    (spool / "replies.json").write_text(json.dumps({"/repos/o/r": reply(REPO)}))
    env = {**ENV, "GATES_DIR": str(ROOT), "BASE_SHA": "base"}
    args = ("--base", "base", "--store", str(store))
    run_rc, ran = run(
        sys.executable,
        str(SERVER),
        str(spool / "replies.json"),
        "--",
        sys.executable,
        str(RUNNER),
        "run",
        *args,
        cwd=here,
        env=env,
    )
    fold_rc, out = run(sys.executable, str(RUNNER), "fold", *args, cwd=here, env=env)
    if run_rc != 0 or fold_rc != 0 or "shadow fakes-compare: fail: exit 1" not in out:
        return f"a hand-edited recording under the runner: run {run_rc}, fold {fold_rc}:\n{ran}\n{out}"
    other = repo(base, {"assets/logo.txt": "b\n"})
    chosen = run(
        sys.executable, str(RUNNER), "select", "--base", "base", cwd=other, env=env
    )[1]
    if "fakes-compare\t" in chosen:
        return f"a change to assets/logo.txt selected fakes-compare:\n{chosen}"
    return None


def main():
    failures = [sweep(*case) for case in SWEEPS]
    failures += [since(*case) for case in SINCE]
    failures += [default_today(), without_siblings(), under_the_runner()]
    failures += compares()
    singles = [
        unreachable,
        only_get_and_no_token,
        kept_reply,
        first_recording,
        one_by_id,
        drift_beside_a_refusal,
        drift_marks_stale,
        unwritable_layout,
        compare_under_the_runner,
    ]
    failures += [case() for case in singles]
    failures = [f for f in failures if f]
    total = len(SWEEPS) + len(SINCE) + 3 + len(COMPARES) + len(singles)
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
