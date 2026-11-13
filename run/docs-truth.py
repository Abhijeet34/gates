#!/usr/bin/env python3
"""Hold a repository's documents to four rules a tree alone can answer.

Usage: docs-truth.py owner|commands|budget|emdash|shape [root]    (default root: the current directory)

owner     Every AGENTS.md and CLAUDE.md is listed in `.github/docs.toml` with an
          `owner` (any non-empty name or role) and a `reader` (agents, compliance
          or clients). Any other Markdown file is judged only when it is listed.
          A row's `path` may be a glob; a row carrying `kind` or `template` and
          neither `owner` nor `reader` only classifies documents for `shape`.
commands  Every command in a shell-tagged fence (bash, sh, shell, zsh) of any
          Markdown file parses under `bash -n`, and its command word exists. It
          exists when it is a bash builtin or keyword, a file of the tree (found
          the way path-truth.py finds a path; executable when run directly, and
          present when handed to bash, sh or python3, which always exist), or a
          program the manifest declares in `tools`. PATH is not consulted, because that would judge
          the machine running the check, and nothing is executed. Not judged: a
          command word holding `$`, a backtick, a glob or `~`, heredoc bodies,
          and every inline span, which path-truth.py owns when it names a path.
budget    The root AGENTS.md is at most BUDGET bytes.
emdash    No Markdown file carries an em dash ("Never use the em dash", the
          maintainer's global agent instructions, and the draft skill's hard rule
          1), except a forked file: upstream text this tree cannot edit without
          the next upstream sync reporting a change upstream never made. A fork
          is the file that carries the `<!-- LOCAL FORK` header the fork policy
          (FORKS.md, "What is forked") puts at the top of a forked SKILL.md, in
          its first FORK_LINES lines, so a quotation of the header deep in a file
          exempts nothing. The factory plan r5 row "Docs and writing, no AI slop"
          records the exemption: "em dashes (forked files exempt)".
shape     Every document has a kind, by its place under docs/ or by a [[doc]]
          row, and holds the `##` headings its template version requires
          (policy/docs/<kind>.<n>.md of this gates tree; `template`, absent means
          1) in order, with no placeholder left in prose. The manifest's keys,
          kinds, surfaces and template numbers are known, every document its
          `surfaces` require exists, and a reference document is a [[contract]]
          snapshot. A document on an older template only prints an `outdated`
          line. README.md, "The documentation rows", owns the rules.

BUDGET is the repository figure of the instruction-surface budgets, "each
repository's under 8,000 bytes" (factory plan r5, section 7 "Budgets", and report
7.2, "a root AGENTS.md of at most 8,000 bytes"). The fleet set every instruction
budget in bytes or lines, none in words, so none is converted. The home file's
5,000 bytes and the 200-line cap stay with size-limit.py. A nested AGENTS.md has
no fleet budget and is not judged.
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib

# The import would leave run/__pycache__ in the gates checkout, where the gate
# proof reads every file under run/ as a gate.
sys.dont_write_bytecode = True
from gate import glob_re  # noqa: E402

NAMES = {"AGENTS.md", "CLAUDE.md"}
ROW_KEYS = {"path", "owner", "reader", "kind", "template"}
TOP_KEYS = {"tools", "surfaces", "doc"}
GLOB = re.compile(r"[*?]")
TEMPLATES = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "policy", "docs"
)
TEMPLATE_FILE = re.compile(r"([a-z]+)\.([1-9][0-9]*)\.md\Z")
# Kinds repo-standard holds; `shape` accepts them in the manifest and judges nothing in them.
ELSEWHERE = {"readme", "agents"}
INDEX = "docs/README.md"
# A file at one of these paths, or anywhere under one of these directories, has the kind by place.
BY_PATH = {
    "docs/getting-started.md": "tutorial",
    "docs/architecture.md": "architecture",
    "docs/operations.md": "runbook",
}
BY_DIR = {
    "docs/how-to/": "howto",
    "docs/reference/": "reference",
    "docs/explanation/": "explanation",
    "docs/decisions/": "decision",
    "docs/postmortems/": "postmortem",
    "docs/spec/": "spec",
}
# What each surface requires (R in README.md, "The documentation rows"): (kind, file name or None, where it goes).
THREAT = ("explanation", "threat-model.md", "docs/explanation/threat-model.md")
REFERENCE = ("reference", None, "docs/reference/<surface>.md")
TUTORIAL = ("tutorial", None, "docs/getting-started.md")
HOWTO = ("howto", None, "docs/how-to/<goal>.md")
ARCHITECTURE = ("architecture", None, "docs/architecture.md")
RUNBOOK = ("runbook", None, "docs/operations.md")
SPEC = ("spec", None, "docs/spec/<subject>.md")
SURFACES = {
    "cli": [REFERENCE],
    "plugin": [REFERENCE],
    "library": [REFERENCE],
    "service": [REFERENCE, ARCHITECTURE, THREAT, RUNBOOK],
    "site": [RUNBOOK],
    "app": [REFERENCE, TUTORIAL, HOWTO, ARCHITECTURE, THREAT, RUNBOOK, SPEC],
}
PLACEHOLDER = re.compile(r"\[\[FILL:|<!--\s*fill:")
COMMENT = re.compile(r"<!--.*?-->")
OPTIONAL = "<!-- optional -->"
HEADING = re.compile(r"^## (.+?)\s*#*\s*$")
SPAN = re.compile(r"(`+).*?\1")
CONTRACTS = ".github/changes.toml"
SKIP_DIRS = {".git", "node_modules", "target", "vendor", ".venv"}
MANIFEST = ".github/docs.toml"
READERS = {"agents", "compliance", "clients"}
BUDGET = 8000
EM_DASH = "\u2014"
FORK_MARKER = "<!-- LOCAL FORK"
FORK_LINES = 30
SHELL_FENCES = {"bash", "sh", "shell", "zsh"}
BASH = shutil.which("bash") or "bash"
INTERPRETERS = {"bash", "sh", "python3"}
FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*(\S*)")
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
UNRESOLVABLE = re.compile(r"[$`*?{}~\\]|^/")
# Boundaries start a new command; any other all-punctuation token is a redirection.
BOUNDARIES = {";", ";;", "&&", "||", "|", "&", "(", ")", "|&"}
# A word after one of these is a command, though no boundary precedes it.
OPENERS = {"if", "then", "elif", "else", "do", "while", "until", "!", "time"}


def walk(root):
    """(relative paths of the Markdown files, every path suffix the tree can be cited by)."""
    docs, suffixes = [], {}
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            rel = os.path.normpath(os.path.relpath(os.path.join(here, name), root))
            parts = rel.split(os.sep)
            for i in range(len(parts)):
                suffixes.setdefault("/".join(parts[i:]), []).append(rel)
            if name.endswith(".md"):
                docs.append(rel)
    return docs, suffixes


def manifest(root):
    """(the manifest's tables, findings): a missing file is an empty manifest."""
    path = os.path.join(root, MANIFEST)
    if not os.path.exists(path):
        return {}, []
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle), []
    except (tomllib.TOMLDecodeError, OSError) as e:
        return {}, [f"{MANIFEST}: cannot be read: {e}"]


