#!/usr/bin/env python3
"""A shadow row over the licences of a repository's dependencies and vendored files.

Usage: licence.py [--policy FILE] [root]   (defaults: .github/licence-policy.toml, the current directory)

Every tracked `package-lock.json`, `uv.lock`, `pnpm-lock.yaml` and `Cargo.lock`
(none under node_modules) lists dependencies, and every tracked file under a
`vendored` glob of the policy is copied code. Each gets a licence expression: an
npm lockfile entry's `license`; a pnpm package's from its installed package.json, read only at the path its
lock entry derives; a crate's from `cargo metadata --offline`, run from outside
the tree so that none of the tree's cargo or toolchain configuration applies; for a uv.lock package, which records
none, the policy's `[declared]` entry; for a vendored file, its own
`SPDX-License-Identifier` in its first 30 lines, else a `[declared]` path entry.
README.md, "The licence row", owns the policy's shape.

The expression is read as SPDX: `OR` takes its best alternative, `AND` its worst
part, and an id is `allowed`, `review` or `refused` by the list that names it,
whatever its case. An id no list names, an expression that does not parse, and a
dependency or file with no licence all need review. A refused licence refuses; one
that needs review refuses until `[reviewed]` records the expression it accepts and
why, which no entry can do for a refused one, and an entry accepts only that
expression, so a dependency whose licence has since changed needs review again.

Exit status: 0 every licence allowed or reviewed, 1 a finding, 2 could not
decide. Every exit 2 prints `licence: undecided (TYPE): ...`, and TYPE is one of:

bad-policy   the policy is missing, does not parse, or breaks its shape.
bad-lock     a lockfile does not parse.
tool-error   git failed, root is not a git checkout, or cargo metadata failed.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

# The import would leave run/__pycache__ in the gates checkout, where the gate
# proof reads every file under run/ as a gate.
sys.dont_write_bytecode = True
import gate  # noqa: E402

POLICY = ".github/licence-policy.toml"
LISTS = ("allowed", "review", "refused")
TABLES = ("declared", "reviewed")
# A vendored tree carries its licence text in these files; they are the notice, not code.
NOTICES = re.compile(r"(LICEN[CS]E|COPYING|NOTICE|README)([.-][^/]*)?\Z", re.I)
HEADER_LINES = 30
SPDX = re.compile(r"SPDX-License-Identifier:\s*(.+?)\s*(?:\*/|-->|#\}|$)")
TOKEN = re.compile(r"\(|\)|[^\s()]+")
KEY = re.compile(r"(npm|uv|cargo):[^@\s]+(@\S+)?|path:\S+")
# A pnpm-lock.yaml `packages:` key: two spaces, then name@version, quoted or not.
PNPM_KEY = re.compile(r"  ['\"]?/?(@?[^@\s'\"]+)@([^(\s'\":]+)")
NOT_RECORDED = "no licence recorded; name it under [declared]"
RANK = {"allowed": 0, "review": 1, "refused": 2}
# Environment variables that name a program cargo runs. The tree cannot set them, a caller's shell can.
CARGO_WRAPPERS = ("RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER")


def fold(expr):
    """An SPDX expression compared as the standard does: case-insensitive, spaces collapsed."""
    return " ".join(expr.split()).casefold()


class Undecided(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def policy_of(path):
    """The policy file read and checked against its shape."""
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as e:
        raise Undecided("bad-policy", f"{path.name}: no policy file") from e
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise Undecided("bad-policy", f"{path.name}: {e}") from e
    problems = [
        f"unknown key {k!r}" for k in sorted(set(data) - {*LISTS, *TABLES, "vendored"})
    ]
    for k in (*LISTS, "vendored"):
        v = data.get(k, [])
        if not (
            isinstance(v, list) and all(isinstance(s, str) and s.strip() for s in v)
        ):
            problems.append(f"{k} must be a list of strings")
    named = {}
    for k in LISTS:
        for lid in data.get(k, []) if isinstance(data.get(k), list) else []:
            if fold(lid) in named and named[fold(lid)] != k:
                problems.append(f"{lid!r} is in both {named[fold(lid)]} and {k}")
            named[fold(lid)] = k
    for k in TABLES:
        v = data.get(k, {})
        if not isinstance(v, dict):
            problems.append(f"[{k}] must be a table")
            continue
        for key, val in v.items():
            if not KEY.fullmatch(key):
                problems.append(
                    f"[{k}] key {key!r} is not npm:NAME, uv:NAME, cargo:NAME or path:GLOB"
                )
            if k == "reviewed":
                if not (
                    isinstance(val, dict)
                    and set(val) == {"accepts", "reason"}
                    and all(isinstance(v, str) and v.strip() for v in val.values())
                ):
                    problems.append(
                        f"[{k}] {key!r} must be a table of accepts and reason"
                    )
            elif not isinstance(val, str) or not val.strip():
                problems.append(f"[{k}] {key!r} must name a licence")
    if problems:
        raise Undecided("bad-policy", f"{path.name}: {'; '.join(problems)}")
    return {
        "lists": named,
        "vendored": [gate.glob_re(g) for g in data.get("vendored", [])],
        **{k: data.get(k, {}) for k in TABLES},
    }


def verdict(expr, lists):
    """(allowed|review|refused, why) of an SPDX expression under the policy's lists."""
    tokens = TOKEN.findall(expr)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def atom():
        tok = take() if peek() else None
        if tok == "(":
            got = either()
            if (take() if peek() else None) != ")":
                raise ValueError("an unclosed parenthesis")
            return got
        if tok is None or tok == ")" or tok.upper() in ("AND", "OR", "WITH"):
            raise ValueError(f"{tok or 'the end'} where a licence id belongs")
        if peek() and peek().upper() == "WITH":
            take()
            exc = take() if peek() else None
            if exc is None or exc in ("(", ")"):
                raise ValueError("WITH and no exception")
            # An exception changes the terms, so the pair is judged only as listed.
            tok = f"{tok} WITH {exc}"
        state = lists.get(fold(tok))
        if state is None:
            return "review", f"{tok} is in no list of the policy"
        if state == "review":
            return "review", f"{tok} needs review"
        return state, f"{tok} is {state}"

    def both():
        got = [atom()]
        while peek() and peek().upper() == "AND":
            take()
            got.append(atom())
        return max(got, key=lambda v: RANK[v[0]])

    def either():
        got = [both()]
        while peek() and peek().upper() == "OR":
            take()
            got.append(both())
        return min(got, key=lambda v: RANK[v[0]])

    try:
        result = either()
        if peek() is not None:
            raise ValueError(f"{peek()} after a whole expression")
    except ValueError as e:
        return "review", f"{expr!r} is not an SPDX expression ({e})"
    return result


