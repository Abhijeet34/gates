#!/usr/bin/env python3
"""Rows over what a change does to a repository's declared contracts, migrations and facts.

Usage: change-gates.py contracts|migrations|facts [--base B] [root]    (default root: the current directory)

A repository declares what each row judges in `.github/changes.toml` at its
root; README.md, "The change rows", owns the shape and why it is this shape.
With a `--base`, `contracts` and `migrations` read the declarations as the base
has them, so a change cannot rewrite what judges it; `facts` reads the working
tree's, because the declaration is where a change records its decision.
`--base` is the commit the change is measured from (`git diff B...HEAD`, and a
base of forty zeros is the empty tree); `contracts` with a `sources` list and
`migrations` need it, `facts` uses it to sweep only what the change just
superseded and sweeps every declaration without it.

contracts    A `[[contract]]` names a `snapshot` file and either `sources`, globs of
             the files that define the contract, or `command`, which prints it.
             With `sources` a change to one of them is refused unless the same
             change touches the snapshot; with `command` the printed text must equal
             the snapshot byte for byte, whatever changed.
migrations   A `[[migrations]]` names a `dir`, a `pattern` (default `*.sql`) and
             optionally `dry_run`. A migration file already on the base may not be
             edited or deleted. A change that adds one runs the dry run: `sqlite`
             applies every migration of the directory in name order to a scratch
             database, and any other value is a shell command run at the root with
             SCRATCH (an empty directory of its own) and NEW_MIGRATIONS (the added
             paths, one per line) in its environment.
facts        A `[[fact]]` names an `id`, what is `now` true, the superseded `was`
             statements, the `decision` that superseded them and optionally `allow`,
             globs of files that may still quote the old statement as history. Every
             other tracked text file that states one is named, with its line.

Exit status: 0 nothing to name, 1 a finding, 2 could not decide. Every exit 2
prints `change-gates: refused (TYPE): ...`, and TYPE is one of:

undeclared        no `.github/changes.toml`, or none of the table the row judges (at the
                  base, when the row reads the base's).
bad-declaration   the file does not parse or a table is malformed.
no-base           a row that compares with a base was given none.
missing-tool      a declared dry-run command is not on the machine (exit 127).
tool-error        a declared command timed out, or git failed.
outside-tree      a snapshot, migration directory or migration is, or sits behind, a
                  symlink that leaves the tree.
"""

import argparse
import fnmatch
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

# The import would leave run/__pycache__ in the gates checkout, where the gate
# proof reads every file under run/ as a gate.
sys.dont_write_bytecode = True
from gate import glob_re  # noqa: E402

CONFIG = ".github/changes.toml"
NO_BASE = "0" * 40
TIMEOUT = 120
# A declared command is repository code: it gets the program path and locale, and
# an empty HOME, not the runner's credentials.
COMMAND_ENV = ("PATH", "LANG", "TERM", "TMPDIR")
MAX_FILE = 1_000_000
SQLITE_ATTACH = 24


class Refused(Exception):
    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind


def git(root, *args, check=True):
    proc = subprocess.run(
        ["git", *args], cwd=root, stdin=subprocess.DEVNULL, capture_output=True
    )
    if check and proc.returncode:
        err = proc.stderr.decode(errors="replace").strip()
        raise Refused("tool-error", f"git {' '.join(args)}: {err}")
    return proc.stdout if proc.returncode == 0 else None


def changes(root, base):
    """{path: status letter} for `git diff base...HEAD`, a rename counted as a delete and an add."""
    if base is None:
        raise Refused("no-base", "this row compares with a base: pass --base")
    if base == NO_BASE:
        empty = git(root, "hash-object", "-t", "tree", "/dev/null").decode().strip()
        spec = [empty, "HEAD"]
    else:
        spec = [f"{base}...HEAD"]
    out = git(root, "diff", "--name-status", "--no-renames", "-z", *spec).decode()
    parts = out.split("\0")[:-1]
    return {
        path: status[0] for status, path in zip(parts[0::2], parts[1::2], strict=True)
    }


def declarations(text, table):
    try:
        found = tomllib.loads(text).get(table, [])
    except tomllib.TOMLDecodeError as e:
        raise Refused("bad-declaration", f"{CONFIG}: {e}") from e
    if not isinstance(found, list) or not all(isinstance(d, dict) for d in found):
        raise Refused(
            "bad-declaration", f"{CONFIG}: `{table}` must be [[{table}]] tables"
        )
    return found


def read_config(root, table, rev=None):
    """The declarations of `table` in the working tree, or at `rev`; None when the file is absent."""
    if rev is None:
        path = Path(root) / CONFIG
        text = path.read_text() if path.is_file() else None
    else:
        raw = git(root, "show", f"{rev}:{CONFIG}", check=False)
        text = raw.decode() if raw is not None else None
    return None if text is None else declarations(text, table)


