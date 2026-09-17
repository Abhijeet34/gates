#!/usr/bin/env python3
"""Measure how wide every allowlist in .gitleaks.toml actually is.

    .ci/gitleaks/test-allowlists.py

The rules have had a coverage test since test-rules.sh; the allowlists had
none, and an allowlist is the half that fails SILENTLY - a rule that stops
firing reddens nothing, and neither does a hole that is one path wider than its
author meant. Every row of fixtures/allowlist-cases.tsv is checked three ways:

  1. the silenced line DOES fire at a neutral path, so a row cannot pass by
     naming a line no rule ever cared about, and so the PATH is isolated as the
     only thing that changes between this scan and the next;
  2. it does NOT fire at the path the entry declares;
  3. the row's still-fires line, at that same path, IS still reported - and the
     silenced line beside it is still not. A `-` there says the entry excuses
     every rule at that path, which is a claim the table then carries in the
     open rather than a property nobody wrote down.

A NOTE line, not a failure, marks a row that stays silent with our own
allowlists stripped out: gitleaks' bundled allowlist already covered it, so the
entry is inert for that line and is width nobody is paying for.

Coverage runs off .gitleaks.toml itself, not off a list here: every `paths`
regex of every [[allowlists]] block must be exercised by a row, and every row
must name a regex that block still declares. Delete an entry and its row goes
stale; add one and it is unexercised. Either way this fails.

Fails closed. A missing gitleaks, an unreadable config, or a gitleaks crash
exits nonzero rather than reporting a pass.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / ".gitleaks.toml"
CASES = ROOT / ".ci/gitleaks/fixtures/allowlist-cases.tsv"
KNOWN = ROOT / ".ci/gitleaks/fixtures/known-secrets.tsv"

RED, GREEN, YELLOW, OFF = "\033[31m", "\033[32m", "\033[33m", "\033[0m"
failures: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    print(f"{RED}FAIL{OFF} {msg}", file=sys.stderr)
    failures.append(msg)


def die(msg: str) -> None:
    print(f"test-allowlists: {msg}", file=sys.stderr)
    raise SystemExit(2)


def strip_allowlists(text: str) -> str:
    """The same rules with every [[allowlists]] block removed.

    Allowlists sit at the end of the file by convention, so the split is
    positional - and the convention is asserted rather than trusted, because a
    rule accidentally left below the cut would silently stop being exercised
    and every case would then measure a config that detects nothing.
    """
    head, sep, tail = text.partition("[[allowlists]]")
    if not sep:
        die("no [[allowlists]] block in .gitleaks.toml - nothing to test")
    if "[[rules]]" in tail:
        die("a [[rules]] block sits below the first [[allowlists]]; move it up")
    return head


def scan(tree: Path, config: Path) -> list[dict]:
    """Findings for a tree, scanned from inside it.

    Measured on gitleaks 8.30.1: `gitleaks dir <absolute path>` reports File as
    an absolute path, which no `^`-anchored allowlist can match, so every entry
    in the config would read as absent. Scanning `.` with cwd inside the tree is
    what reproduces the paths a real repository scan produces.
    """
    report = tree / "_report.json"
    proc = subprocess.run(
        [
            "gitleaks",
            "dir",
            ".",
            "--config",
            str(config),
            "--no-banner",
            "--log-level",
            "error",
            "--report-format",
            "json",
            "--report-path",
            str(report),
        ],
        cwd=tree,
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):
        die(f"gitleaks exited {proc.returncode}: {proc.stderr.strip()}")
    return json.loads(report.read_text() or "[]")


def build(base: Path, rel: str, lines: list[str]) -> Path:
    tree = Path(tempfile.mkdtemp(dir=base))
    target = tree / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(f"{line}\n" for line in lines))
    return tree


def main() -> int:
    if not shutil.which("gitleaks"):
        die("gitleaks not installed - install with: brew install gitleaks")
    for path in (CONFIG, CASES, KNOWN):
        if not path.is_file():
            die(f"cannot read {path}")

    text = CONFIG.read_text()
    try:
        declared = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        die(f"{CONFIG} does not parse: {exc}")
    registry = {
        p for entry in declared.get("allowlists", []) for p in entry.get("paths", [])
    }
    if not registry:
        die("no allowlist paths declared - the coverage check would be vacuous")

    # `@label` in a case cell is that label's line in the generated fixture.
    known = {
        cols[1]: cols[2]
        for raw in KNOWN.read_text().splitlines()
        if raw and not raw.startswith("#")
        for cols in [raw.split("\t")]
    }

    rows = []
    for lineno, raw in enumerate(CASES.read_text().splitlines(), 1):
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) != 4:
            die(f"{CASES}:{lineno}: expected 4 tab-separated columns, got {len(parts)}")
        for i in (2, 3):
            if parts[i].startswith("@"):
                if parts[i][1:] not in known:
                    die(f"{CASES}:{lineno}: no {parts[i][1:]!r} row in {KNOWN.name}")
                parts[i] = known[parts[i][1:]]
        rows.append(parts)
    if not rows:
        die("case table yielded zero rows")

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        bare = base / "no-allowlists.toml"
        bare.write_text(strip_allowlists(text))

        for regex, rel, silenced, fires in rows:
            label = f"{regex} @ {rel}"
            if regex not in registry:
                fail(f"{label}: .gitleaks.toml declares no such allowlist path")
                continue

            # 1. the silenced line must be a real finding to begin with.
            if not scan(build(base, "probe.txt", [silenced]), CONFIG):
                fail(
                    f"{label}: silenced line fires no rule at a neutral path, "
                    f"so it proves nothing about this entry"
                )
                continue

            # 2. and must be silent at the declared path.
            got = scan(build(base, rel, [silenced]), CONFIG)
            if got:
                fail(f"{label}: still reported - {got[0]['RuleID']}")
            elif not scan(build(base, rel, [silenced]), bare):
                notes.append(
                    f"{label}: gitleaks' own allowlist already "
                    f"silences this - our entry is inert here"
                )

            # 3. a real credential beside it must survive, on line 2.
            if fires != "-":
                got = scan(build(base, rel, [silenced, fires]), CONFIG)
                if not got:
                    fail(
                        f"{label}: still-fires line is swallowed too - the "
                        f"entry is wider than the table claims"
                    )
                elif any(f["StartLine"] != 2 for f in got):
                    fail(f"{label}: the silenced line was reported alongside it")

    unexercised = registry - {r[0] for r in rows}
    for path in sorted(unexercised):
        fail(f"{path}: declared in .gitleaks.toml, exercised by no case")

    if failures:
        print(
            f"{RED}{len(failures)} of {len(rows)} allowlist checks failed{OFF}",
            file=sys.stderr,
        )
        return 1
    for note in notes:
        print(f"{YELLOW}NOTE{OFF} {note}")
    blanket = sum(1 for r in rows if r[3] == "-")
    print(
        f"{GREEN}ok{OFF} {len(rows)} allowlist paths exercised "
        f"({len(rows) - blanket} narrowed, {blanket} blanket), "
        f"{len(registry)} declared"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
