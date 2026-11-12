#!/usr/bin/env python3
"""Refuse a change that relaxes a check, unless a recorded policy covers the relaxation.

Usage: weakening.py --base B [--head H] [--repo OWNER/NAME] [--tests GLOB]...

Compares the change against its merge base and names four kinds of finding:

  test-exemption       a skip, todo, ignore or `continue` added to a file --tests names
  context-removed      a required status context gone from a branch ruleset
  workflow-condition   `paths-ignore`, `continue-on-error` or a job `if:` other than
                       `always()` added where a required context runs
  moving-ref           a `uses:` added at a ref that is not a commit or digest

Each finding is matched against the [[relaxation]] records of
$GATES_DIR/policy/relaxations.toml, the gates checkout, never the tree under
review, so a change cannot grant itself a policy. The verdict names the policy
each relaxation matched. A moving ref that replaces a commit pin of the same
action is never covered. README.md, "The weakening guard", owns the design.

Exit status: 0 nothing unmatched, 1 refused, 2 could not decide.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tomllib
from collections import Counter

# The import would leave run/__pycache__ in the gates checkout, where the gate
# proof reads every file under run/ as a gate.
sys.dont_write_bytecode = True
from gate import glob_re  # noqa: E402

NO_BASE = "0" * 40
POLICY = "policy/relaxations.toml"
WORKFLOWS = [
    glob_re(g) for g in (".github/workflows/*.yml", ".github/workflows/*.yaml")
]
RULESETS = [glob_re(g) for g in (".github/ruleset*.json", ".github/rulesets/*.json")]

# `#[ignore]` is Rust's skip, so a `#` that opens an attribute is code.
COMMENT = re.compile(r"^\s*(?://|#(?!\[)|\*|/\*)")
EXEMPT = re.compile(
    r"\bcontinue\b|\.(?:skip|skipIf|todo)\b|[{,]\s*(?:skip|todo)\s*:"
    r"|\bpytest\.mark\.skip|\bunittest\.skip|\bskipTest\(|#\[ignore|\bt\.Skip(?:Now|f)?\("
)
# A regex or string literal: /.../flags, '...' or "...".
LITERAL = re.compile(
    r"/((?:\\.|[^/\\\n])+)/[a-z]*|'((?:\\.|[^'\\])*)'|\"((?:\\.|[^\"\\])*)\""
)
# What would combine or negate the recorded literal, so the line exempts more than it.
COMBINES = re.compile(r"\|\||&&|!|\b(?:or|and|not)\b")
USES = re.compile(r"^\s*(?:-\s+)?uses\s*:\s*['\"]?([^\s'\"#]+)")
PINNED = re.compile(r"@(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})$")
KEY = re.compile(r"^(\s*)(?:-\s+)?['\"]?([A-Za-z0-9_-]+)['\"]?\s*:\s*(.*)$")
ALWAYS = re.compile(r"^(?:\$\{\{\s*always\(\)\s*\}\}|always\(\))$")

KINDS = {"moving-ref": "ref", "test-exemption": "ref", "context-removed": "context_to"}
RECORD_KEYS = {"id", "ratified", "by", "kinds", "repos", "ref", "context_to"}
REPO = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._*-]+$")


class Undecided(Exception):
    pass


def git(*args):
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    if proc.returncode:
        err = proc.stderr.decode(errors="replace").strip()
        raise Undecided(f"git {' '.join(args)}: {err}")
    return proc.stdout.decode(errors="replace")


def files(rev, globs):
    names = git("ls-tree", "-r", "-z", "--name-only", rev).split("\0")
    return {
        p: git("show", f"{rev}:{p}") for p in names if any(g.match(p) for g in globs)
    }


def uncomment(line):
    if '"' not in line and "'" not in line:
        line = re.sub(r"(^|\s)#.*", "", line)
    return line.rstrip()


def load_policy():
    """The records in $GATES_DIR's policy file, each checked against the schema."""
    gates = os.environ.get("GATES_DIR")
    if not gates:
        raise Undecided("GATES_DIR is unset, so no recorded policy can be read")
    path = os.path.join(gates, POLICY)
    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Undecided(f"{POLICY}: {e}") from e
    if set(doc) - {"relaxation"}:
        raise Undecided(f"{POLICY}: unknown table {sorted(set(doc) - {'relaxation'})}")
    records, seen = doc.get("relaxation", []), set()
    for rec in records:
        where = f"{POLICY}: record {rec.get('id')!r}"
        if set(rec) - RECORD_KEYS:
            raise Undecided(f"{where}: unknown key {sorted(set(rec) - RECORD_KEYS)}")
        if not isinstance(rec.get("id"), str) or not rec["id"] or rec["id"] in seen:
            raise Undecided(f"{where}: `id` must be a unique non-empty string")
        seen.add(rec["id"])
        if not isinstance(rec.get("ratified"), datetime.date):
            raise Undecided(f"{where}: `ratified` must be a date")
        if not isinstance(rec.get("by"), str) or not rec["by"]:
            raise Undecided(f"{where}: `by` must name who decided")
        kinds, repos = rec.get("kinds"), rec.get("repos")
        if not kinds or not isinstance(kinds, list) or set(kinds) - set(KINDS):
            raise Undecided(f"{where}: `kinds` must list some of {sorted(KINDS)}")
        if (
            not repos
            or not isinstance(repos, list)
            or not all(isinstance(r, str) and REPO.match(r) for r in repos)
        ):
            raise Undecided(f"{where}: `repos` must list OWNER/NAME patterns")
        needed = {KINDS[k] for k in kinds}
        for key in {"ref", "context_to"}:
            if (key in rec) != (key in needed):
                state = "missing" if key in needed else "matches none of its kinds"
                raise Undecided(f"{where}: `{key}` {state}")
        try:
            if "ref" in rec:
                re.compile(rec["ref"])
            if "context_to" in rec:
                if "{from}" not in rec["context_to"]:
                    raise Undecided(f"{where}: `context_to` must hold {{from}}")
                re.compile(rec["context_to"].replace("{from}", "x"))
        except (re.error, TypeError) as e:
            raise Undecided(f"{where}: bad pattern: {e}") from e
    return records