def need(root, table, base=None):
    """The declarations of `table` at `base`, or in the working tree when there is no commit to read."""
    rev = None if base in (None, NO_BASE) else base
    found = read_config(root, table, rev)
    if not found:
        where = "" if rev is None else " at the base"
        raise Refused("undeclared", f"{CONFIG} declares no [[{table}]]{where}")
    return found


def inside(root, rel):
    """`rel` under root, refused when a symlink on its way leaves the tree."""
    path = Path(root) / rel
    real = path.resolve()
    if real != Path(root) and Path(root) not in real.parents:
        raise Refused("outside-tree", f"{rel} resolves outside the tree, to {real}")
    return path


def strings(decl, key, table, required=True):
    value = decl.get(key, [] if not required else None)
    ok = isinstance(value, list) and all(
        isinstance(v, str) and v.strip() for v in value
    )
    if not ok or (required and not value):
        raise Refused(
            "bad-declaration",
            f"{CONFIG}: [[{table}]] {decl.get('id')!r}: {key} must be a non-empty list of strings",
        )
    return value


def text_key(decl, key, table, required=True):
    value = decl.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise Refused(
            "bad-declaration",
            f"{CONFIG}: [[{table}]] {decl.get('id')!r}: {key} must be a non-empty string",
        )
    return value


def run_command(root, command, env=None):
    kept = {k: v for k, v in os.environ.items() if k in COMMAND_ENV or k[:3] == "LC_"}
    try:
        with tempfile.TemporaryDirectory(prefix="change-gates.") as home:
            proc = subprocess.run(
                ["bash", "-e", "-c", command],
                cwd=root,
                env={**kept, "HOME": home, **(env or {})},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=TIMEOUT,
            )
    except subprocess.TimeoutExpired as e:
        raise Refused("tool-error", f"{command!r} timed out after {TIMEOUT}s") from e
    if proc.returncode == 127:
        raise Refused(
            "missing-tool",
            f"{command!r} exited 127: a program it calls is not installed",
        )
    return proc


def contracts(root, base):
    found = []
    for decl in need(root, "contract", base):
        cid = text_key(decl, "id", "contract")
        snapshot = text_key(decl, "snapshot", "contract")
        sources, command = decl.get("sources"), decl.get("command")
        if (sources is None) == (command is None):
            raise Refused(
                "bad-declaration",
                f"{CONFIG}: [[contract]] {cid!r}: name exactly one of sources and command",
            )
        shot = inside(root, snapshot)
        if not shot.is_file():
            found.append(f"contracts: {cid}: the snapshot {snapshot} does not exist")
            continue
        if command is not None:
            command = text_key(decl, "command", "contract")
            proc = run_command(root, command)
            if proc.returncode:
                raise Refused(
                    "tool-error",
                    f"contract {cid!r}: {command!r} exited {proc.returncode}",
                )
            if proc.stdout != shot.read_bytes():
                found.append(
                    f"contracts: {cid}: the output of {command!r} differs from {snapshot}"
                    f" (first at line {first_difference(proc.stdout, shot.read_bytes())})"
                )
            continue
        globs = [glob_re(g) for g in strings(decl, "sources", "contract")]
        changed = changes(root, base)
        touched = sorted(
            p for p in changed if p != snapshot and any(g.match(p) for g in globs)
        )
        if touched and snapshot not in changed:
            found += [
                f"contracts: {cid}: {p} changed and {snapshot} did not" for p in touched
            ]
    return found, "every declared contract matches its snapshot"


def first_difference(a, b):
    la, lb = a.split(b"\n"), b.split(b"\n")
    return next(
        (i for i, (x, y) in enumerate(zip(la, lb, strict=False), 1) if x != y),
        min(len(la), len(lb)) + 1,
    )


def sqlite_dry_run(root, files):
    """The first file that fails when each is applied to a scratch database in name order."""
    with tempfile.TemporaryDirectory(prefix="change-gates.") as scratch:
        db = sqlite3.connect(Path(scratch) / "scratch.db")
        # A migration is repository code, so it may not reach another file of the machine.
        db.set_authorizer(
            lambda action, *_: (
                sqlite3.SQLITE_DENY if action == SQLITE_ATTACH else sqlite3.SQLITE_OK
            )
        )
        try:
            for name in files:
                try:
                    db.executescript(inside(root, name).read_text())
                except (sqlite3.Error, UnicodeDecodeError) as e:
                    return name, str(e)
        finally:
            db.close()
    return None, None


def in_folder(path, folder, pattern):
    head, _, name = path.rpartition("/")
    return head == folder and fnmatch.fnmatchcase(name, pattern)


