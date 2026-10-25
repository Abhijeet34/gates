#!/usr/bin/env python3
"""Refuse a change to the protected surface: the files that grant an agent authority.

Usage: permissions-diff.py --base B [--head H]

Reads `git diff B...H` in the current repository and names every changed path
on SURFACE, the protected surface of security design 7.2 as it exists today
(README.md, "The permissions-diff gate", owns the mapping and what it leaves
out). Most entries are read whole; a workflow is read only for its
`permissions:` blocks, the routing table only for the tables that say which
vendor may see which repository, a gate registry only for its existing
rows, and a command, agent or skill only for the front matter keys that can
grant it authority, so an edit beside them is not refused. A new registry row
is not either.

A workflow is read by lines, not parsed as YAML, so it fails closed: a changed
line that spells `permissions` in any other form, opens a flow mapping or
spells a key the block reader does not parse is named, and so is any edit to
a file that uses an anchor or alias, since either can carry a token's grant.

Every change it names is refused, a narrowing included: telling one from a
widening is the review's job, and the refusal is what sizes the change L and
opens a class-2 decision. A base of forty zeros compares with the empty tree.

Exit status: 0 nothing protected changed, 1 refused, 2 could not decide.
"""

import argparse
import difflib
import re
import subprocess
import sys
import tomllib

# The import would leave run/__pycache__ in the gates checkout, where the gate
# proof reads every file under run/ as a gate.
sys.dont_write_bytecode = True
from gate import glob_re  # noqa: E402

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
# A node starts a line, follows `- `, `: ` or `? `, or follows `[`, `{` or `,`.
NODE = r"(?:^\s*|[-:?]\s+|[\[{,]\s*)"
ANCHOR = re.compile(NODE + r"[&*][^\s\[\]{},&*>]")
FLOW = re.compile(NODE + r"\{")
# `key: |`, `- >-`, `key: |2+`: the line that opens a block scalar.
BLOCK_SCALAR = re.compile(
    r"^(\s*)((?:-\s+)*)([^\s#'\"-][^:]*:\s+)?[|>](?:[+-]?[1-9]?|[1-9][+-])\s*$"
)
UNREAD_KEY = [
    (
        re.compile(r"\bpermissions\b"),
        "holds `permissions` outside a plain permissions: key",
    ),
    (FLOW, "opens a flow mapping, which the block reader does not parse"),
    (
        re.compile(r"^\s*\?(\s|$)"),
        "spells an explicit key, which the block reader does not parse",
    ),
    (
        re.compile(r'"[^"]*\\[^"]*"\s*:'),
        "spells an escaped key, which the block reader does not parse",
    ),
]


def uncomment(line):
    """The line without a trailing comment; a line with a quote is kept whole, so a `#` in a string hides nothing."""
    if '"' not in line and "'" not in line:
        line = re.sub(r"(^|\s)#.*", "", line)
    return line.rstrip()


def scalar_bodies(lines):
    """Indexes of the lines inside a block scalar, whose text is data and not YAML nodes."""
    inside, parent = set(), None
    for i, line in enumerate(lines):
        if parent is not None:
            if not line.strip() or len(line) - len(line.lstrip()) > parent:
                inside.add(i)
                continue
            parent = None
        m = BLOCK_SCALAR.match(line)
        if m:
            # A block scalar's body sits deeper than its key's column, or than its dash.
            parent = len(m.group(1) + m.group(2)) if m.group(3) else len(m.group(1))
    return inside


