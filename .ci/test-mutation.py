#!/usr/bin/env python3
"""Regression tests for run/mutation.py, and its rows in shadow under run/gate.py.

    .ci/test-mutation.py            the tool is .ci/fixtures/mutation/stub-tool.py
    .ci/test-mutation.py --real     cargo-mutants 27.1.0 on PATH, and npm to install
                                    Stryker 10.0.0 and vitest into a fixture package;
                                    ci.yml's `shell` job runs it (check_shell, under CI)

Each case builds a repository from .ci/fixtures/mutation with a base and a change,
runs one row and requires its exit status and a line of its output. The default
run proves the script's own decisions against a stub that answers as the real
tools did on these fixtures: which survivor is the change's, which change is a
weakening, and which failure is a typed refusal. `--real` puts the fixtures to
the pinned tools for what the stub only replays: the weak, strong, removed and
added assertion of each language. The stub's copy of those four is the gate
registry's mutation rows, which run where no tool is installed (README.md,
"The mutation rows").
"""

import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "run/mutation.py"
RUNNER = ROOT / "run/gate.py"
BUILD = ROOT / ".ci/fixtures/mutation/build.sh"
ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "STUB_"))},
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@invalid",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@invalid",
    "PYTHONDONTWRITEBYTECODE": "1",
}
workdir = Path(tempfile.mkdtemp())