def licence_field(value):
    """An npm `license` value as one expression; the legacy object and list forms too."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict) and isinstance(value.get("type"), str):
        return value["type"]
    if isinstance(value, list):
        types = [licence_field(v) for v in value]
        if types and all(types):
            return " OR ".join(f"({t})" for t in types)
    return None


def npm(root, path, text):
    """[(key, version, licence or None, why none)] of an npm lockfile."""
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except ValueError as e:
        raise Undecided("bad-lock", f"{path}: {e}") from e
    out = []
    packages = data.get("packages")
    if isinstance(packages, dict):
        for where, item in packages.items():
            # Only a node_modules entry is a dependency: "", a workspace member's own entry and a link are the project's code.
            if (
                "node_modules/" not in where
                or not isinstance(item, dict)
                or item.get("link")
            ):
                continue
            name = item.get("name") or where.rsplit("node_modules/", 1)[-1]
            out.append(
                (
                    f"npm:{name}",
                    item.get("version", "?"),
                    licence_field(item.get("license")),
                    NOT_RECORDED,
                )
            )
        return out
    # lockfileVersion 1 records no licence, so each dependency needs a [declared] entry.
    pending = list((data.get("dependencies") or {}).items())
    while pending:
        name, item = pending.pop(0)
        if isinstance(item, dict):
            out.append((f"npm:{name}", item.get("version", "?"), None, NOT_RECORDED))
            pending.extend((item.get("dependencies") or {}).items())
    return out


def uv(root, path, text):
    """[(key, version, None, why none)] of a uv lockfile, which records no licence."""
    try:
        packages = tomllib.loads(text).get("package", [])
    except tomllib.TOMLDecodeError as e:
        raise Undecided("bad-lock", f"{path}: {e}") from e
    out = []
    for p in packages if isinstance(packages, list) else []:
        source = p.get("source") if isinstance(p, dict) else None
        # An editable or virtual source is the project or a workspace member.
        if not isinstance(p, dict) or (
            isinstance(source, dict) and {"editable", "virtual"} & set(source)
        ):
            continue
        out.append((f"uv:{p.get('name')}", p.get("version", "?"), None, NOT_RECORDED))
    return out


def installed_manifest(modules, name, ver):
    """The package.json `pnpm install` left for name@ver, or None.

    Only the paths the lock entry derives are read: the virtual-store directory
    (with the `_` suffix pnpm adds for peers), or `node_modules/NAME` when hoisted.
    A package.json elsewhere is never taken for it, and neither is one reached
    through a symlink, which could lead out of the tree.
    """
    store = modules / ".pnpm"
    stem = f"{name.replace('/', '+')}@{ver}"
    dirs = []
    if store.is_dir():
        dirs = sorted(
            d.name
            for d in store.iterdir()
            if d.name == stem or d.name.startswith(f"{stem}_")
        )
    derived = [
        *(store / d / "node_modules" / name / "package.json" for d in dirs),
        modules / name / "package.json",
    ]
    for manifest in derived:
        if not manifest.is_file() or manifest.resolve() != manifest:
            continue
        try:
            data = json.loads(manifest.read_text())
        except (OSError, ValueError):
            continue
        found = (
            (data.get("name"), data.get("version")) if isinstance(data, dict) else None
        )
        if found == (name, ver):
            return data
    return None


def pnpm(root, path, text):
    """[(key, version, licence or None, why none)] of a pnpm lockfile, read from the installed tree.

    The lockfile records no licence, but `pnpm install` leaves each package's own
    package.json under node_modules/.pnpm, or under node_modules when hoisted.
    """
    version = re.search(r"^lockfileVersion: ['\"]?(\d+)", text, re.M)
    if not version or version.group(1) not in ("6", "9"):
        raise Undecided("bad-lock", f"{path}: not a lockfileVersion 6 or 9")
    section = re.search(r"^packages:[ \t]*\n(.*?)(?=^\S|\Z)", text, re.M | re.S)
    keys = PNPM_KEY.findall(section.group(1) if section else "")
    modules = (root / path).parent / "node_modules"
    out = []
    for name, ver in dict.fromkeys(keys):
        if ver == "link":
            continue
        data = installed_manifest(modules, name, ver)
        if data is not None:
            expr, why = (
                licence_field(data.get("license")),
                "its package.json names no licence",
            )
        else:
            expr, why = None, "not installed, so no package.json to read"
        out.append((f"npm:{name}", ver, expr, f"{why}; name it under [declared]"))
    return out


def cargo(root, path, text):
    """[(key, version, licence or None, why none)] of a Cargo.lock, read through cargo metadata.

    --offline reads the crates a `cargo fetch --locked` left, so the row makes no
    network call; --all-features resolves every crate the lockfile lists.

    cargo runs in an empty directory beside nothing of the tree's: it takes its
    configuration (a `rustc-wrapper` names a program it then runs) and rustup its
    toolchain override from the working directory, so --manifest-path names the
    crates and the tree configures neither. The toolchain is the runner's own.
    """
    try:
        locked = tomllib.loads(text).get("package", [])
    except tomllib.TOMLDecodeError as e:
        raise Undecided("bad-lock", f"{path}: {e}") from e
    argv = [
        "cargo",
        "metadata",
        "--manifest-path",
        str((root / path).with_name("Cargo.toml")),
        "--offline",
        "--locked",
        "--all-features",
        "--format-version",
        "1",
    ]
    # An empty wrapper, unlike an absent one, overrides a wrapper that cargo configuration names.
    env = {k: v for k, v in os.environ.items() if k != "RUSTC"}
    env.update({k: "" for k in CARGO_WRAPPERS})
    try:
        with tempfile.TemporaryDirectory() as outside:
            proc = subprocess.run(
                argv, cwd=outside, env=env, capture_output=True, text=True
            )
    except FileNotFoundError as e:
        raise Undecided("tool-error", f"{path}: cargo metadata: {e}") from e
    if proc.returncode:
        last = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        raise Undecided(
            "tool-error", f"{path}: cargo metadata exited {proc.returncode}: {last}"
        )
    try:
        crates = {
            (c["name"], c["version"]): c for c in json.loads(proc.stdout)["packages"]
        }
    except (ValueError, KeyError, TypeError) as e:
        raise Undecided("tool-error", f"{path}: cargo metadata: {e}") from e
    out = []
    for p in locked if isinstance(locked, list) else []:
        # No source is a workspace member or a path dependency: the project's own code.
        if not isinstance(p, dict) or not p.get("source"):
            continue
        crate = crates.get((p.get("name"), p.get("version")))
        if crate is None:
            expr, why = None, "not in cargo metadata; name it under [declared]"
        else:
            # The legacy `MIT/Apache-2.0` form, which crates.io still serves, means OR.
            expr = re.sub(r"\s*/\s*", " OR ", crate.get("license") or "") or None
            why = "its Cargo.toml names no SPDX licence; name it under [declared]"
        out.append((f"cargo:{p.get('name')}", p.get("version", "?"), expr, why))
    return out


READERS = {
    "package-lock.json": npm,
    "uv.lock": uv,
    "pnpm-lock.yaml": pnpm,
    "Cargo.lock": cargo,
}


def entry(table, key, version):
    """The table's value for a dependency, its exact version first."""
    return table.get(f"{key}@{version}", table.get(key))