def key_blocks(lines, key):
    """Each line matching `key`, with its more-indented lines after it."""
    blocks = []
    for i, line in enumerate(lines):
        m = key.match(line)
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
    a = [uncomment(line) for line in (old or "").splitlines()]
    b = [uncomment(line) for line in (new or "").splitlines()]
    out = []
    if key_blocks(a, PERMISSIONS) != key_blocks(b, PERMISSIONS):
        out.append("a permissions: block changed")
    skip_a, skip_b = scalar_bodies(a), scalar_bodies(b)
    # Line numbers are kept through dropping blank lines, so a finding names the line.
    a = [(n, line) for n, line in enumerate(a, 1) if line]
    b = [(n, line) for n, line in enumerate(b, 1) if line]
    ops = difflib.SequenceMatcher(
        None, [x for _, x in a], [x for _, x in b], autojunk=False
    )
    changed = []
    for tag, i1, i2, j1, j2 in ops.get_opcodes():
        if tag != "equal":
            changed += [("base line", n, x) for n, x in a[i1:i2]]
            changed += [("line", n, x) for n, x in b[j1:j2]]
    for side, n, line in changed:
        if PERMISSIONS.match(line):
            continue
        out += [f"{side} {n} {why}" for rule, why in UNREAD_KEY if rule.search(line)]
    if changed:
        anchored = [
            ("base line", n) for n, x in a if n - 1 not in skip_a and ANCHOR.search(x)
        ]
        anchored += [
            ("line", n) for n, x in b if n - 1 not in skip_b and ANCHOR.search(x)
        ]
        if anchored:
            side, n = anchored[0]
            out.append(
                f"the file uses a YAML anchor or alias ({side} {n}), so any edit"
                " may change a token's permissions"
            )
    return out


FRONT_MATTER = re.compile(r"---\n(.*?\n)---(?:\n|\Z)", re.S)
# Front matter keys that only describe a command, agent or skill; every other key,
# a quoted spelling of these included, may grant it tools, hooks or a mode.
DESCRIPTIVE = {"name", "description", "argument-hint", "when_to_use", "color", "model"}
TOP_KEY = re.compile(r"()(?=\S)")


def front_matter(text):
    if not (text or "").startswith("---"):
        return []
    m = FRONT_MATTER.match(text)
    if not m:
        return None
    blocks = key_blocks([uncomment(line) for line in m.group(1).splitlines()], TOP_KEY)
    return [b for b in blocks if b[0].split(":", 1)[0] not in DESCRIPTIVE]


def grants(path, old, new):
    a, b = front_matter(old), front_matter(new)
    if None in (a, b):
        raise Unreadable(f"{path}: its front matter is not closed by a --- line")
    return [] if a == b else ["a front matter key that can grant authority changed"]


def source(path, old, new):
    """Go source, read whole; its tests grant nothing."""
    return [] if path.endswith("_test.go") else ["the source changed"]


# Rows proved in .ci/gate-registry.toml whose control is a security control, though
# their surface names what they read; a row on a SECURITY_SURFACES surface is
# protected too: the review agent's security lens and the gate pinning lock.
SECURITY_SURFACES = {"Security", "Code review", "Gate pinning"}
SECURITY_ROWS = {
    "workflow-policy",
    "dependency-advisories",
    "signature-check",
    "exposure-scan",
    "commit-msg",
    "workflow-zizmor",
    "workflow-actionlint",
    "advisories-osv",
    "advisories-osv-undecided",
    "advisories-osv-stale",
    "tool-missing",
    "pr-text-slop",
    "pr-text-em-dash",
    "pr-text-attribution",
    "pr-text-home-path",
    "pr-text-security-marker",
    "licence-refused",
    "licence-review",
    "licence-vendored",
}


def go_declaration(text, name):
    """The top-level `var name` declaration in Go source, to the line closing its brackets."""
    m = re.search(rf"^var\s+{name}\b", text or "", re.M)
    if not m:
        return None
    # The declaration ends where Go would insert its semicolon: a newline outside
    # brackets after an identifier, a literal or a closing bracket.
    i, depth, last = m.start(), 0, ""
    while i < len(text):
        c = text[i]
        if text.startswith("//", i):
            i = text.find("\n", i)
            if i < 0:
                break
            continue
        if text.startswith("/*", i):
            i = text.find("*/", i + 2)
            if i < 0:
                break
            i += 2
            continue
        if c in "\"'`":
            j = i + 1
            while j < len(text) and text[j] != c and (c == "`" or text[j] != "\n"):
                j += 2 if c != "`" and text[j] == "\\" else 1
            i, last = j + 1, c
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "\n" and depth <= 0 and (last.isalnum() or last in "_)]}\"'`"):
            return text[m.start() : i]
        if not c.isspace():
            last = c
        i += 1
    return text[m.start() :]


