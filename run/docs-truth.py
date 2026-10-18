#!/usr/bin/env python3
"""Hold a repository's documents to three rules a tree alone can answer.

Usage: docs-truth.py owner|commands|budget [root]    (default root: the current directory)

owner     Every AGENTS.md and CLAUDE.md is listed in `.github/docs.toml` with an
          `owner` (any non-empty name or role) and a `reader` (agents, compliance
          or clients). Any other Markdown file is judged only when it is listed.
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

NAMES = {"AGENTS.md", "CLAUDE.md"}
SKIP_DIRS = {".git", "node_modules", "target", "vendor", ".venv"}
MANIFEST = ".github/docs.toml"
READERS = {"agents", "compliance", "clients"}
BUDGET = 8000
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


def owner(root):
    docs, _ = walk(root)
    table, bad = manifest(root)
    if bad:
        return bad
    listed = {}
    rows = table.get("doc", [])
    if not (isinstance(rows, list) and all(isinstance(r, dict) for r in rows)):
        return [f"{MANIFEST}: `doc` must be an array of [[doc]] tables"]
    for n, row in enumerate(rows, 1):
        where = f"{MANIFEST}: [[doc]] {n}"
        unknown = sorted(set(row) - {"path", "owner", "reader"})
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
        if path in listed:
            bad.append(f"{where}: listed twice")
        listed[path] = row
        if not os.path.isfile(os.path.join(root, path)):
            bad.append(f"{where}: does not exist")
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


CHECKS = {
    "owner": (owner, "Every instruction file names an owner and a reader."),
    "commands": (commands, "Every command in a shell fence parses and exists."),
    "budget": (budget, "The root AGENTS.md is within its byte budget."),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in CHECKS:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    check, ok = CHECKS[sys.argv[1]]
    root = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else ".")
    bad = check(root)
    for finding in bad:
        print(finding)
    if bad:
        print(f"{len(bad)} {sys.argv[1]} finding(s).", file=sys.stderr)
        return 1
    print(f"{ok} OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
