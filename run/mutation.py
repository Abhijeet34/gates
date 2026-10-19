#!/usr/bin/env python3
"""Mutation proof on a change: a mutant of changed code that no test kills is a finding.

Usage: mutation.py rust|typescript --base B [--dir D] [--in-place] [root]    (default root: the current directory)

rust        cargo-mutants over the crate or workspace at D, limited with --in-diff to
            the lines `git diff B...HEAD` changes under D.
typescript  Stryker with its vitest runner over the package at D, limited with
            --mutate to the changed lines of its TypeScript sources, and run
            --incremental so a later run reuses the verdicts of unchanged mutants.
--in-place  rust only: cargo-mutants mutates the checkout and restores each file,
            rather than a copy of D, for a crate whose build reads files outside D
            or untracked ones (quoth's). Only on a disposable checkout: a run killed
            mid-mutant leaves the mutant in the tree.
            Stryker's report also carries reused mutants outside --mutate, so a
            mutant counts only when it touches a changed line.

When the change deletes or weakens a test under D, the row drops the diff limit:
--in-diff matches only code under test, so a change to tests alone would mutate
nothing. It mutates every source under D at HEAD, and a survivor is a finding
unless the same mutant also survived at the merge base, because then the change
did not let it live. A mutant of new code has no counterpart at the base, so it
is named, as in the diff run. README.md, "The mutation rows", owns what counts as deleting
or weakening a test per language and why.

Exit status: 0 pass, 1 a mutant survived, 2 could not decide. Every exit 2 prints
`mutation: refused (TYPE): ...`, and TYPE is one of:

missing-tool      cargo or cargo-mutants is not on PATH; the package at D has no
                  node_modules/.bin/stryker or vitest runner.
wrong-version     the tool is not the version pinned in PINS.
dirty-tree        tracked files under D differ from HEAD, so the tree the tool
                  mutates is not the change being judged.
baseline-failed   the tests fail with no mutant applied, so no survivor means anything.
tool-error        git or the tool failed, timed out, or wrote no report.

The pins live in PINS and nowhere else; a TypeScript package carries the same
versions as exact devDependencies, because Stryker loads its runner and vitest
from the package it runs in.
"""

import argparse
import collections
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

PINS = {
    "cargo-mutants": "27.1.0",
    "@stryker-mutator/core": "10.0.0",
    "@stryker-mutator/vitest-runner": "10.0.0",
}
TIMEOUT = 3600
INCREMENTAL = "node_modules/.cache/gates-mutation/stryker-incremental.json"
SKIP_DIRS = {".git", "node_modules", "target", "vendor", ".venv", "__pycache__"}
# The same test-file rule as run/slop-rules.py and run/fakes.py.
TEST_NAME = re.compile(
    r"(?:^|/)(?:test[_-][^/]*|[^/]*[_.-](?:test|spec)\.\w+)$|(?:^|/)(?:tests?|__tests__)/"
)
FIXTURE_DIR = re.compile(r"(?:^|/)(?:fixtures?|testdata|snapshots?)/")
TS_SOURCE = (".ts", ".tsx", ".mts", ".cts")
TS_TESTS = (*TS_SOURCE, ".js", ".jsx", ".mjs", ".cjs")

# What a test, an assertion and a skip look like, counted per file at the base and at HEAD.
MARKERS = {
    "rust": {
        "tests": re.compile(r"#\[\s*(?:\w+::)*(?:test|rstest|test_case)\b"),
        "asserts": re.compile(
            r"\b(?:debug_)?assert(?:_eq|_ne|_matches)?!|#\[\s*should_panic\b"
        ),
        "skips": re.compile(r"#\[\s*ignore\b"),
    },
    "typescript": {
        "tests": re.compile(
            r"(?<![\w.$])(?:it|test)(?:\.(?:each|concurrent|sequential|fails|only))*\s*[(`]"
        ),
        "asserts": re.compile(
            r"(?<![\w.$])(?:expect|assert|expectTypeOf)(?:\.\w+)*\s*\("
        ),
        "skips": re.compile(
            r"(?<![\w.$])(?:it|test|describe|suite)(?:\.\w+)*\.(?:skip|todo|only|skipIf|runIf)\b"
            r"|(?<![\w.$])x(?:it|test|describe)\s*\("
        ),
    },
}


