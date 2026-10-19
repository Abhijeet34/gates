#!/usr/bin/env python3
"""Select, run and fold a repository's gates from its registry and verdict records.

Usage:
  gate.py select --base B [--head H]       selected gate ids, each with why
  gate.py key ID [--head H]                the gate's cache key at H
  gate.py run --base B --store DIR         run the selection on the checkout
  gate.py fold --base B [--head H] --store DIR

The registry is `.github/gates.toml` in the tree under test, read at H, one
`[[gate]]` table per gate:

  id     = "rust"
  inputs = ["src/**", "Cargo.toml", "Cargo.lock"]   # decide selection and the key
  run    = "cargo test --locked"                     # bash -e, at the checkout root
  tools  = { cargo = "cargo --version" }             # optional: probe whose output keys
  blocks = true                                      # optional: false reports only
  kind   = "check"                                   # optional: check, review or docs
  shadow_since = 2026-10-19                          # optional: date it entered shadow
  scoped = true                                      # optional: only its inputs select it

A `review` or `docs` row has no `run`: the review pipeline appends its verdict
to DIR/records.jsonl in the same record shape, `run` skips it, and the fold
reads it like any other, so an absent or `error` review never folds to pass.
A review row with a record for H is folded even when selection left it out.
A review row may also carry `lens`, `reviewers` and `budget`, which run/review.py
reads and judges; on any other row they are unknown keys.

Selection maps `git diff --name-only B...H` through each gate's inputs, and
selects every gate when a changed path is named by no gate, is the registry or
is under `.github/`, or when there is no base or no change: over-testing is
cheap with the cache, under-testing is not. A `scoped` row is the exception for
the first two: it is for a row too slow to run on an unrelated change (the
advisory row, README.md, "The tool rows"), so it is selected only when a changed
path matches its inputs or the registry changes, and an unnamed path or another
file under `.github/` leaves it out. No base and no change still select it.

A gate's key is a sha256 over its id, its registry row, this file, the runner
class, each tool probe's output and the (mode, blob, path) of every file at H
its inputs match, so a change outside a gate's inputs reuses its verdict. `run`
appends one verdict record per selected gate to DIR/records.jsonl, reusing a
`pass` with the same key instead of running, and keeps each log under DIR/logs
by its sha256. `fold` exits 0 only when every selected blocking gate's latest
record for H is `pass`; a missing, `fail` or `error` record refuses.

Promotion (design 4.5 and 4.7): a row's shadow window opens at the later of its
`shadow_since` and its latest event in DIR/standing.jsonl, one JSON line
{"gate", "event", "at"} per upheld false alarm (`false-alarm`) or failed
proof (`proof-failed`). A row with an open window blocks only once the window
spans 14 days and holds 50 fresh `pass` or `fail` records with a `started`
time in it, whichever is later, so it has 0 upheld false alarms in it by
construction; until then its red is reported as `shadow`, never a block. A row
with no window is established and blocks, so an empty store never demotes it.

Exit status: 0 pass, 1 refused, 2 could not decide (bad registry, git failure).
"""

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

REGISTRY = ".github/gates.toml"
ROW_KEYS = {"id", "inputs", "run", "tools", "blocks", "kind", "shadow_since", "scoped"}
REVIEW_KEYS = {"lens", "reviewers", "budget"}
KINDS = {"check", "review", "docs"}
VERDICTS = {"pass", "fail", "error"}
EVENTS = {"false-alarm", "proof-failed"}
SHADOW_DAYS, SHADOW_RUNS = 14, 50
NO_BASE = "0" * 40
RUNNER = f"{platform.system()}-{platform.machine()}".lower()
PROBE_TIMEOUT = 60


class Undecided(Exception):
    """The runner could not establish an answer; never read as a pass."""