def path_entry(table, path):
    return next(
        (
            v
            for k, v in table.items()
            if k.startswith("path:") and gate.glob_re(k[5:]).match(path)
        ),
        None,
    )


def judge(subject, expr, reviewed, lists, missing):
    """(the finding for one dependency or file or None, whether a [reviewed] entry accepted it)."""
    if expr is None:
        state, why = "review", missing
    else:
        state, why = verdict(expr, lists)
    if state == "refused":
        return f"refused: {subject}: {why}", False
    if state != "review":
        return None, False
    # An entry accepts the expression it records and no other, so a changed licence is reviewed again.
    recorded = reviewed["accepts"] if reviewed and expr is not None else None
    if recorded is not None and fold(recorded) == fold(expr):
        return None, True
    hint = ""
    if expr is not None:
        hint = "; record why it is accepted under [reviewed]"
        if reviewed is not None:
            hint = f"; [reviewed] accepts {reviewed['accepts']}, not {expr}"
    return f"review: {subject}: {why}{hint}", False


def tracked(root):
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise Undecided("tool-error", f"git ls-files in {root}: {e}") from e
    return [p for p in out.decode().split("\0") if p]


def header(path):
    """The SPDX expression a file states in its first lines, or None."""
    with open(path, "rb") as f:
        head = f.read(8192)
    if b"\0" in head:
        return None
    for line in head.decode(errors="replace").splitlines()[:HEADER_LINES]:
        m = SPDX.search(line)
        if m:
            return m.group(1).strip()
    return None