class Refused(Exception):
    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind


def run(argv, cwd, timeout=TIMEOUT):
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise Refused("tool-error", f"{argv[0]} timed out after {timeout}s") from e
    except OSError as e:
        raise Refused("tool-error", f"{argv[0]} could not run: {e}") from e
    out = (proc.stdout + proc.stderr).decode(errors="replace")
    return proc.returncode, out, proc.stdout


def git(root, *args):
    rc, out, stdout = run(["git", "-c", "core.quotepath=off", *args], root)
    if rc:
        raise Refused("tool-error", f"git {' '.join(args)}: {out.strip()}")
    return stdout


def last_line(out):
    lines = [line for line in out.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "no output"


# --- the tools ----------------------------------------------------------------


def cargo_mutants(pkg):
    if shutil.which("cargo") is None:
        raise Refused(
            "missing-tool",
            f"cargo is not on PATH (cargo-mutants {PINS['cargo-mutants']} is pinned)",
        )
    rc, out, _ = run(["cargo", "mutants", "--version"], pkg, timeout=60)
    if rc:
        raise Refused(
            "missing-tool",
            f"`cargo mutants` is not installed (pinned {PINS['cargo-mutants']};"
            " install steps in README.md): " + last_line(out),
        )
    found = re.search(r"cargo-mutants (\d+\.\d+\.\d+)", out)
    if not found or found[1] != PINS["cargo-mutants"]:
        raise Refused(
            "wrong-version",
            f"cargo-mutants reports {found[1] if found else 'no version'},"
            f" the pin is {PINS['cargo-mutants']}",
        )


def stryker(pkg):
    """The package's own Stryker, once it and its vitest runner are the pinned versions."""
    exe = pkg / "node_modules/.bin/stryker"
    if not exe.exists():
        raise Refused(
            "missing-tool",
            f"{exe.relative_to(pkg)} does not exist; the package needs"
            f" @stryker-mutator/core {PINS['@stryker-mutator/core']} as an exact devDependency",
        )
    for name in ("@stryker-mutator/core", "@stryker-mutator/vitest-runner"):
        manifest = pkg / "node_modules" / name / "package.json"
        try:
            version = json.loads(manifest.read_text()).get("version")
        except FileNotFoundError:
            raise Refused(
                "missing-tool",
                f"{name} is not installed in the package (pinned {PINS[name]})",
            ) from None
        except (OSError, ValueError) as e:
            raise Refused("tool-error", f"{manifest}: {e}") from e
        if version != PINS[name]:
            raise Refused(
                "wrong-version", f"{name} is {version}, the pin is {PINS[name]}"
            )
    return exe


# --- the change -----------------------------------------------------------------


def under(rel, d):
    return d == "." or rel.startswith(d + "/")


def skipped(rel):
    return any(part in SKIP_DIRS for part in rel.split("/")[:-1])


def counted(lang, rel):
    """Whether `rel` is a file whose tests, assertions and skips are counted."""
    if skipped(rel):
        return False
    if lang == "rust":
        return rel.endswith(".rs")
    return rel.endswith(TS_TESTS) and bool(TEST_NAME.search(rel))


def ts_source(rel):
    name = rel.rsplit("/", 1)[-1]
    return (
        rel.endswith(TS_SOURCE)
        and not name.endswith((".d.ts", ".d.mts", ".d.cts"))
        and ".config." not in name
        and not TEST_NAME.search(rel)
        and not FIXTURE_DIR.search(rel)
        and not skipped(rel)
    )


def renames(root, base):
    """(old, new) per changed path; None stands for a side that does not exist."""
    out = git(
        root, "diff", "--name-status", "-M", "-z", "--no-color", base, "HEAD"
    ).decode(errors="replace")
    fields, pairs = out.split("\0"), []
    i = 0
    while i < len(fields) - 1:
        status = fields[i]
        if status[:1] in ("R", "C"):
            old, new = fields[i + 1], fields[i + 2]
            pairs.append((old if status[0] == "R" else None, new))
            i += 3
            continue
        path = fields[i + 1]
        pairs.append((None if status == "A" else path, None if status == "D" else path))
        i += 2
    return pairs


def counts(lang, root, rev, rel):
    if rel is None or not counted(lang, rel):
        return collections.Counter()
    text = git(root, "show", f"{rev}:{rel}").decode(errors="replace")
    return collections.Counter(
        {kind: len(pat.findall(text)) for kind, pat in MARKERS[lang].items()}
    )


def weakened(lang, root, base, d):
    """Why the change deletes or weakens a test under d, one line per file; [] when it does not."""
    found = []
    for old, new in renames(root, base):
        if not any(
            p is not None and under(p, d) and counted(lang, p) for p in (old, new)
        ):
            continue
        before, after = counts(lang, root, base, old), counts(lang, root, "HEAD", new)
        why = [
            f"{kind} {before[kind]} -> {after[kind]}"
            for kind in ("tests", "asserts")
            if after[kind] < before[kind]
        ]
        if after["skips"] > before["skips"]:
            why.append(f"skips {before['skips']} -> {after['skips']}")
        if why:
            found.append(f"{new or old}: {', '.join(why)}")
    return found


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_lines(root, base, d):
    """{path under d: set of line numbers the change adds or rewrites}."""
    found, path = {}, None
    diff = git(
        root, "diff", "-U0", "--no-renames", "--no-color", "--no-ext-diff", base, "HEAD"
    ).decode(errors="replace")
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            if path is not None and not under(path, d):
                path = None
            elif path is not None:
                found[path] = set()
        elif path is not None and (hunk := HUNK.match(line)):
            start, count = int(hunk[1]), int(hunk[2] or 1)
            found[path].update(range(start, start + count))
    return {p: lines for p, lines in found.items() if lines}


def ranges(lines):
    out, start, prev = [], None, None
    for n in sorted(lines):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append((start, prev))
            start = prev = n
    if start is not None:
        out.append((start, prev))
    return out


def local(rel, d):
    return rel if d == "." else rel[len(d) + 1 :]


# --- one run of a tool ------------------------------------------------------------


def snippet(text, span):
    """The source a 1-based, end-exclusive span covers."""
    lines = text.splitlines(keepends=True)
    (sl, sc), (el, ec) = span
    if not (1 <= sl <= el <= len(lines)):
        return ""
    if sl == el:
        return lines[sl - 1][sc - 1 : ec - 1]
    middle = "".join(lines[sl : el - 1])
    return lines[sl - 1][sc - 1 :] + middle + lines[el - 1][: ec - 1]


def read(pkg, rel):
    try:
        return (pkg / rel).read_text(errors="replace")
    except OSError:
        return ""


def run_rust(pkg, scratch, diff, in_place):
    """(mutants tested, survivors as (label, key)) for one cargo-mutants run on pkg."""
    scratch.mkdir(parents=True, exist_ok=True)
    out = scratch / "rust-out"
    argv = ["cargo", "mutants", "--dir", str(pkg), "--output", str(out), "--no-shuffle"]
    argv += ["--colors", "never"]
    if in_place:
        argv.append("--in-place")
    if diff is not None:
        (scratch / "change.diff").write_text(diff)
        argv += ["--in-diff", str(scratch / "change.diff")]
    rc, log, _ = run(argv, pkg)
    # 2 is a missed mutant, 3 a timeout, 4 a failing baseline; anything else is the tool's own error.
    if rc == 4:
        raise Refused("baseline-failed", "cargo-mutants: " + last_line(log))
    if rc not in (0, 2, 3):
        raise Refused("tool-error", f"cargo-mutants exited {rc}: {last_line(log)}")
    report = out / "mutants.out/outcomes.json"
    if not report.is_file():
        if rc == 0 and diff is not None:
            return 0, []  # --in-diff found no mutant on the changed lines
        raise Refused(
            "tool-error", f"cargo-mutants exited {rc} and wrote no outcomes.json"
        )
    try:
        outcomes = json.loads(report.read_text())["outcomes"]
    except (OSError, ValueError, KeyError) as e:
        raise Refused("tool-error", f"cargo-mutants outcomes.json: {e}") from e
    tested, survivors = 0, []
    for o in outcomes:
        scenario = o.get("scenario")
        if not isinstance(scenario, dict) or "Mutant" not in scenario:
            continue
        m, summary = scenario["Mutant"], o.get("summary")
        if summary in ("CaughtMutant", "MissedMutant", "Timeout"):
            tested += 1
        if summary != "MissedMutant":
            continue
        span = m.get("span", {})
        start, end = span.get("start", {}), span.get("end", {})
        original = snippet(
            read(pkg, m["file"]),
            (
                (start.get("line", 0), start.get("column", 0)),
                (end.get("line", 0), end.get("column", 0)),
            ),
        )
        function = (m.get("function") or {}).get("function_name", "")
        key = (m["file"], function, m.get("genre"), m.get("replacement"), original)
        survivors.append((m.get("name") or f"{m['file']}: {m.get('replacement')}", key))
    return tested, survivors


SURVIVED = ("Survived", "NoCoverage")
TESTED = ("Killed", "Timeout", *SURVIVED)


def run_typescript(pkg, scratch, mutate, keep):
    """(mutants tested, survivors as (label, key)) for one Stryker run on pkg.

    `keep(file, start_line, end_line)` decides whether a reported mutant is in scope."""
    exe = stryker(pkg)
    scratch.mkdir(parents=True, exist_ok=True)
    report = scratch / "stryker-report.json"
    config = {
        "testRunner": "vitest",
        "plugins": ["@stryker-mutator/vitest-runner"],
        "mutate": mutate,
        "reporters": ["json"],
        "jsonReporter": {"fileName": str(report)},
        "incremental": True,
        # Stryker's default, reports/, is tracked-tree litter in a caller that does not
        # ignore it (gh-axi, 2026-10-19); node_modules is ignored wherever it exists.
        "incrementalFile": str(pkg / INCREMENTAL),
        "cleanTempDir": "always",
        "thresholds": {"high": 80, "low": 60, "break": None},
    }
    (scratch / "stryker.json").write_text(json.dumps(config, indent=2))
    rc, log, _ = run([str(exe), "run", str(scratch / "stryker.json")], pkg)
    if rc:
        if "initial test run" in log:
            raise Refused("baseline-failed", "stryker: " + last_line(log))
        raise Refused("tool-error", f"stryker exited {rc}: {last_line(log)}")
    try:
        files = json.loads(report.read_text())["files"]
    except (OSError, ValueError, KeyError) as e:
        raise Refused("tool-error", f"stryker wrote no readable report: {e}") from e
    tested, survivors = 0, []
    for rel, entry in sorted(files.items()):
        source = entry.get("source", "")
        for m in entry.get("mutants", []):
            loc = m.get("location", {})
            start, end = loc.get("start", {}), loc.get("end", {})
            sl, el = start.get("line", 0), end.get("line", 0)
            if not keep(rel, sl, el) or m.get("status") not in TESTED:
                continue
            tested += 1
            if m["status"] not in SURVIVED:
                continue
            original = snippet(
                source, ((sl, start.get("column", 0)), (el, end.get("column", 0)))
            )
            replacement = m.get("replacement", "")
            label = (
                f"{rel}:{sl}:{start.get('column', 0)}: replace {original!r} with"
                f" {replacement!r} ({m.get('mutatorName')}, {m['status']})"
            )
            survivors.append(
                (label, (rel, m.get("mutatorName"), replacement, original))
            )
    return tested, survivors


# --- the rows ---------------------------------------------------------------------


def diff_run(lang, root, pkg, d, base, scratch, in_place):
    lines = added_lines(root, base, d)
    if lang == "rust":
        lines = {p: n for p, n in lines.items() if p.endswith(".rs") and not skipped(p)}
        if not lines:
            return None
        diff = git(
            pkg,
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-renames",
            "--relative",
            base,
            "HEAD",
            "--",
            ".",
        ).decode(errors="replace")
        return run_rust(pkg, scratch, diff, in_place)
    lines = {local(p, d): n for p, n in lines.items() if ts_source(p)}
    if not lines:
        return None
    mutate = [
        f"{rel}:{a}-{b}" for rel, n in sorted(lines.items()) for a, b in ranges(n)
    ]

    def keep(rel, sl, el):
        return any(n in lines.get(rel, ()) for n in range(sl, el + 1))

    return run_typescript(pkg, scratch, mutate, keep)


def full_run(lang, root, tree, d, scratch, rev, in_place):
    """One unrestricted run over the sources under d of `tree`, a checkout of `rev`."""
    pkg = tree / d
    if lang == "rust":
        return run_rust(pkg, scratch, None, in_place)
    listed = git(root, "ls-tree", "-r", "-z", "--name-only", rev).decode(
        errors="replace"
    )
    mutate = sorted(
        local(p, d) for p in listed.split("\0") if p and under(p, d) and ts_source(p)
    )
    if not mutate:
        return 0, []
    return run_typescript(pkg, scratch, mutate, lambda rel, sl, el: True)


def export(root, rev, into, d, pkg):
    """The tracked tree at `rev`, extracted into `into`, sharing the package's node_modules."""
    data = git(root, "archive", "--format=tar", rev)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(into, filter="data")
    modules = pkg / "node_modules"
    if modules.is_dir() and not (into / d / "node_modules").exists():
        (into / d).mkdir(parents=True, exist_ok=True)
        (into / d / "node_modules").symlink_to(modules)
    return into


def judge(lang, root, d, base_arg, in_place):
    pkg = (root / d).resolve()
    if not pkg.is_dir() or not (pkg == root or root in pkg.parents):
        raise Refused("tool-error", f"--dir {d} is not a directory inside {root}")
    if lang == "rust":
        cargo_mutants(pkg)
    else:
        if in_place:
            raise Refused("tool-error", "--in-place applies to rust only")
        stryker(pkg)
    base = git(root, "merge-base", base_arg, "HEAD").decode().strip()
    dirty = git(root, "status", "--porcelain", "--untracked-files=no", "--", d).decode()
    if dirty.strip():
        raise Refused(
            "dirty-tree",
            f"tracked files under {d} differ from HEAD: {last_line(dirty)}",
        )
    found = []
    with tempfile.TemporaryDirectory(prefix="gates-mutation-") as tmp:
        scratch = Path(tmp)
        reasons = weakened(lang, root, base, d)
        if not reasons:
            result = diff_run(lang, root, pkg, d, base, scratch / "head", in_place)
            if result is None:
                print(
                    f"mutation: {lang}: the change touches no {lang} source under {d}, nothing to mutate"
                )
                return []
            tested, survivors = result
            found = [label for label, _ in survivors]
            print(
                f"mutation: {lang}: {tested} mutant(s) of the changed lines under {d}, {len(found)} survived"
            )
            return found
        for why in reasons:
            print(
                f"mutation: {why}: a test was deleted or weakened, so every mutant under {d} runs"
            )
        tested, survivors = full_run(
            lang, root, root, d, scratch / "head", "HEAD", in_place
        )
        print(
            f"mutation: {lang}: {tested} mutant(s) under {d} at HEAD, {len(survivors)} survived"
        )
        if not survivors:
            return []
        tree = export(root, base, scratch / "tree", d, pkg)
        _, before = full_run(lang, root, tree, d, scratch / "base", base, in_place)
        alive = collections.Counter(key for _, key in before)
        for label, key in survivors:
            if alive[key]:
                alive[key] -= 1
            else:
                found.append(label)
        kept = len(survivors) - len(found)
        if kept:
            print(
                f"mutation: {lang}: {kept} survivor(s) also survive at the merge base, so the change did not cause them"
            )
    return found


def main():
    ap = argparse.ArgumentParser(
        usage="mutation.py rust|typescript --base B [--dir D] [--in-place] [root]"
    )
    ap.add_argument("lang", choices=("rust", "typescript"))
    ap.add_argument("--base", required=True)
    ap.add_argument("--dir", default=".")
    ap.add_argument("--in-place", action="store_true")
    ap.add_argument("root", nargs="?", default=".")
    # Plain parse_args consumes the optional root early on Python before 3.12.7.
    args = ap.parse_intermixed_args()
    root = Path(args.root).resolve()
    d = args.dir.strip("/") or "."
    try:
        found = judge(args.lang, root, d, args.base, args.in_place)
    except Refused as e:
        print(f"mutation: refused ({e.kind}): {e}")
        return 2
    for label in found:
        print(f"mutation: survived: {label if d == '.' else d + '/' + label}")
    if found:
        print(f"mutation: {len(found)} mutant(s) of this change survived every test")
        return 1
    print(f"mutation: {args.lang}: no mutant of this change survived")
    return 0


if __name__ == "__main__":
    sys.exit(main())