def declarations(*names):
    """A Go declaration, and every changed line naming it, so an assignment elsewhere is named too."""

    def changed(path, old, new):
        a, b = (old or "").splitlines(), (new or "").splitlines()
        ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
        lines = [
            x for t, i1, i2, j1, j2 in ops if t != "equal" for x in a[i1:i2] + b[j1:j2]
        ]
        out = []
        for name in names:
            if go_declaration(old, name) != go_declaration(new, name):
                out.append(f"the {name} declaration changed")
            elif any(re.search(rf"\b{name}\b", line) for line in lines):
                out.append(f"a line naming {name} changed")
        return out

    return changed


# (glob, what the matching file grants, how its change is read: None names any change).
SURFACE = [
    ("**/.claude/settings*.json", "Claude Code settings", None),
    ("**/managed-settings.json", "Claude Code managed settings", None),
    ("**/managed-settings.d/**", "Claude Code managed settings", None),
    ("**/claude-code/config/settings*.json", "the deployed Claude Code settings", None),
    ("internal/sandbox/profile.go", "the worker sandbox profile", None),
    ("internal/sandbox/*.go", "what the worker sandbox grants", source),
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
        "the gate proof's security rows",
        rows(
            lambda row: (
                row.get("surface") in SECURITY_SURFACES
                or row.get("id") in SECURITY_ROWS
            )
        ),
    ),
    ("policy/**", "the tool rows' ignore lists and the relaxation records", None),
    ("run/tool-gates.py", "the tool rows' pins and flags", None),
    ("run/pr-text.py", "the pull request text pattern set", None),
    ("run/licence.py", "the licence row", None),
    ("run/weakening.py", "the weakening guard", None),
    (".github/licence-policy.toml", "the licence policy and its declarations", None),
    ("**/zizmor.yml", "a tool's in-tree configuration", None),
    ("**/zizmor.yaml", "a tool's in-tree configuration", None),
    ("**/.github/actionlint.yml", "a tool's in-tree configuration", None),
    ("**/.github/actionlint.yaml", "a tool's in-tree configuration", None),
    ("**/osv-scanner.toml", "a tool's in-tree configuration", None),
    ("run/review.py", "the review agent and its verifier's sandbox", None),
    ("run/gate.py", "the gate runner and the globs this gate matches with", None),
    ("run/secret-scan.sh", "the secret scan", None),
    (".gitleaks.toml", "the secret scan's rules and allowlists", None),
    (".ci/test-workflow-policy.sh", "the workflow policy", None),
    ("run/dependency-advisories.sh", "the dependency advisories row", None),
    ("run/dependency-advisories/**", "the dependency advisories row", None),
    (
        "internal/adapter/claude/claude.go",
        "the hosts a boxed run's proxy allows",
        declarations("Egress"),
    ),
    (
        "repos/depbase.go",
        "the hosts a base build's proxy allows",
        declarations("Registries"),
    ),
    ("egress/*.go", "the boxed run's egress proxy", source),
    ("**/.mcp.json", "an agent's MCP servers", None),
    ("**/.claude/commands/**.md", "a command's tool grants", grants),
    ("**/.claude/agents/**.md", "an agent's tool grants", grants),
    ("**/.claude/skills/**/SKILL.md", "a skill's tool grants", grants),
    ("**/claude-code/config/commands/**.md", "a command's tool grants", grants),
    ("**/claude-code/config/agents/**.md", "an agent's tool grants", grants),
    ("**/claude-code/config/skills/**/SKILL.md", "a skill's tool grants", grants),
    ("**/.claude/hooks/**", "a Claude Code hook script", None),
    ("**/claude-code/config/hooks/**", "a Claude Code hook script", None),
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