def entries(table, docs, root):
    """([(path, row, the docs it matches)], findings) for the manifest's [[doc]] rows."""
    rows = table.get("doc", [])
    if not (isinstance(rows, list) and all(isinstance(r, dict) for r in rows)):
        return [], [f"{MANIFEST}: `doc` must be an array of [[doc]] tables"]
    out, bad, seen = [], [], set()
    for n, row in enumerate(rows, 1):
        where = f"{MANIFEST}: [[doc]] {n}"
        unknown = sorted(set(row) - ROW_KEYS)
        path = row.get("path")
        if unknown:
            bad.append(f"{where}: unknown key {unknown[0]!r}")
        if (
            not isinstance(path, str)
            or not path
            or os.path.isabs(path)
            or ".." in path.split("/")
            or not path.endswith(".md")
        ):
            bad.append(
                f"{where}: path must be a relative Markdown file inside the tree"
            )
            continue
        path = os.path.normpath(path)
        where = f"{MANIFEST}: {path}"
        if path in seen:
            bad.append(f"{where}: listed twice")
        seen.add(path)
        if GLOB.search(path):
            matched = [d for d in docs if glob_re(path).match(d)]
            if not matched:
                bad.append(f"{where}: matches nothing")
        else:
            matched = [path] if os.path.isfile(os.path.join(root, path)) else []
            if not matched:
                bad.append(f"{where}: does not exist")
        out.append((path, row, matched))
    return out, bad


def owner(root):
    docs, _ = walk(root)
    table, bad = manifest(root)
    if bad:
        return bad
    rows, bad = entries(table, docs, root)
    listed = set()
    for path, row, matched in rows:
        # A row that only classifies a document for `shape` names no owner.
        if ("kind" in row or "template" in row) and not {"owner", "reader"} & set(row):
            continue
        listed.update(matched)
        where = f"{MANIFEST}: {path}"
        if not (isinstance(row.get("owner"), str) and row["owner"].strip()):
            bad.append(f"{where}: names no owner")
        if row.get("reader") not in READERS:
            bad.append(f"{where}: reader must be one of {sorted(READERS)}")
    for rel in docs:
        if os.path.basename(rel) in NAMES and rel not in listed:
            bad.append(f"{rel}: names no owner and reader; list it in {MANIFEST}")
    return bad