def changed_lines(old, head, path):
    patch = git(
        "diff",
        "-U0",
        "--no-color",
        "--no-ext-diff",
        old,
        head,
        "--",
        f":(literal){path}",
    )
    added, removed, n = [], [], 0
    for line in patch.splitlines():
        if line.startswith("@@"):
            n = int(re.search(r"\+(\d+)", line).group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            added.append((n, line[1:]))
            n += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(line[1:])
    return added, removed


def exemptions(old, head, tests):
    out = []
    names = git(
        "diff", "--name-only", "--no-renames", "-z", "--diff-filter=AMT", old, head
    )
    for path in (p for p in names.split("\0") if p):
        if not any(g.match(path) for g in tests):
            continue
        added, removed = changed_lines(old, head, path)
        # A line moved within the file is not new, so only the surplus counts.
        gone = Counter(
            r.strip() for r in removed if EXEMPT.search(r) and not COMMENT.match(r)
        )
        for n, line in added:
            if COMMENT.match(line) or not EXEMPT.search(line):
                continue
            if gone[line.strip()]:
                gone[line.strip()] -= 1
            else:
                out.append(("test-exemption", f"{path}:{n}", line.strip()))
    return out


def contexts(rulesets):
    """Every required context named across the rulesets, or None for one that is not JSON."""
    found = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "required_status_checks" and isinstance(v, list):
                    found.update(
                        c["context"]
                        for c in v
                        if isinstance(c, dict) and "context" in c
                    )
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for path, text in rulesets.items():
        try:
            walk(json.loads(text))
        except json.JSONDecodeError:
            return None, path
    return found, None


def jobs(text):
    """{job id: (name, needs, [(line, label, value)])} and the workflow's paths-ignore blocks."""
    lines = [uncomment(x) for x in text.splitlines()]
    out, ignores, job, job_indent, key_indent, inside = {}, [], None, None, None, False
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        m = KEY.match(line)
        if indent == 0:
            inside, job = bool(m and m.group(2) == "jobs"), None
            continue
        if not inside:
            if m and m.group(2) == "paths-ignore":
                block = [line.strip()]
                for nested in lines[i + 1 :]:
                    if nested.strip() and len(nested) - len(nested.lstrip()) <= indent:
                        break
                    block.append(nested.strip())
                ignores.append((i + 1, " ".join(x for x in block if x)))
            continue
        if job_indent is None:
            job_indent = indent
        if indent == job_indent:
            job, key_indent = (m.group(2) if m else None), None
            if job:
                out[job] = [None, [], []]
            continue
        if job is None:
            continue
        if key_indent is None:
            key_indent = indent
        if not m:
            continue
        key, value = m.group(2), m.group(3).strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if indent == key_indent and key == "name":
            out[job][0] = value
        elif indent == key_indent and key == "needs":
            if value:
                out[job][1] += [v.strip(" '\"") for v in value.strip("[]").split(",")]
            else:
                for nested in lines[i + 1 :]:
                    item = re.match(r"^\s*-\s+['\"]?([A-Za-z0-9_-]+)", nested)
                    if not item:
                        break
                    out[job][1].append(item.group(1))
        elif indent == key_indent and key == "if" and not ALWAYS.match(value):
            out[job][2].append((i + 1, "if", value))
        elif key == "continue-on-error" and value != "false":
            out[job][2].append((i + 1, "continue-on-error", value))
    return out, ignores


def required(parsed, names):
    """The job ids a required context names, with every job they need."""

    def named(jid, name):
        return any(
            c in (jid, name)
            or c.startswith(f"{jid} / ")
            or (name and c.startswith(f"{name} / "))
            for c in names
        )

    todo = [jid for jid, (name, _, _) in parsed.items() if named(jid, name)]
    seen = set()
    while todo:
        jid = todo.pop()
        if jid in seen or jid not in parsed:
            continue
        seen.add(jid)
        todo += parsed[jid][1]
    return seen


def workflow_findings(base_wf, head_wf, names):
    out = []
    before = Counter()
    for path, text in base_wf.items():
        parsed, ignores = jobs(text)
        before.update((path, "paths-ignore", v) for _, v in ignores)
        before.update(
            (path, jid, label, v) for jid, j in parsed.items() for _, label, v in j[2]
        )
    for path, text in sorted(head_wf.items()):
        parsed, ignores = jobs(text)
        req = required(parsed, names)
        if not req:
            continue
        found = [
            (n, (path, "paths-ignore", v), f"paths-ignore: {v}") for n, v in ignores
        ]
        found += [
            (n, (path, jid, label, v), f"job {jid}: {label}: {v}")
            for jid in sorted(req)
            for n, label, v in parsed[jid][2]
        ]
        for n, key, subject in sorted(found):
            if before[key]:
                before[key] -= 1
            else:
                out.append(("workflow-condition", f"{path}:{n}", subject))
    return out


def moving_refs(base_wf, head_wf):
    def refs(wf):
        for path, text in sorted(wf.items()):
            for n, line in enumerate(text.splitlines(), 1):
                m = USES.match(uncomment(line))
                if m and not m.group(1).startswith("./"):
                    yield path, n, m.group(1)

    before = Counter(ref for _, _, ref in refs(base_wf) if not PINNED.search(ref))
    pinned = {
        ref.rsplit("@", 1)[0] for _, _, ref in refs(base_wf) if PINNED.search(ref)
    }
    out = []
    for path, n, ref in refs(head_wf):
        if PINNED.search(ref):
            continue
        if before[ref]:
            before[ref] -= 1
            continue
        repin = ref.rsplit("@", 1)[0] in pinned
        out.append(("moving-ref", f"{path}:{n}", ref, repin))
    return out


def findings(old, head, tests):
    out = exemptions(old, head, tests)
    base_rs, head_rs = files(old, RULESETS), files(head, RULESETS)
    c0, bad0 = contexts(base_rs)
    c1, bad1 = contexts(head_rs)
    if None in (c0, c1):
        out.append(
            ("context-removed", bad0 or bad1, "the ruleset cannot be read as JSON")
        )
        c0, c1 = c0 or set(), c1 or set()
    for gone in sorted(c0 - c1):
        out.append(
            (
                "context-removed",
                "rulesets",
                json.dumps({"from": gone, "to": sorted(c1 - c0)}),
            )
        )
    base_wf, head_wf = files(old, WORKFLOWS), files(head, WORKFLOWS)
    out += workflow_findings(base_wf, head_wf, c0 | c1)
    return [(*f, False) for f in out] + moving_refs(base_wf, head_wf)


def literals(line):
    for m in LITERAL.finditer(line):
        yield m, next(g for g in m.groups() if g is not None).replace("\\/", "/")


def covers(rec, kind, subject, repo):
    if kind not in rec["kinds"] or not any(
        glob_re(r).match(repo) for r in rec["repos"]
    ):
        return False
    if kind == "moving-ref":
        return re.fullmatch(rec["ref"], subject) is not None
    if kind == "test-exemption":
        # The line must test exactly the recorded literal, alone and as written.
        found = list(literals(subject))
        if len(found) != 1 or found[0][1] != rec["ref"]:
            return False
        m = found[0][0]
        return not COMBINES.search(subject[: m.start()] + subject[m.end() :])
    if kind == "context-removed" and subject.startswith("{"):
        moved = json.loads(subject)
        want = re.compile(rec["context_to"].replace("{from}", re.escape(moved["from"])))
        return any(want.fullmatch(c) for c in moved["to"])
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    ap.add_argument("--tests", action="append", default=[])
    args = ap.parse_args()
    try:
        if not args.repo:
            raise Undecided("no --repo and GITHUB_REPOSITORY is unset")
        records = load_policy()
        head = git("rev-parse", "--verify", f"{args.head}^{{commit}}").strip()
        if args.base == NO_BASE:
            old = git("hash-object", "-t", "tree", "--stdin").strip()
        else:
            old = git("merge-base", args.base, head).strip()
        found = findings(old, head, [glob_re(g) for g in args.tests])
    except Undecided as e:
        print(f"weakening: could not decide: {e}", file=sys.stderr)
        return 2
    unmatched = 0
    for kind, where, subject, repin in found:
        if repin:
            unmatched += 1
            print(
                f"weakening: {kind} at {where}: {subject} replaces a commit pin, which no recorded policy covers"
            )
            continue
        rec = next((r for r in records if covers(r, kind, subject, args.repo)), None)
        if rec:
            print(f"weakening: {kind} at {where} matches recorded policy {rec['id']}")
        else:
            unmatched += 1
            print(f"weakening: {kind} at {where}: {subject[:160]}")
    if unmatched:
        print(
            f"weakening: refused: {unmatched} weakening(s) match no recorded policy in {POLICY}"
        )
        return 1
    print(
        "weakening: every weakening matches a recorded policy"
        if found
        else "weakening: no weakening"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