def git(*args):
    proc = subprocess.run(["git", *args], capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()
        raise Undecided(f"git {' '.join(args)}: {err}")
    return proc.stdout


def glob_re(glob):
    """`**` spans directories, `*` and `?` stay within one path segment."""
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def load(head):
    try:
        text = git("show", f"{head}:{REGISTRY}").decode()
        rows = tomllib.loads(text).get("gate", [])
    except tomllib.TOMLDecodeError as e:
        raise Undecided(f"{REGISTRY}: {e}") from e
    if not rows:
        raise Undecided(f"{REGISTRY} lists no gates")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise Undecided(f"{REGISTRY}: `gate` must be an array of [[gate]] tables")
    seen = set()
    for row in rows:
        gid = row.get("id")
        # An unknown key is refused because a misspelt `tools` would drop the
        # probes from the key and reuse a verdict across a tool bump.
        known = ROW_KEYS | (REVIEW_KEYS if row.get("kind") == "review" else set())
        problems = [f"unknown key {k!r}" for k in sorted(set(row) - known)]
        if not isinstance(gid, str) or not gid:
            problems.append("no id")
        elif gid in seen:
            problems.append("duplicate id")
        inputs = row.get("inputs")
        if not (
            isinstance(inputs, list)
            and inputs
            and all(isinstance(g, str) and g for g in inputs)
        ):
            problems.append("inputs must be a non-empty list of globs")
        kind = row.setdefault("kind", "check")
        if kind not in KINDS:
            problems.append(f"kind must be one of {sorted(KINDS)}")
        elif kind != "check":
            if "run" in row:
                problems.append(f"a {kind} row's verdict is recorded, so it has no run")
        elif not isinstance(row.get("run"), str) or not row["run"].strip():
            problems.append("no run command")
        since = row.get("shadow_since")
        if since is not None and (
            not isinstance(since, datetime.date) or isinstance(since, datetime.datetime)
        ):
            problems.append("shadow_since must be a date such as 2026-10-19")
        tools = row.get("tools", {})
        if not (
            isinstance(tools, dict) and all(isinstance(v, str) for v in tools.values())
        ):
            problems.append("tools must map a name to a probe command")
        for flag in ("blocks", "scoped"):
            if not isinstance(row.get(flag, True), bool):
                problems.append(f"{flag} must be true or false")
        if problems:
            raise Undecided(f"{REGISTRY}: gate {gid!r}: {'; '.join(problems)}")
        seen.add(gid)
        row["_globs"] = [glob_re(g) for g in inputs]
    return rows


def public(row):
    return {k: v for k, v in row.items() if not k.startswith("_")}


def matches(row, path):
    return any(r.match(path) for r in row["_globs"])


def select(rows, base, head):
    """{gate id: why it was selected}, in registry order."""
    everyone = dict.fromkeys(r["id"] for r in rows)
    if base == NO_BASE:
        return dict.fromkeys(everyone, "no base to compare with")
    # --no-renames, so a rename names the path it left as well as the one it took.
    out = git("diff", "--name-only", "--no-renames", "-z", f"{base}...{head}")
    changed = [p for p in out.decode().split("\0") if p]
    if not changed:
        return dict.fromkeys(
            everyone, "no change to compare, so nothing is proved unaffected"
        )
    why, wide, registry_changed = {}, None, False
    for path in changed:
        hits = [r["id"] for r in rows if matches(r, path)]
        for gid in hits:
            why.setdefault(gid, path)
        registry_changed |= path == REGISTRY
        if wide is None and path.startswith(".github/"):
            wide = f"{path} can change how every gate runs"
        elif wide is None and not hits:
            wide = f"{path} is named by no gate"
    if wide is None:
        return {gid: why[gid] for gid in everyone if gid in why}
    # A scoped row is the slow one, so only its inputs or its own definition select it.
    scoped = set() if registry_changed else {r["id"] for r in rows if r.get("scoped")}
    return {
        gid: why[gid] if gid in scoped else wide
        for gid in everyone
        if gid not in scoped or gid in why
    }


def probe(row):
    versions = {}
    for name, command in sorted(row.get("tools", {}).items()):
        try:
            proc = subprocess.run(
                ["bash", "-c", command], capture_output=True, timeout=PROBE_TIMEOUT
            )
        except subprocess.TimeoutExpired as e:
            raise Undecided(f"{row['id']}: tool probe {name!r} timed out") from e
        if proc.returncode:
            raise Undecided(
                f"{row['id']}: tool probe {name!r} exited {proc.returncode}"
            )
        versions[name] = proc.stdout.decode(errors="replace").strip()
    return versions


def key(row, head, tools):
    entries = []
    for line in git("ls-tree", "-r", "-z", head).decode().split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            mode, _kind, blob = meta.split()
            if matches(row, path):
                entries.append([path, mode, blob])
    material = {
        "gate": row["id"],
        "row": public(row),
        "runner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "runner_class": RUNNER,
        "tools": tools,
        "inputs": sorted(entries),
    }
    blob = json.dumps(
        material, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def records(store, strict):
    """Every record in the store; a damaged line refuses when `strict`, else is skipped."""
    path = store / "records.jsonl"
    if not path.exists():
        return []
    out = []
    for n, raw in enumerate(path.read_bytes().splitlines(), 1):
        try:
            rec = json.loads(raw.decode())
            if not isinstance(rec, dict) or not all(
                isinstance(rec.get(k), str) for k in ("gate", "head", "verdict")
            ):
                raise ValueError("not a verdict record")
        except ValueError as e:
            # Skipping a damaged line could hide an `error` that came after a
            # `pass`, so the fold refuses it; the cache only loses a hit.
            if strict:
                raise Undecided(f"{path}:{n}: {e}") from e
            continue
        out.append(rec)
    return out


def when(text):
    try:
        t = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else None


def events(store):
    """{gate id: time of its latest upheld false alarm or failed proof}."""
    path = store / "standing.jsonl"
    if not path.exists():
        return {}
    latest = {}
    for n, raw in enumerate(path.read_bytes().splitlines(), 1):
        try:
            ev = json.loads(raw.decode())
            at = when(ev.get("at")) if isinstance(ev, dict) else None
            if (
                at is None
                or not isinstance(ev.get("gate"), str)
                or ev.get("event") not in EVENTS
            ):
                raise ValueError("not a standing event")
        except ValueError as e:
            # A line that cannot be read may be a demotion, so nothing is decided.
            raise Undecided(f"{path}:{n}: {e}") from e
        latest[ev["gate"]] = max(at, latest.get(ev["gate"], at))
    return latest


def standing(row, recs, latest_event, now):
    """(whether the row blocks, why not when it does not)."""
    if not row.get("blocks", True):
        return False, "blocks = false"
    starts = [latest_event] if latest_event else []
    if row.get("shadow_since"):
        starts.append(
            datetime.datetime.combine(
                row["shadow_since"], datetime.time(), datetime.UTC
            )
        )
    if not starts:
        return True, None
    start = max(starts)
    runs = 0
    for rec in recs:
        t = when(rec.get("started"))
        # A reuse or an `error` judged nothing, so neither earns promotion.
        fresh = not rec.get("reused_from") and rec["verdict"] in ("pass", "fail")
        if rec["gate"] == row["id"] and fresh and t and t >= start:
            runs += 1
    days = (now - start).days
    if days >= SHADOW_DAYS and runs >= SHADOW_RUNS:
        return True, None
    return False, (
        f"in shadow since {start:%Y-%m-%d}: {runs} of {SHADOW_RUNS} runs,"
        f" {max(days, 0)} of {SHADOW_DAYS} days"
    )


def write(store, rec):
    store.mkdir(parents=True, exist_ok=True)
    # One write of one short line in append mode, so concurrent runs interleave whole lines.
    with open(store / "records.jsonl", "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def keep_log(store, data):
    digest = hashlib.sha256(data).hexdigest()
    logs = store / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{digest}.log").write_bytes(data)
    return f"sha256:{digest}"


def run_one(row, head, base, store, timeout):
    started = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = {
        "schema": 1,
        "gate": row["id"],
        "head": head,
        "base": base,
        "runner": RUNNER,
        "started": started,
    }
    try:
        tools = probe(row)
        rec.update(tools=tools, key=key(row, head, tools))
    except Undecided as e:
        return {
            **rec,
            "verdict": "error",
            "key": None,
            "detail": str(e),
            "reused_from": None,
        }
    for old in records(store, strict=False):
        if old.get("key") == rec["key"] and old["verdict"] == "pass":
            return {
                **rec,
                "verdict": "pass",
                "reused_from": old["head"],
                "log": old.get("log"),
                "duration_ms": 0,
            }
    t0 = time.monotonic()
    proc = subprocess.Popen(
        ["bash", "-e", "-c", row["run"]],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
        verdict, detail = (
            ("pass" if proc.returncode == 0 else "fail"),
            f"exit {proc.returncode}",
        )
    except subprocess.TimeoutExpired:
        # A grandchild holding the pipe would block the next read, so the whole group dies first.
        os.killpg(proc.pid, signal.SIGKILL)
        output, _ = proc.communicate()
        verdict, detail = "error", f"timed out after {timeout}s"
    return {
        **rec,
        "verdict": verdict,
        "detail": detail,
        "reused_from": None,
        "log": keep_log(store, output),
        "duration_ms": round((time.monotonic() - t0) * 1000),
    }


def fold(rows, chosen, head, store):
    """(every selected blocking gate passed, one line per selected gate)."""
    latest, recs, ledger = {}, records(store, strict=True), events(store)
    now = datetime.datetime.now(datetime.UTC)
    for rec in recs:
        if rec["head"] == head:
            latest[rec["gate"]] = rec
    ok, lines = True, []
    for row in rows:
        gid = row["id"]
        # A review that sat on H folds even where selection left its row out, so a
        # block found by a row the review router convened is never dropped.
        if gid not in chosen and not (row["kind"] == "review" and gid in latest):
            continue
        rec = latest.get(gid)
        verdict = rec["verdict"] if rec else None
        if verdict == "pass":
            via = (
                f" (reused from {rec['reused_from'][:12]})"
                if rec.get("reused_from")
                else ""
            )
            lines.append(f"pass {gid}{via}")
            continue
        why = (
            "no verdict for this head"
            if rec is None
            else f"{verdict}: {rec.get('detail', '')}".rstrip(": ")
        )
        if verdict is not None and verdict not in VERDICTS:
            why = f"unknown verdict {verdict!r}"
        blocks, shadow = standing(row, recs, ledger.get(gid), now)
        if blocks:
            ok = False
            lines.append(f"FAIL {gid}: {why}")
        else:
            lines.append(f"shadow {gid}: {why} ({shadow})")
    return ok, lines


def main():
    ap = argparse.ArgumentParser(
        description="Select, run and fold this repository's gates."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("select", "run", "fold"):
        p = sub.add_parser(name)
        p.add_argument("--base", required=True)
        if name != "run":
            p.add_argument("--head", default="HEAD")
        if name != "select":
            p.add_argument("--store", required=True, type=Path)
    sub.choices["run"].add_argument("--timeout", type=int, default=3600)
    k = sub.add_parser("key")
    k.add_argument("id")
    k.add_argument("--head", default="HEAD")
    args = ap.parse_args()

    try:
        head = (
            git("rev-parse", "--verify", f"{getattr(args, 'head', 'HEAD')}^{{commit}}")
            .decode()
            .strip()
        )
        rows = load(head)
        if args.cmd == "key":
            row = next((r for r in rows if r["id"] == args.id), None)
            if row is None:
                raise Undecided(f"{REGISTRY} has no gate {args.id!r}")
            print(key(row, head, probe(row)))
            return 0
        base = args.base
        if base != NO_BASE:
            base = git("rev-parse", "--verify", f"{base}^{{commit}}").decode().strip()
        chosen = select(rows, base, head)
        if args.cmd == "select":
            for gid, why in chosen.items():
                print(f"{gid}\t{why}")
            return 0
        if args.cmd == "run":
            # A verdict is recorded against H's committed tree, so it must be what ran.
            if git("status", "--porcelain").strip():
                raise Undecided(
                    "the checkout has uncommitted or untracked changes, so a verdict would not describe HEAD"
                )
            failed, ledger = False, events(args.store)
            now = datetime.datetime.now(datetime.UTC)
            for row in rows:
                if row["id"] in chosen and row["kind"] != "check":
                    print(f"await {row['id']}: its {row['kind']} verdict is recorded")
                elif row["id"] in chosen:
                    rec = run_one(row, head, base, args.store, args.timeout)
                    write(args.store, rec)
                    blocks, _ = standing(
                        row,
                        records(args.store, strict=False),
                        ledger.get(row["id"]),
                        now,
                    )
                    failed |= rec["verdict"] != "pass" and blocks
                    via = (
                        f" (reused from {rec['reused_from'][:12]})"
                        if rec["reused_from"]
                        else ""
                    )
                    print(f"{rec['verdict']} {row['id']}{via}")
            return 1 if failed else 0
        ok, lines = fold(rows, chosen, head, args.store)
        print("\n".join(lines))
        print(
            f"fold: {len(chosen)} selected at {head[:12]}, {'every blocking gate passed' if ok else 'refused'}"
        )
        return 0 if ok else 1
    except Undecided as e:
        print(f"gate: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
