#!/usr/bin/env python3
"""A shadow row over the licences of a repository's dependencies and vendored files.

Usage: licence.py [--policy FILE] [root]   (defaults: .github/licence-policy.toml, the current directory)

Every tracked `package-lock.json` and `uv.lock` (none under node_modules) lists
dependencies, and every tracked file under a `vendored` glob of the policy is
copied code. Each gets a licence expression: an npm lockfile entry's `license`;
for a uv.lock package, which records none, the policy's `[declared]` entry; for
a vendored file, its own `SPDX-License-Identifier` in its first 30 lines, else a
`[declared]` path entry. README.md, "The licence row", owns the policy's shape.

The expression is read as SPDX: `OR` takes its best alternative, `AND` its worst
part, and an id is `allowed`, `review` or `refused` by the list that names it.
An id no list names, an expression that does not parse, and a dependency or file
with no licence all need review. A refused licence refuses; one that needs review
refuses until `[reviewed]` records why it was accepted, which no entry can do for
a refused one.

Exit status: 0 every licence allowed or reviewed, 1 a finding, 2 could not
decide. Every exit 2 prints `licence: undecided (TYPE): ...`, and TYPE is one of:

bad-policy   the policy is missing, does not parse, or breaks its shape.
bad-lock     a lockfile does not parse.
tool-error   git failed, or root is not a git checkout.
"""

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

# The import would leave run/__pycache__ in the gates checkout, where the gate
# proof reads every file under run/ as a gate.
sys.dont_write_bytecode = True
import gate  # noqa: E402

POLICY = ".github/licence-policy.toml"
LISTS = ("allowed", "review", "refused")
TABLES = ("declared", "reviewed")
LOCKS = ("package-lock.json", "uv.lock")
# A vendored tree carries its licence text in these files; they are the notice, not code.
NOTICES = re.compile(r"(LICEN[CS]E|COPYING|NOTICE|README)([.-][^/]*)?\Z", re.I)
HEADER_LINES = 30
SPDX = re.compile(r"SPDX-License-Identifier:\s*(.+?)\s*(?:\*/|-->|#\}|$)")
TOKEN = re.compile(r"\(|\)|[^\s()]+")
KEY = re.compile(r"(npm|uv):[^@\s]+(@\S+)?|path:\S+")
RANK = {"allowed": 0, "review": 1, "refused": 2}


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
            if lid in named and named[lid] != k:
                problems.append(f"{lid!r} is in both {named[lid]} and {k}")
            named[lid] = k
    for k in TABLES:
        v = data.get(k, {})
        if not isinstance(v, dict):
            problems.append(f"[{k}] must be a table")
            continue
        for key, val in v.items():
            if not KEY.fullmatch(key):
                problems.append(
                    f"[{k}] key {key!r} is not npm:NAME, uv:NAME or path:GLOB"
                )
            if not isinstance(val, str) or not val.strip():
                problems.append(f"[{k}] {key!r} must name a licence or a reason")
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
        state = lists.get(tok)
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


def npm(path, text):
    """[(key, version, licence or None)] of an npm lockfile."""
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
                )
            )
        return out
    # lockfileVersion 1 records no licence, so each dependency needs a [declared] entry.
    pending = list((data.get("dependencies") or {}).items())
    while pending:
        name, item = pending.pop(0)
        if isinstance(item, dict):
            out.append((f"npm:{name}", item.get("version", "?"), None))
            pending.extend((item.get("dependencies") or {}).items())
    return out


def uv(path, text):
    """[(key, version, None)] of a uv lockfile, which records no licence."""
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
        out.append((f"uv:{p.get('name')}", p.get("version", "?"), None))
    return out


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
    """The finding for one dependency or file, or None when its licence is accepted."""
    if expr is None:
        state, why = "review", missing
    else:
        state, why = verdict(expr, lists)
    if state == "refused":
        return f"refused: {subject}: {why}"
    if state == "review" and reviewed is None:
        hint = "" if expr is None else "; record why it is accepted under [reviewed]"
        return f"review: {subject}: {why}{hint}"
    return None


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
        if name not in LOCKS or "node_modules/" in f"/{rel}":
            continue
        text = (root / rel).read_text(errors="replace")
        deps = npm(rel, text) if name == "package-lock.json" else uv(rel, text)
        for key, version, expr in deps:
            judged += 1
            expr = expr or entry(declared, key, version)
            ok = entry(reviewed, key, version)
            line = judge(
                f"{rel}: {key} {version}", expr, ok, lists,
                "no licence recorded; name it under [declared]",
            )  # fmt: skip
            accepted += line is None and ok is not None
            if line:
                found.append(line)
    for rel in paths:
        if not any(g.match(rel) for g in policy["vendored"]) or NOTICES.match(
            rel.rsplit("/", 1)[-1]
        ):
            continue
        judged += 1
        expr = header(root / rel) or path_entry(declared, rel)
        ok = path_entry(reviewed, rel)
        line = judge(
            f"{rel}: vendored", expr, ok, lists,
            f"no SPDX-License-Identifier in its first {HEADER_LINES} lines and no [declared] path",
        )  # fmt: skip
        accepted += line is None and ok is not None
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