def check(root, policy):
    """(findings, how many dependencies and files were judged, how many by a [reviewed] entry)."""
    lists, declared, reviewed = policy["lists"], policy["declared"], policy["reviewed"]
    found, judged, accepted = [], 0, 0
    paths = tracked(root)
    for rel in paths:
        name = rel.rsplit("/", 1)[-1]
        if name not in READERS or "node_modules/" in f"/{rel}":
            continue
        text = (root / rel).read_text(errors="replace")
        for key, version, expr, missing in READERS[name](root, rel, text):
            judged += 1
            expr = expr or entry(declared, key, version)
            line, by_entry = judge(
                f"{rel}: {key} {version}", expr, entry(reviewed, key, version), lists, missing
            )  # fmt: skip
            accepted += by_entry
            if line:
                found.append(line)
    for rel in paths:
        if not any(g.match(rel) for g in policy["vendored"]) or NOTICES.match(
            rel.rsplit("/", 1)[-1]
        ):
            continue
        judged += 1
        expr = header(root / rel) or path_entry(declared, rel)
        line, by_entry = judge(
            f"{rel}: vendored", expr, path_entry(reviewed, rel), lists,
            f"no SPDX-License-Identifier in its first {HEADER_LINES} lines and no [declared] path",
        )  # fmt: skip
        accepted += by_entry
        if line:
            found.append(line)
    return found, judged, accepted


def main():
    ap = argparse.ArgumentParser(usage="licence.py [--policy FILE] [root]")
    ap.add_argument("--policy")
    ap.add_argument("root", nargs="?", default=".")
    args = ap.parse_intermixed_args()
    root = Path(args.root).resolve()
    try:
        policy = policy_of(Path(args.policy) if args.policy else root / POLICY)
        found, judged, accepted = check(root, policy)
    except Undecided as e:
        print(f"licence: undecided ({e.kind}): {e}")
        return 2
    for line in found:
        print(f"licence: {line}")
    if found:
        refused = sum(f.startswith("refused") for f in found)
        print(f"licence: {refused} refused, {len(found) - refused} need review")
        return 1
    print(
        f"licence: {judged} dependencies and vendored files allowed ({accepted} by a [reviewed] entry)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