def run(*args, cwd, env=ENV):
    proc = subprocess.run(
        args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    return proc.returncode, proc.stdout + proc.stderr


def commit(repo, message):
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-qm", message, cwd=repo)


def build(lang, *steps, stub=True, into=None, before=None):
    """A fixture repository; `before` maps extra files to write ahead of the base commit."""
    repo = Path(tempfile.mkdtemp(dir=workdir)) if into is None else into
    for rel, text in (before or {}).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    args = ["bash", str(BUILD), *(["--stub"] if stub else []), lang, *steps]
    rc, out = run(*args, cwd=repo)
    if rc:
        raise SystemExit(f"build.sh {' '.join(args[2:])} failed:\n{out}")
    return repo


def edit(repo, rel, old, new):
    path = repo / rel
    text = path.read_text()
    assert old in text, f"{rel} lacks {old!r}"
    path.write_text(text.replace(old, new))


def retag(repo):
    """Move `base` to HEAD, so the next commit is the change under judgement."""
    run("git", "tag", "-f", "base", cwd=repo)


def gate(lang, repo, *extra, env=None):
    path = f"{repo / '.stub'}:{ENV['PATH']}"
    full = {**ENV, "PATH": path, **(env or {})}
    return run(
        sys.executable, str(GATE), lang, "--base", "base", *extra, str(repo),
        cwd=workdir, env=full,
    )  # fmt: skip


def expect(name, result, want, *needles, absent=()):
    rc, out = result
    missing = [n for n in needles if n not in out]
    present = [n for n in absent if n in out]
    if rc != want or missing or present:
        return f"{name}: wanted exit {want} with {missing} and without {present}, got {rc}:\n{out}"
    return None


SURVIVOR_RS = "mutation: survived: src/lib.rs:6:5: replace is_even -> bool with true"
SURVIVOR_TS = "mutation: survived: src/math.ts:6:10: replace 'x % 2 === 0' with 'true'"
FULL = "a test was deleted or weakened, so every mutant under . runs"


def refused(kind):
    return f"mutation: refused ({kind}):"


# --- the full run's base comparison -------------------------------------------------


def base_survivor():
    """A survivor that lived at the base too is not the change's; the new one is."""
    repo = build("rust", "weak")
    retag(repo)
    edit(repo, "src/lib.rs", "        assert_eq!(double(3), 6);\n", "")
    commit(repo, "drop the double assertion")
    return expect(
        "rust: only the survivor the change caused is named",
        gate("rust", repo),
        1,
        "mutation: survived: src/lib.rs:2:5: replace double -> i32 with 0",
        "1 survivor(s) also survive at the merge base",
        absent=[SURVIVOR_RS],
    )


def new_code_survivor():
    """A mutant of code the change adds has no base counterpart, and is named as in the diff run."""
    repo = build("rust", "weak")
    edit(repo, "src/lib.rs", "        assert_eq!(double(3), 6);\n", "")
    edit(repo, "src/lib.rs", "        assert!(is_even(2));\n", "")
    commit(repo, "add is_even untested and drop the tests")
    return expect(
        "rust: untested new code plus a deleted test is named",
        gate("rust", repo),
        1,
        f"mutation: src/lib.rs: asserts 1 -> 0: {FULL}",
        SURVIVOR_RS,
    )


def weakenings():
    rs_ignore = build("rust", "strong")
    retag(rs_ignore)
    edit(
        rs_ignore,
        "src/lib.rs",
        "    #[test]\n    fn even",
        "    #[test]\n    #[ignore]\n    fn even",
    )
    commit(rs_ignore, "ignore a test")
    ts_skip = build("typescript", "strong")
    retag(ts_skip)
    edit(ts_skip, "test/math.test.ts", 'test("even"', 'test.skip("even"')
    commit(ts_skip, "skip a test")
    ts_gone = build("typescript", "strong")
    retag(ts_gone)
    (ts_gone / "test/math.test.ts").unlink()
    commit(ts_gone, "delete the tests")
    ts_moved = build("typescript", "strong")
    retag(ts_moved)
    run("git", "mv", "test/math.test.ts", "test/numbers.test.ts", cwd=ts_moved)
    commit(ts_moved, "rename the tests")
    ts_only = build("typescript", "strong")
    retag(ts_only)
    edit(ts_only, "test/math.test.ts", 'test("even"', 'test.only("even"')
    commit(ts_only, "focus a test")
    return [
        expect(
            "rust: #[ignore] added", gate("rust", rs_ignore), 0, f"skips 0 -> 1: {FULL}"
        ),
        expect(
            "typescript: .skip added",
            gate("typescript", ts_skip),
            0,
            f"skips 0 -> 1: {FULL}",
        ),
        expect(
            "typescript: .only added",
            gate("typescript", ts_only),
            0,
            f"skips 0 -> 1: {FULL}",
        ),
        expect(
            "typescript: a test file deleted",
            gate("typescript", ts_gone),
            1,
            f"mutation: test/math.test.ts: tests 2 -> 0, asserts 3 -> 0: {FULL}",
        ),
        expect(
            "typescript: a test file renamed unchanged",
            gate("typescript", ts_moved),
            0,
            "nothing to mutate",
            absent=[FULL],
        ),
    ]


def scoping():
    reused = build("typescript", "weak")
    readme = build("rust", "strong")
    retag(readme)
    (readme / "README.md").write_text("notes\n")
    commit(readme, "notes")
    outer = Path(tempfile.mkdtemp(dir=workdir))
    crate = outer / "crates/fix/src"
    crate.mkdir(parents=True)
    fixtures = ROOT / ".ci/fixtures/mutation"
    run("git", "init", "-q", "--template=", "-b", "main", cwd=outer)
    shutil.copy(fixtures / "rust/base/src/lib.rs", crate)
    commit(outer, "base")
    retag(outer)
    shutil.copy(fixtures / "rust/weak/src/lib.rs", crate)
    commit(outer, "change")
    (outer / ".stub").mkdir()
    shutil.copy(fixtures / "stub-tool.py", outer / ".stub/cargo")
    return [
        expect(
            "typescript: a reused survivor outside the change is not named",
            gate("typescript", reused, env={"STUB_REUSED": "1"}),
            1,
            SURVIVOR_TS,
            absent=["x * 2"],
        ),
        expect(
            "rust: a change to no Rust file",
            gate("rust", readme),
            0,
            "nothing to mutate",
        ),
        expect(
            "rust: a crate under --dir",
            gate("rust", outer, "--dir", "crates/fix"),
            1,
            "mutation: survived: crates/fix/src/lib.rs:6:5: replace is_even -> bool with true",
        ),
    ]


# --- refusals ------------------------------------------------------------------------


def refusals():
    rust = build("rust", "weak")
    ts = build("typescript", "weak")
    bare = build("typescript", "weak", stub=False)
    old = build("typescript", "weak")
    (old / "node_modules/@stryker-mutator/core/package.json").write_text(
        '{"version": "9.6.1"}'
    )
    dirty = build("rust", "weak")
    (dirty / "src/lib.rs").write_text("pub fn changed() {}\n")
    return [
        expect(
            "typescript: no Stryker",
            gate("typescript", bare),
            2,
            refused("missing-tool"),
        ),
        expect(
            "typescript: Stryker 9.6.1",
            gate("typescript", old),
            2,
            refused("wrong-version")
            + " @stryker-mutator/core is 9.6.1, the pin is 10.0.0",
        ),
        expect(
            "rust: no cargo-mutants",
            gate("rust", rust, env={"STUB_NO_MUTANTS": "1"}),
            2,
            refused("missing-tool"),
        ),
        expect(
            "rust: cargo-mutants 27.0.0",
            gate("rust", rust, env={"STUB_VERSION": "27.0.0"}),
            2,
            refused("wrong-version")
            + " cargo-mutants reports 27.0.0, the pin is 27.1.0",
        ),
        expect(
            "rust: a failing baseline",
            gate("rust", rust, env={"STUB_EXIT": "4"}),
            2,
            refused("baseline-failed"),
        ),
        expect(
            "typescript: a failing baseline",
            gate("typescript", ts, env={"STUB_EXIT": "1"}),
            2,
            refused("baseline-failed"),
        ),
        expect(
            "rust: the tool's own error",
            gate("rust", rust, env={"STUB_EXIT": "1"}),
            2,
            refused("tool-error") + " cargo-mutants exited 1",
        ),
        expect("rust: a dirty tree", gate("rust", dirty), 2, refused("dirty-tree")),
        expect(
            "rust: --in-place reaches cargo-mutants",
            gate("rust", rust, "--in-place", env={"STUB_IN_PLACE": "1"}),
            1,
            SURVIVOR_RS,
        ),
        expect(
            "typescript: --in-place is refused",
            gate("typescript", ts, "--in-place"),
            2,
            refused("tool-error") + " --in-place applies to rust only",
        ),
        expect(
            "rust: an unknown base",
            run(
                sys.executable,
                str(GATE),
                "rust",
                "--base",
                "nope",
                str(rust),
                cwd=workdir,
                env={**ENV, "PATH": f"{rust / '.stub'}:{ENV['PATH']}"},
            ),
            2,
            refused("tool-error") + " git merge-base nope HEAD",
        ),  # fmt: skip
    ]


# --- the rows under run/gate.py --------------------------------------------------------

ROWS = """[[gate]]
id = "mutation-rust"
inputs = ["**/*.rs", "**/Cargo.toml", "**/Cargo.lock"]
scoped = true
tools = {{ cargo-mutants = "cargo mutants --version", base = 'git merge-base "$BASE_SHA" HEAD' }}
run = 'python3 "$GATES_DIR/run/mutation.py" rust --base "$BASE_SHA"'
{shadow}
[[gate]]
id = "docs"
inputs = ["*.md"]
run = "true"
"""


def under_runner(since, touch="src/lib.rs"):
    """(run's exit, fold's exit, fold's output) for a weak Rust change under gate.py."""
    shadow = f"shadow_since = {since}\n" if since else ""
    repo = build(
        "rust", "strong", before={".github/gates.toml": ROWS.format(shadow=shadow)}
    )
    retag(repo)
    if touch == "src/lib.rs":
        edit(repo, "src/lib.rs", "        assert!(!is_even(3));\n", "")
    else:
        (repo / touch).write_text("notes\n")
    commit(repo, "change")
    env = {
        **ENV,
        "GATES_DIR": str(ROOT),
        "BASE_SHA": "base",
        "PATH": f"{repo / '.stub'}:{ENV['PATH']}",
    }
    store = repo.parent / f"{repo.name}-store"
    args = ("--base", "base", "--store", str(store))
    run_rc, _ = run(sys.executable, str(RUNNER), "run", *args, cwd=repo, env=env)
    fold_rc, out = run(sys.executable, str(RUNNER), "fold", *args, cwd=repo, env=env)
    return run_rc, fold_rc, out


def runner_cases():
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    results = []
    run_rc, fold_rc, out = under_runner(today)
    window = f"(in shadow since {today}: 1 of 50 runs, 0 of 14 days)"
    if (run_rc, fold_rc) != (
        0,
        0,
    ) or f"shadow mutation-rust: fail: exit 1 {window}" not in out:
        results.append(
            f"shadow row: wanted 0, 0 and a shadow line, got {run_rc}, {fold_rc}:\n{out}"
        )
    run_rc, fold_rc, out = under_runner(None)
    if (run_rc, fold_rc) != (1, 1) or "FAIL mutation-rust: fail: exit 1" not in out:
        results.append(
            f"established row: wanted 1, 1 and a FAIL line, got {run_rc}, {fold_rc}:\n{out}"
        )
    _, _, out = under_runner(today, touch="README.md")
    if "mutation-rust" in out:
        results.append(f"scoped row: a README change selected it:\n{out}")
    return results or [None]


# --- --real: the same fixtures put to the pinned tools -----------------------------------


def node_modules():
    """One installed copy of the fixture package's devDependencies, shared by every case."""
    here = build("typescript", stub=False)
    rc, out = run(
        "npm", "install", "--no-audit", "--no-fund", "--ignore-scripts", cwd=here
    )
    if rc:
        raise SystemExit(f"npm install failed:\n{out}")
    return here / "node_modules"


def real_cases(seen):
    rc, out = run("cargo", "mutants", "--version", cwd=workdir)
    if rc:
        return [f"--real needs cargo-mutants 27.1.0 on PATH: {out.strip()}"]
    modules = node_modules()

    def ts(*steps):
        repo = build("typescript", *steps, stub=False)
        (repo / "node_modules").symlink_to(modules)
        return repo

    cases = [
        ("rust: a weak new test", "rust", lambda: build("rust", "weak", stub=False), 1, [SURVIVOR_RS]),
        ("rust: a strong new test", "rust", lambda: build("rust", "strong", stub=False), 0, ["0 survived"]),
        ("rust: an assertion removed", "rust", lambda: build("rust", "strong", "weak", stub=False), 1, [FULL, SURVIVOR_RS]),
        ("typescript: a weak new test", "typescript", lambda: ts("weak"), 1, [SURVIVOR_TS]),
        ("typescript: a strong new test", "typescript", lambda: ts("strong"), 0, ["0 survived"]),
        ("typescript: an assertion removed", "typescript", lambda: ts("strong", "weak"), 1, [FULL, SURVIVOR_TS]),
        ("rust: an assertion added is not a weakening", "rust", lambda: build("rust", "weak", "strong", stub=False), 0, [], [FULL]),
        ("typescript: an assertion added is not a weakening", "typescript", lambda: ts("weak", "strong"), 0, [], [FULL]),
    ]  # fmt: skip
    results = []
    for name, lang, make, want, needles, *absent in cases:
        repo = make()
        t0 = time.monotonic()
        results.append(
            expect(
                name,
                gate(lang, repo),
                want,
                *needles,
                absent=absent[0] if absent else (),
            )
        )
        seen.append((name, time.monotonic() - t0))
        _, left = run("git", "status", "--porcelain", "--ignored", cwd=repo)
        litter = [
            line
            for line in left.splitlines()
            if line not in ("!! node_modules", "!! target/")
        ]
        if litter:
            results.append(f"{name}: the run left {litter} in the tree")
    return results


def main():
    real = "--real" in sys.argv[1:]
    seen = []
    if real:
        results = real_cases(seen)
    else:
        results = [
            base_survivor(),
            new_code_survivor(),
            *weakenings(),
            *scoping(),
            *refusals(),
        ]
        results += runner_cases()
    failures = [r for r in results if r]
    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    for name, seconds in seen:
        print(f"  {seconds:6.2f} s  {name}")
    total = len(results)
    label = "real" if real else "stub"
    print(f"test-mutation ({label}): {total - len(failures)} of {total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
