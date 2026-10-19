#!/usr/bin/env python3
"""Regression tests for run/docs-truth.py, and its four rows in shadow under run/gate.py.

    .ci/test-docs-truth.py

Each case writes a tree, runs one check on it and requires its exit status and,
for a refusal, a line of its output. A file whose text is a tuple is executable.
"""

import datetime
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/docs-truth.py"
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
DASH = "\u2014"


def run(*args, cwd, env=ENV):
    proc = subprocess.run(
        args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    return proc.returncode, proc.stdout + proc.stderr


def tree(files):
    root = Path(tempfile.mkdtemp(dir=workdir))
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, tuple):
            path.write_text(text[0])
            path.chmod(0o755)
        else:
            path.write_text(text)
    return root


def manifest(*rows, tools=()):
    out = f"tools = {list(tools)!r}\n".replace("'", '"')
    for path, owner, reader in rows:
        out += f'\n[[doc]]\npath = "{path}"\nowner = "{owner}"\nreader = "{reader}"\n'
    return out


def doc(*lines, lang="bash"):
    return "# Doc\n\n```" + lang + "\n" + "\n".join(lines) + "\n```\n"


LISTED = manifest(("AGENTS.md", "maintainer", "agents"))
SCRIPT = ("#!/bin/sh\n",)

