#!/usr/bin/env python3
"""Refuse a change to the protected surface: the files that grant an agent authority.

Usage: permissions-diff.py --base B [--head H]

Reads `git diff B...H` in the current repository and names every changed path
on SURFACE, the protected surface of security design 7.2 as it exists today
(README.md, "The permissions-diff gate", owns the mapping and what it leaves
out). Most entries are read whole; a workflow is read only for its
`permissions:` blocks, the routing table only for the tables that say which
vendor may see which repository, and a gate registry only for its existing
rows, so an edit beside them is not refused. A new registry row is not either.

Every change it names is refused, a narrowing included: telling one from a
widening is the review's job, and the refusal is what sizes the change L and
opens a class-2 decision. A base of forty zeros compares with the empty tree.

Exit status: 0 nothing protected changed, 1 refused, 2 could not decide.
"""

import argparse
import re
import subprocess
import sys
import tomllib

from gate import glob_re

NO_BASE = "0" * 40
DECISION = (
    "an automatic change may not land them: size the change L and open a"
    " class-2 decision"
)


class Undecided(Exception):
    pass


def git(*args):
    proc = subprocess.run(["git", *args], stdin=subprocess.DEVNULL, capture_output=True)
    if proc.returncode:
        err = proc.stderr.decode(errors="replace").strip()
        raise Undecided(f"git {' '.join(args)}: {err}")
    return proc.stdout


def blob(rev, path):
    """The file's text at rev, or None when it is absent there."""
    if not git("--literal-pathspecs", "ls-tree", "-z", rev, "--", path):
        return None
    return git("show", f"{rev}:{path}").decode(errors="replace")


class Unreadable(Exception):
    """A protected file whose content cannot be compared, so its change is named."""


def toml(text, path):
    try:
        return tomllib.loads(text or "")
    except tomllib.TOMLDecodeError as e:
        raise Unreadable(f"{path} cannot be read as TOML ({e})") from e


def tables(*names):
    def changed(path, old, new):
        a, b = toml(old, path), toml(new, path)
        return [f"[{n}]" for n in names if a.get(n) != b.get(n)]

    return changed


def rows(pick):
    """An existing row deleted or edited; `pick` chooses which rows are protected."""

    def changed(path, old, new):
        def by_id(text):
            gates = toml(text, path).get("gate", [])
            if not isinstance(gates, list):
                raise Unreadable(f"{path}: `gate` is not an array of tables")
            return {r.get("id"): r for r in gates if isinstance(r, dict)}

        before, after = by_id(old), by_id(new)
        out = []
        for gid, row in before.items():
            if not pick(row):
                continue
            if gid not in after:
                out.append(f"row {gid!r} deleted")
            elif after[gid] != row:
                keys = sorted(
                    k
                    for k in set(row) | set(after[gid])
                    if row.get(k) != after[gid].get(k)
                )
                out.append(f"row {gid!r} changed ({', '.join(keys)})")
        return out

    return changed


PERMISSIONS = re.compile(r"(\s*)permissions\s*:")


def permission_blocks(text):
    """Each `permissions:` key with its nested lines, comments and blank lines dropped."""
    lines = [re.sub(r"(^|\s)#.*", "", line).rstrip() for line in text.splitlines()]
    blocks = []
    for i, line in enumerate(lines):
        m = PERMISSIONS.match(line)
        if not m:
            continue
        indent, block = len(m.group(1)), [line.strip()]
        for nested in lines[i + 1 :]:
            if not nested:
                continue
            if len(nested) - len(nested.lstrip()) <= indent:
                break
            block.append(nested.strip())
        blocks.append(block)
    return blocks


def workflow(path, old, new):
    a, b = permission_blocks(old or ""), permission_blocks(new or "")
    return [] if a == b else ["a permissions: block changed"]


# (glob, what the matching file grants, how its change is read: None names any change).
SURFACE = [
    ("**/.claude/settings*.json", "Claude Code settings", None),
    ("**/managed-settings.json", "Claude Code managed settings", None),
    ("**/managed-settings.d/**", "Claude Code managed settings", None),
    ("**/claude-code/config/settings*.json", "the deployed Claude Code settings", None),
    ("internal/sandbox/profile.go", "the worker sandbox profile", None),
    ("internal/permit/permit.go", "the fac permit deny overlay", None),
    ("route.toml", "the routing table", tables("tiers", "repos", "vendors")),
    ("**/github-apps/roles.json", "the GitHub App roles the minter subsets", None),
    ("**/github-apps/app-token.sh", "the GitHub App token minter", None),
    ("**/github-apps/register-app.py", "the GitHub App manifests", None),
    (".github/workflows/*.yml", "a workflow token's permissions", workflow),
    (".github/workflows/*.yaml", "a workflow token's permissions", workflow),
    (".github/ruleset*.json", "a branch ruleset", None),
    (".github/rulesets/**", "a branch ruleset", None),
    (".github/gates.toml", "the gate registry", rows(lambda row: True)),
    (
        ".ci/gate-registry.toml",
        "the gate proof's Security rows",
        rows(lambda row: row.get("surface") == "Security"),
    ),
    ("outbound/outbound.go", "the outbound text pattern set", None),
    ("**/memory-write-guard.sh", "the memory write guard", None),
    ("**/captain.md", "the captain's standing instructions", None),
    # Without this entry an automatic change could drop a line of SURFACE and pass.
    ("run/permissions-diff.py", "this gate's protected surface", None),
]
SURFACE = [(glob_re(g), what, how) for g, what, how in SURFACE]


def findings(base, head):
    if base == NO_BASE:
        old = git("hash-object", "-t", "tree", "--stdin").decode().strip()
    else:
        old = git("merge-base", base, head).decode().strip()
    out = git("diff", "--name-only", "--no-renames", "-z", old, head)
    changed = [p for p in out.decode().split("\0") if p]
    found = []
    for path in changed:
        entry = next((e for e in SURFACE if e[0].match(path)), None)
        if entry is None:
            continue
        _, what, how = entry
        if how is None:
            found.append(f"{path} changes {what}")
            continue
        try:
            details = how(path, blob(old, path), blob(head, path))
        except Unreadable as e:
            details = [str(e)]
        if details:
            found.append(f"{path} changes {what}: {'; '.join(details)}")
    return changed, found


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    args = ap.parse_args()
    try:
        head = git("rev-parse", "--verify", f"{args.head}^{{commit}}").decode().strip()
        base = args.base
        if base != NO_BASE:
            base = git("rev-parse", "--verify", f"{base}^{{commit}}").decode().strip()
        changed, found = findings(base, head)
    except Undecided as e:
        print(f"permissions-diff: could not decide: {e}", file=sys.stderr)
        return 2
    for line in found:
        print(f"permissions-diff: {line}")
    if found:
        print(
            f"permissions-diff: refused: {len(found)} change(s) to the protected"
            f" surface; {DECISION}"
        )
        return 1
    print(
        f"permissions-diff: {len(changed)} changed path(s), none on the protected surface"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
