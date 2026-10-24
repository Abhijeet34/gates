#!/usr/bin/env python3
"""What cargo-mutants 27.1.0 and Stryker 10.0.0 report on the fixtures beside this file.

Installed as `cargo` on PATH, or as a package's node_modules/.bin/stryker, by the
gate-registry rows and the default run of .ci/test-mutation.py, because the CI
jobs install neither tool (README.md, "The mutation rows"). It knows these fixtures
only: the `true` mutant of is_even survives unless a test asserts the odd case,
and double's mutant survives unless a test calls double(3), as the real tools
reported on 2026-10-19. `.ci/test-mutation.py --real` asks the real tools again.

STUB_VERSION overrides the reported version, STUB_EXIT forces an exit status
(4 is cargo-mutants' failing baseline), STUB_NO_MUTANTS makes `cargo mutants` an
unknown command, STUB_IN_PLACE fails a run not passed --in-place, and
STUB_REUSED adds a survivor on an unchanged line, as
Stryker's --incremental report carries one from an earlier run, and STUB_EMPTY
reports no mutant at all, as a change to comments alone does.
"""

import json
import os
import sys
from pathlib import Path

env = os.environ.get


def outcome(fn, line, col, end, replacement, missed):
    return {
        "scenario": {
            "Mutant": {
                "name": f"src/lib.rs:{line}:{col}: replace {fn} with {replacement}",
                "file": "src/lib.rs",
                "function": {"function_name": fn.split(" ")[0]},
                "span": {
                    "start": {"line": line, "column": col},
                    "end": {"line": line, "column": end},
                },
                "replacement": replacement,
                "genre": "FnValue",
            }
        },
        "summary": "MissedMutant" if missed else "CaughtMutant",
    }


def cargo(args):
    if not args or args[0] != "mutants" or env("STUB_NO_MUTANTS"):
        print("error: no such command: `mutants`", file=sys.stderr)
        return 101
    if args[1:] == ["--version"]:
        print(f"cargo-mutants {env('STUB_VERSION', '27.1.0')}")
        return 0
    valued = ("--dir", "--output", "--colors", "--in-diff")
    opts = {a: args[i + 1] for i, a in enumerate(args[:-1]) if a in valued}
    if env("STUB_EXIT"):
        print("stub: forced exit", file=sys.stderr)
        return int(env("STUB_EXIT"))
    if env("STUB_IN_PLACE") and "--in-place" not in args:
        print("stub: --in-place was not passed", file=sys.stderr)
        return 1
    source = (Path(opts["--dir"]) / "src/lib.rs").read_text()
    diff = Path(opts["--in-diff"]).read_text() if "--in-diff" in opts else None
    found = []
    if "fn is_even" in source and (diff is None or "+    x % 2 == 0" in diff):
        found += [
            outcome("is_even -> bool", 6, 5, 15, "true", "!is_even(3)" not in source),
            outcome("is_even -> bool", 6, 5, 15, "false", False),
        ]
    if diff is None:
        found.append(outcome("double -> i32", 2, 5, 10, "0", "double(3)" not in source))
    if env("STUB_EMPTY"):
        found = []
    if not found and diff is not None:
        print(" INFO No mutants to filter")
        return 0
    out = Path(opts["--output"]) / "mutants.out"
    out.mkdir(parents=True)
    (out / "outcomes.json").write_text(
        json.dumps(
            {"outcomes": [{"scenario": "Baseline", "summary": "Success"}, *found]}
        )
    )
    return 2 if any(o["summary"] == "MissedMutant" for o in found) else 0


def mutant(line, col, end, mutator, replacement, survived):
    return {
        "mutatorName": mutator,
        "replacement": replacement,
        "location": {
            "start": {"line": line, "column": col},
            "end": {"line": line, "column": end},
        },
        "status": "Survived" if survived else "Killed",
    }


def in_range(entries, line):
    for entry in entries:
        name, _, span = entry.partition(":")
        if name != "src/math.ts":
            continue
        if not span:
            return True
        a, b = span.split("-")
        if int(a) <= line <= int(b):
            return True
    return False


def stryker(args):
    if args == ["--version"]:
        print(env("STUB_VERSION", "10.0.0"))
        return 0
    if env("STUB_EXIT"):
        print("There were failed tests in the initial test run.", file=sys.stderr)
        return int(env("STUB_EXIT"))
    config = json.loads(Path(args[1]).read_text())
    source = Path("src/math.ts").read_text()
    tests = Path("test/math.test.ts")
    tests = tests.read_text() if tests.exists() else ""
    found = []
    if "isEven" in source and in_range(config["mutate"], 6):
        found += [
            mutant(
                6, 10, 21, "ConditionalExpression", "true", "isEven(3)" not in tests
            ),
            mutant(6, 10, 21, "ConditionalExpression", "false", False),
        ]
    if in_range(config["mutate"], 2) or env("STUB_REUSED"):
        found.append(
            mutant(
                2,
                10,
                15,
                "ArithmeticOperator",
                "x / 2",
                "double(3)" not in tests or bool(env("STUB_REUSED")),
            )
        )
    if env("STUB_EMPTY"):
        found = []
    report = {
        "files": {
            "src/math.ts": {
                "language": "typescript",
                "source": source,
                "mutants": found,
            }
        }
    }
    Path(config["jsonReporter"]["fileName"]).write_text(json.dumps(report))
    return 0


if __name__ == "__main__":
    tool = cargo if Path(sys.argv[0]).name == "cargo" else stryker
    sys.exit(tool(sys.argv[1:]))