# (name, check, files, want exit, a line the output must carry)
CASES = [
    (
        "an unlisted AGENTS.md is refused",
        "owner",
        {"AGENTS.md": "x\n"},
        1,
        "AGENTS.md: names no owner and reader",
    ),
    (
        "a listed AGENTS.md passes",
        "owner",
        {"AGENTS.md": "x\n", ".github/docs.toml": LISTED},
        0,
        None,
    ),
    (
        "a nested CLAUDE.md is named by its path",
        "owner",
        {"AGENTS.md": "x\n", "apps/web/CLAUDE.md": "x\n", ".github/docs.toml": LISTED},
        1,
        "apps/web/CLAUDE.md: names no owner and reader",
    ),
    (
        "a reader outside the three is refused",
        "owner",
        {
            "AGENTS.md": "x\n",
            ".github/docs.toml": manifest(("AGENTS.md", "maintainer", "everyone")),
        },
        1,
        "AGENTS.md: reader must be one of",
    ),
    (
        "a blank owner is refused",
        "owner",
        {
            "AGENTS.md": "x\n",
            ".github/docs.toml": manifest(("AGENTS.md", "  ", "agents")),
        },
        1,
        "AGENTS.md: names no owner",
    ),
    (
        "a listed file that is gone is refused",
        "owner",
        {
            "AGENTS.md": "x\n",
            ".github/docs.toml": LISTED + manifest(("docs/gone.md", "m", "clients")),
        },
        1,
        "docs/gone.md: does not exist",
    ),
    (
        "a misspelt key is refused",
        "owner",
        {"AGENTS.md": "x\n", ".github/docs.toml": LISTED.replace("reader", "readers")},
        1,
        "unknown key 'readers'",
    ),
    (
        "a path outside the tree is refused",
        "owner",
        {
            "AGENTS.md": "x\n",
            ".github/docs.toml": LISTED + manifest(("../AGENTS.md", "m", "agents")),
        },
        1,
        "path must be a relative Markdown file inside the tree",
    ),
    (
        "a path listed twice is refused",
        "owner",
        {
            "AGENTS.md": "x\n",
            ".github/docs.toml": LISTED + manifest(("AGENTS.md", "m", "agents")),
        },
        1,
        "AGENTS.md: listed twice",
    ),
    (
        "an unparsable manifest is refused",
        "owner",
        {"AGENTS.md": "x\n", ".github/docs.toml": "[[doc\n"},
        1,
        ".github/docs.toml: cannot be read",
    ),
    (
        "an unlisted README is not judged",
        "owner",
        {"AGENTS.md": "x\n", "README.md": "x\n", ".github/docs.toml": LISTED},
        0,
        None,
    ),
    ("a tree with no instruction file passes", "owner", {"README.md": "x\n"}, 0, None),
    (
        "a command naming a script that is gone is refused",
        "commands",
        {"README.md": doc("echo hi", ".ci/gone.sh check")},
        1,
        "README.md:5: `.ci/gone.sh` does not exist",
    ),
    (
        "a script that is not executable is refused",
        "commands",
        {"README.md": doc("tools/run.sh"), "tools/run.sh": "x\n"},
        1,
        "README.md:4: `tools/run.sh` is not executable",
    ),
    (
        "an executable script passes",
        "commands",
        {"README.md": doc("tools/run.sh --flag"), "tools/run.sh": SCRIPT},
        0,
        None,
    ),
    (
        "a bare name is found by trailing segments",
        "commands",
        {"README.md": doc("scan.py inspect <path>..."), "hooks/scan.py": SCRIPT},
        0,
        None,
    ),
    (
        "a bare name nobody declares is refused",
        "commands",
        {"README.md": doc("gitleaks dir .")},
        1,
        "`gitleaks` is not a shell builtin, a file of this tree",
    ),
    (
        "a declared tool passes",
        "commands",
        {
            "README.md": doc("gitleaks dir ."),
            ".github/docs.toml": manifest(tools=["gitleaks"]),
        },
        0,
        None,
    ),
    (
        "every command of a pipeline is judged",
        "commands",
        {"README.md": doc("./a.sh | tee out"), "a.sh": SCRIPT},
        1,
        "`tee` is not a shell builtin",
    ),
    (
        "an environment prefix does not hide the command",
        "commands",
        {"README.md": doc("MODE=1 ./gone.sh")},
        1,
        "`./gone.sh` does not exist",
    ),
    (
        "a redirection target is not a command",
        "commands",
        {"README.md": doc("./a.sh > out.txt 2>&1", "./a.sh <in.txt"), "a.sh": SCRIPT},
        0,
        None,
    ),
    (
        "a builtin and a keyword pass",
        "commands",
        {
            "README.md": doc("cd tools && ./run.sh", "if true; then echo ok; fi"),
            "tools/run.sh": SCRIPT,
        },
        0,
        None,
    ),
    (
        "a script handed to bash must exist",
        "commands",
        {"README.md": doc("bash -e gone.sh")},
        1,
        "`gone.sh` does not exist",
    ),
    (
        "a script handed to python3 passes when present",
        "commands",
        {
            "README.md": doc("python3 tools/x.py inspect", 'bash -e "$GATE"'),
            "tools/x.py": "x\n",
        },
        0,
        None,
    ),
    (
        "a command word with a variable is not judged",
        "commands",
        {"README.md": doc('"$GATES_DIR/run/x.sh"', "$TOOL --version")},
        0,
        None,
    ),
    (
        "an absolute path is not judged",
        "commands",
        {"README.md": doc("/usr/bin/env true", "bash /opt/none.sh")},
        0,
        None,
    ),
    (
        "a block bash cannot parse is refused",
        "commands",
        {"README.md": doc("if true; then", "echo hi")},
        1,
        "README.md:4: `bash -n` rejects this block",
    ),
    (
        "a fence that is not shell is not judged",
        "commands",
        {
            "README.md": doc("gone.sh now", lang="text")
            + doc("gone.sh now", lang="yaml")
        },
        0,
        None,
    ),
    (
        "an inline span is not judged",
        "commands",
        {"README.md": "Run `gone.sh now` first.\n"},
        0,
        None,
    ),
    (
        "a heredoc body is not judged",
        "commands",
        {
            "README.md": doc(": <<'EOF' > out", "gone.sh is not run", "EOF", "./a.sh"),
            "a.sh": SCRIPT,
        },
        0,
        None,
    ),
    (
        "a continuation line is one command",
        "commands",
        {"README.md": doc("./a.sh \\", "  --flag \\", "  --other"), "a.sh": SCRIPT},
        0,
        None,
    ),
    (
        "a refusal names the line of a continued command",
        "commands",
        {"README.md": doc("echo hi", "./gone.sh \\", "  --flag")},
        1,
        "README.md:5: `./gone.sh` does not exist",
    ),
    (
        "a tilde fence is read",
        "commands",
        {"README.md": "~~~bash\n./gone.sh\n~~~\n"},
        1,
        "README.md:2: `./gone.sh` does not exist",
    ),
    (
        "a nested document is read from its own directory",
        "commands",
        {"docs/a.md": doc("./run.sh"), "docs/run.sh": SCRIPT},
        0,
        None,
    ),
    (
        "a command inside a substitution is judged",
        "commands",
        {"README.md": doc("echo $(./gone.sh)")},
        1,
        "`./gone.sh` does not exist",
    ),
    (
        "a quote spanning lines does not split the command",
        "commands",
        {"README.md": doc("echo 'one", "gone.sh two'", "./a.sh"), "a.sh": SCRIPT},
        0,
        None,
    ),
    (
        "an AGENTS.md of 8001 bytes is refused",
        "budget",
        {"AGENTS.md": "x" * 8001},
        1,
        "AGENTS.md: 8001 bytes, over the 8000-byte budget",
    ),
    ("an AGENTS.md of 8000 bytes passes", "budget", {"AGENTS.md": "x" * 8000}, 0, None),
    (
        "a nested AGENTS.md has no budget",
        "budget",
        {"apps/web/AGENTS.md": "x" * 9000},
        0,
        None,
    ),
    ("a CLAUDE.md has no budget", "budget", {"CLAUDE.md": "x" * 9000}, 0, None),
    (
        "an em dash in a README",
        "emdash",
        {"README.md": f"# R\n\nOne {DASH} two.\n"},
        1,
        "README.md:3: carries an em dash",
    ),
    (
        "an em dash in a nested instruction file",
        "emdash",
        {"skills/x/SKILL.md": f"---\nname: x\n---\nA {DASH} B\n"},
        1,
        "skills/x/SKILL.md:4: carries an em dash",
    ),
    (
        "a hyphen, an en dash and a minus",
        "emdash",
        {"README.md": "a - b, 1\u20132, 3\u22124\n"},
        0,
        None,
    ),
    ("a tree without Markdown", "emdash", {"a.py": f"x = '{DASH}'\n"}, 0, None),
    (
        "a forked file is exempt from the em dash rule",
        "emdash",
        {
            "skills/y/SKILL.md": f"---\nname: y\n---\n\n<!-- LOCAL FORK of the npm y skill: drops npx -->\nA {DASH} B\n"
        },
        0,
        None,
    ),
    (
        "the fork header is not the exemption when it sits past the first 30 lines",
        "emdash",
        {"README.md": f"{DASH}\n" + "x\n" * 40 + "<!-- LOCAL FORK quoted -->\n"},
        1,
        "README.md:1: carries an em dash",
    ),
    (
        "the fork exemption covers its own file only",
        "emdash",
        {
            "skills/y/SKILL.md": f"<!-- LOCAL FORK of y -->\n{DASH}\n",
            "README.md": f"{DASH}\n",
        },
        1,
        "README.md:1: carries an em dash",
    ),
]