def migrations(root, base):
    found, ran = [], 0
    changed = changes(root, base)
    for decl in need(root, "migrations", base):
        mid = text_key(decl, "id", "migrations")
        folder = text_key(decl, "dir", "migrations").strip("/")
        pattern = text_key(decl, "pattern", "migrations", required=False) or "*.sql"
        dry_run = text_key(decl, "dry_run", "migrations", required=False)

        for path, status in sorted(changed.items()):
            if in_folder(path, folder, pattern) and status != "A":
                verb = "deleted" if status == "D" else "edited"
                found.append(
                    f"migrations: {mid}: {path} was {verb}; it is already on the base,"
                    " so a fix is a new migration"
                )
        added = sorted(
            p for p, s in changed.items() if s == "A" and in_folder(p, folder, pattern)
        )
        if not added or dry_run is None:
            continue
        ran += 1
        for path in added:
            inside(root, path)
        if dry_run == "sqlite":
            every = sorted(
                f"{folder}/{p.name}"
                for p in inside(root, folder).iterdir()
                if p.is_file() and fnmatch.fnmatchcase(p.name, pattern)
            )
            name, err = sqlite_dry_run(root, every)
            if name:
                found.append(
                    f"migrations: {mid}: {name} failed in a scratch database: {err}"
                )
            continue
        with tempfile.TemporaryDirectory(prefix="change-gates.") as scratch:
            proc = run_command(
                root, dry_run, {"SCRATCH": scratch, "NEW_MIGRATIONS": "\n".join(added)}
            )
        if proc.returncode:
            tail = (
                (proc.stdout + proc.stderr)
                .decode(errors="replace")
                .strip()
                .splitlines()[-3:]
            )
            found.append(
                f"migrations: {mid}: the dry run {dry_run!r} exited {proc.returncode}"
                + "".join(f"\n  {line}" for line in tail)
            )
    return found, f"no landed migration changed; {ran} dry run(s) passed"


def tracked_text(root):
    files = git(root, "ls-files", "-z").decode().split("\0")
    for name in filter(None, files):
        path = Path(root) / name
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE:
            continue
        data = path.read_bytes()
        if b"\0" not in data:
            yield name, data.decode(errors="replace")


def facts(root, base):
    decls = need(root, "fact")
    old = {}
    if base not in (None, NO_BASE):
        before = read_config(root, "fact", rev=base)
        old = {
            d.get("id"): d.get("was", []) for d in before or [] if isinstance(d, dict)
        }
    sweep = []
    for decl in decls:
        fid = text_key(decl, "id", "fact")
        now = text_key(decl, "now", "fact")
        was = strings(decl, "was", "fact")
        decision = text_key(decl, "decision", "fact")
        allow = [glob_re(g) for g in strings(decl, "allow", "fact", required=False)]
        if now in was:
            raise Refused(
                "bad-declaration", f"{CONFIG}: [[fact]] {fid!r}: now is also a was"
            )
        fresh = [w for w in was if w not in old.get(fid, [])]
        sweep += [
            (
                fid,
                w,
                decision,
                allow,
                re.compile(rf"(?<![\w.]){re.escape(w)}(?!\w|\.\d)"),
            )
            for w in fresh
        ]
    found = []
    for name, text in tracked_text(root):
        if name == CONFIG:
            continue
        lines = text.splitlines()
        for fid, was, decision, allow, pattern in sweep:
            if any(g.match(name) for g in allow):
                continue
            found += [
                f"facts: {name}:{n}: still states {was!r}, superseded ({fid}; {decision})"
                for n, line in enumerate(lines, 1)
                if pattern.search(line)
            ]
    return found, f"no tracked file states a superseded fact ({len(sweep)} checked)"


ROWS = {"contracts": contracts, "migrations": migrations, "facts": facts}


def main():
    ap = argparse.ArgumentParser(
        usage="change-gates.py contracts|migrations|facts [--base B] [root]"
    )
    ap.add_argument("row", choices=ROWS)
    ap.add_argument("--base")
    ap.add_argument("root", nargs="?", default=".")
    # Plain parse_args consumes the optional root early on Python before 3.12.7 and rejects it after --base.
    args = ap.parse_intermixed_args()
    root = Path(args.root).resolve()
    try:
        base = None
        if args.base:
            base = args.base
            if base != NO_BASE:
                base = (
                    git(root, "rev-parse", "--verify", f"{base}^{{commit}}")
                    .decode()
                    .strip()
                )
        found, clean = ROWS[args.row](root, base)
    except Refused as e:
        print(f"change-gates: refused ({e.kind}): {e}")
        return 2
    for line in found:
        print(f"change-gates: {line}")
    if found:
        print(f"change-gates: {len(found)} finding(s)")
        return 1
    print(f"change-gates: {clean}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