def budget(root):
    path = os.path.join(root, "AGENTS.md")
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    if size > BUDGET:
        return [
            f"AGENTS.md: {size} bytes, over the {BUDGET}-byte budget for a repository's AGENTS.md"
        ]
    return []


def fences(path):
    """(first line number, lines) of each shell-tagged fenced block."""
    out, block, mark, shell, start = [], [], None, False, 0
    with open(path, encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            m = FENCE.match(line)
            if mark is None:
                if m:
                    mark, shell, block, start = (
                        m.group(1),
                        m.group(2).lower() in SHELL_FENCES,
                        [],
                        number + 1,
                    )
            elif (
                m
                and m.group(1)[0] == mark[0]
                and len(m.group(1)) >= len(mark)
                and not m.group(2)
            ):
                if shell:
                    out.append((start, block))
                mark = None
            elif shell:
                block.append(line.rstrip("\n"))
    return out


def logical_lines(lines, first):
    """(line number, text) with backslash continuations joined."""
    out, text, start = [], "", first
    for offset, line in enumerate(lines):
        if not text:
            start = first + offset
        if line.endswith("\\"):
            text += line[:-1] + " "
            continue
        out.append((start, text + line))
        text = ""
    if text:
        out.append((start, text))
    return out


def words(text, more):
    """Shell words of `text`, joining following lines (from `more`) to close a quote."""
    while True:
        lex = shlex.shlex(text, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        try:
            return list(lex)
        except ValueError:
            if not more:
                return None
            text += "\n" + more.pop(0)[1]


BUILTIN = {}


def is_builtin(word):
    if word not in BUILTIN:
        # PATH is emptied so a `file` answer cannot be the machine's; only these two are accepted.
        proc = subprocess.run(
            [BASH, "-c", 'type -t -- "$1"', "_", word],
            env={"PATH": ""},
            capture_output=True,
            text=True,
        )
        BUILTIN[word] = proc.stdout.strip() in ("builtin", "keyword")
    return BUILTIN[word]


def resolve(word, doc, root, suffixes):
    """Repository files `word` can name: from the root, from the document, or by trailing segments."""
    word = word.removeprefix("./")
    found = [
        os.path.normpath(os.path.join(base, word))
        for base in (".", os.path.dirname(doc))
        if os.path.isfile(os.path.join(root, base, word))
    ]
    return found or suffixes.get(word, [])


def commands(root):
    docs, suffixes = walk(root)
    table, bad = manifest(root)
    if bad:
        return bad
    tools = table.get("tools", [])
    if not (isinstance(tools, list) and all(isinstance(t, str) for t in tools)):
        return [f"{MANIFEST}: `tools` must be a list of program names"]
    tools = set(tools)
    for doc in docs:
        for first, lines in fences(os.path.join(root, doc)):
            text = "\n".join(lines) + "\n"
            parse = subprocess.run(
                ["bash", "-n"], input=text, capture_output=True, text=True
            )
            if parse.returncode:
                reason = (
                    parse.stderr.strip().splitlines()[0]
                    if parse.stderr.strip()
                    else "syntax error"
                )
                bad.append(f"{doc}:{first}: `bash -n` rejects this block: {reason}")
                continue
            bad += judge(doc, logical_lines(lines, first), root, suffixes, tools)
    return bad


def judge(doc, units, root, suffixes, tools):
    bad, heredoc = [], None
    units = list(units)
    while units:
        number, text = units.pop(0)
        if heredoc:
            heredoc = None if text.strip() == heredoc else heredoc
            continue
        toks = words(text, units)
        if not toks:
            continue
        at_command, skip, interp = True, False, False
        for i, tok in enumerate(toks):
            if skip:
                skip = False
                continue
            if set(tok) <= set(";&|()<>"):
                if tok in BOUNDARIES:
                    at_command, interp = True, False
                else:
                    skip = True
                    if tok.startswith("<<") and tok != "<<<" and i + 1 < len(toks):
                        heredoc = toks[i + 1]
                continue
            if interp:
                if tok == "-c":
                    interp = False
                elif not tok.startswith("-"):
                    interp = False
                    if not UNRESOLVABLE.search(tok) and ("/" in tok or "." in tok):
                        if not resolve(tok, doc, root, suffixes):
                            bad.append(f"{doc}:{number}: `{tok}` does not exist")
                continue
            if not at_command:
                continue
            if ASSIGNMENT.match(tok) or tok.isdigit():
                continue
            at_command = tok in OPENERS
            if UNRESOLVABLE.search(tok) or is_builtin(tok):
                continue
            if "/" in tok or tok in suffixes:
                found = resolve(tok, doc, root, suffixes)
                if not found:
                    bad.append(f"{doc}:{number}: `{tok}` does not exist")
                elif not any(os.access(os.path.join(root, f), os.X_OK) for f in found):
                    bad.append(f"{doc}:{number}: `{tok}` is not executable")
            elif tok not in tools and tok not in INTERPRETERS:
                bad.append(
                    f"{doc}:{number}: `{tok}` is not a shell builtin, a file of this tree or a tool {MANIFEST} declares"
                )
            interp = tok in INTERPRETERS
    return bad


def emdash(root):
    docs, _ = walk(root)
    bad = []
    for rel in docs:
        with open(
            os.path.join(root, rel), encoding="utf-8", errors="replace"
        ) as handle:
            lines = handle.read().splitlines()
        if any(FORK_MARKER in line for line in lines[:FORK_LINES]):
            continue
        bad += [
            f"{rel}:{number}: carries an em dash; use a hyphen or reword (a file under {FORK_MARKER} is exempt)"
            for number, line in enumerate(lines, 1)
            if EM_DASH in line
        ]
    return bad


def prose(path):
    """(line number, line) of every line outside a fenced block."""
    out, mark = [], None
    with open(path, encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            m = FENCE.match(line)
            if mark is None:
                if m:
                    mark = m.group(1)
                else:
                    out.append((number, line.rstrip("\n")))
            elif (
                m
                and m.group(1)[0] == mark[0]
                and len(m.group(1)) >= len(mark)
                and not m.group(2)
            ):
                mark = None
    return out


def templates():
    """{kind: {version: [(heading, prefix match, optional)]}} from policy/docs."""
    out = {}
    for name in sorted(os.listdir(TEMPLATES)) if os.path.isdir(TEMPLATES) else []:
        m = TEMPLATE_FILE.match(name)
        if not m:
            continue
        heads = []
        for _, line in prose(os.path.join(TEMPLATES, name)):
            h = HEADING.match(line)
            if not h:
                continue
            text, optional = h.group(1), OPTIONAL in h.group(1)
            text = text.replace(OPTIONAL, "").strip()
            fill = PLACEHOLDER.search(text)
            # A heading with a placeholder fixes only the text before it, such as a tutorial's "1.".
            heads.append(
                (text[: fill.start()].strip() if fill else text, bool(fill), optional)
            )
        out.setdefault(m.group(1), {})[int(m.group(2))] = heads
    return out


def shape_of(rel, kind, version, heads):
    """Findings for one document held to `heads`, its template's `##` headings."""
    bad, have = [], []
    lines = prose(rel[1])
    for number, line in lines:
        if PLACEHOLDER.search(SPAN.sub("", line)):
            bad.append(f"{rel[0]}:{number}: a placeholder is left in prose; answer it")
        h = HEADING.match(line)
        if h:
            have.append(COMMENT.sub("", h.group(1)).strip())
    last, prev = -1, None
    for text, prefix, optional in heads:
        if optional:
            continue
        shown = f"## {text} ..." if prefix else f"## {text}"
        hits = [
            i
            for i, h in enumerate(have)
            if (
                h.startswith(text) if prefix else h == text or h.startswith(text + ": ")
            )
        ]
        later = [i for i in hits if i > last]
        if later:
            last, prev = later[0], shown
        elif hits:
            bad.append(
                f"{rel[0]}: `{shown}` must come after `{prev}`, as {kind} {version} orders them"
            )
        else:
            bad.append(
                f"{rel[0]}: missing the heading `{shown}` that {kind} {version} requires"
            )
    return bad


def snapshots(root):
    """(paths a [[contract]] of .github/changes.toml names as its snapshot, findings)."""
    path = os.path.join(root, CONTRACTS)
    if not os.path.exists(path):
        return set(), []
    try:
        with open(path, "rb") as handle:
            rows = tomllib.load(handle).get("contract", [])
    except (tomllib.TOMLDecodeError, OSError) as e:
        return set(), [f"{CONTRACTS}: cannot be read: {e}"]
    if not isinstance(rows, list):
        return set(), [
            f"{CONTRACTS}: `contract` must be an array of [[contract]] tables"
        ]
    return {
        os.path.normpath(r["snapshot"])
        for r in rows
        if isinstance(r, dict) and isinstance(r.get("snapshot"), str)
    }, []


def shape(root):
    """(findings, `outdated` notes): every document matches its kind's template."""
    docs, _ = walk(root)
    table, bad = manifest(root)
    if bad:
        return bad, []
    known = templates()
    bad += [f"{MANIFEST}: unknown key {k!r}" for k in sorted(set(table) - TOP_KEYS)]
    surfaces = table.get("surfaces", [])
    if not (isinstance(surfaces, list) and all(isinstance(s, str) for s in surfaces)):
        bad.append(f"{MANIFEST}: `surfaces` must be a list of names")
        surfaces = []
    bad += [
        f"{MANIFEST}: unknown surface {s!r}; one of {sorted(SURFACES)}"
        for s in surfaces
        if s not in SURFACES
    ]
    rows, found = entries(table, docs, root)
    bad += found

    # kind and version of each document: by place, then by the one [[doc]] row that classifies it.
    kinds = {}
    for rel in docs:
        kind = BY_PATH.get(rel) or next(
            (k for d, k in BY_DIR.items() if rel.startswith(d)), None
        )
        if kind:
            kinds[rel] = [kind, 1, None]
    for path, row, matched in rows:
        if "kind" not in row and "template" not in row:
            continue
        where = f"{MANIFEST}: {path}"
        kind, version = row.get("kind"), row.get("template", 1)
        if kind is not None and kind not in set(known) | ELSEWHERE:
            bad.append(
                f"{where}: unknown kind {kind!r}; one of {sorted(set(known) | ELSEWHERE)}"
            )
            continue
        if type(version) is not int or version < 1:
            bad.append(f"{where}: template must be a whole number from 1")
            continue
        for rel in matched:
            if rel in kinds and kinds[rel][2]:
                bad.append(
                    f"{rel}: classified by both {kinds[rel][2]} and {path} in {MANIFEST}"
                )
                continue
            have = kind or (kinds.get(rel) or [None])[0]
            if not have:
                bad.append(
                    f"{where}: names a template but no kind, and {rel} has none by place"
                )
                continue
            kinds[rel] = [have, version, path]

    # Rule 1's template check, then rules 3, 4 and 6 for each judged document.
    notes, refs = [], {rel for rel, k in kinds.items() if k[0] == "reference"}
    shots, found = snapshots(root) if refs else (set(), [])
    bad += found
    for rel, (kind, version, entry) in sorted(kinds.items()):
        if kind in ELSEWHERE:
            continue
        versions = known.get(kind, {})
        if version not in versions:
            where = f"{MANIFEST}: {entry}" if entry else rel
            bad.append(
                f"{where}: {kind} {version} has no template policy/docs/{kind}.{version}.md"
                + (f"; set template = {max(versions)}" if versions else "")
            )
            continue
        bad += shape_of(
            (rel, os.path.join(root, rel)), kind, version, versions[version]
        )
        if version < max(versions):
            notes.append(
                f"outdated: {rel} follows {kind} {version}, newest is {max(versions)}"
            )
        if kind == "reference" and rel not in shots:
            bad.append(
                f"{rel}: no [[contract]] in {CONTRACTS} names it as its snapshot"
            )

    # Rule 2: every document under docs/ has a kind.
    bad += [
        f"{rel}: unclassified; move it to the docs/ directory of its kind or give it a kind in {MANIFEST}"
        for rel in docs
        if rel.startswith("docs/") and rel != INDEX and rel not in kinds
    ]

    # Rule 5: every document the surfaces require exists.
    for surface in surfaces:
        for kind, name, where in SURFACES.get(surface, []):
            if not any(
                k[0] == kind and (name is None or os.path.basename(rel) == name)
                for rel, k in kinds.items()
            ):
                what = name or f"a {kind} document"
                bad.append(
                    f"{MANIFEST}: surface {surface!r} requires {what} ({where}) and there is none"
                )
    return bad, notes


CHECKS = {
    "owner": (owner, "Every instruction file names an owner and a reader."),
    "commands": (commands, "Every command in a shell fence parses and exists."),
    "budget": (budget, "The root AGENTS.md is within its byte budget."),
    "emdash": (emdash, "No Markdown file carries an em dash outside a forked file."),
    "shape": (shape, "Every document matches its kind's template."),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in CHECKS:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    check, ok = CHECKS[sys.argv[1]]
    root = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else ".")
    bad = check(root)
    if isinstance(bad, tuple):
        bad, notes = bad
        for note in notes:
            print(note)
    for finding in bad:
        print(finding)
    if bad:
        print(f"{len(bad)} {sys.argv[1]} finding(s).", file=sys.stderr)
        return 1
    print(f"{ok} OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