def expect(name, check, files, want, finding):
    rc, out = run(sys.executable, str(GATE), check, str(tree(files)), cwd=workdir)
    if rc != want:
        return f"{name}: exit {rc}, wanted {want}:\n{out}"
    if finding and finding not in out:
        return f"{name}: output does not carry {finding!r}:\n{out}"
    return None


ROWS = """
[[gate]]
id = "docs-owner-reader"
inputs = ["**/*.md", ".github/docs.toml"]
run = 'python3 "$GATES_DIR/run/docs-truth.py" owner .'
{shadow}
[[gate]]
id = "docs-commands-run"
inputs = ["**"]
run = 'python3 "$GATES_DIR/run/docs-truth.py" commands .'
{shadow}
[[gate]]
id = "docs-agents-budget"
inputs = ["AGENTS.md"]
run = 'python3 "$GATES_DIR/run/docs-truth.py" budget .'
{shadow}
[[gate]]
id = "docs-no-em-dash"
inputs = ["**/*.md"]
run = 'python3 "$GATES_DIR/run/docs-truth.py" emdash .'
{shadow}"""
IDS = (
    "docs-owner-reader",
    "docs-commands-run",
    "docs-agents-budget",
    "docs-no-em-dash",
)


def shadow(since):
    """(run's exit, fold's exit, fold's output) for a tree that breaks all four rules under gate.py."""
    registry = ROWS.format(shadow=f"shadow_since = {since}\n" if since else "")
    repo = tree(
        {
            "AGENTS.md": "x" * 8001,
            "README.md": doc("./gone.sh"),
            "NOTES.md": f"one {DASH} two\n",
            ".github/gates.toml": registry,
        }
    )
    env = {**ENV, "GATES_DIR": str(ROOT)}
    run("git", "init", "-q", "--template=", "-b", "main", cwd=repo)
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-qm", "base", cwd=repo)
    run("git", "tag", "base", cwd=repo)
    (repo / "AGENTS.md").write_text("x" * 8002)
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


def main():
    failures = [expect(*case) for case in CASES]
    failures += [f() for f in (in_shadow, established_blocks)]
    failures = [f for f in failures if f]
    total = len(CASES) + 2
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    print(f"test-docs-truth: {total - len(failures)} of {total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
