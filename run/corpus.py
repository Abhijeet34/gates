#!/usr/bin/env python3
"""A review lens measured on its seeded corpus: every planted defect caught, no clean case flagged.

Usage: corpus.py --corpus DIR --store DIR [--case NAME]... [--record DIR]

DIR is one lens's corpus; README.md, "The seeded corpus", owns its shape:

  corpus.toml             lens, catch, author, reviewers, optional budget
  base/                   the tree every case changes
  cases/NAME/case.toml    `defect` and a [[seed]] {path, lines = [first, last]} per
                          place the planted defect sits, or `clean` for a near miss
  cases/NAME/change/      the files the change writes over base/

Each case is built as a repository (base/ committed and tagged `base`, its
change committed over it, `.github/gates.toml` holding one review row of the
lens) and run through run/review.py --gate as a caller's row runs, recording
its verdict in the store. GATES_CORPUS_CASE=LENS/NAME is in review.py's
environment, so a recorded CLI can answer for the case.

With catch = "blocks" a seeded case is caught by a finding that blocks, verified,
at one of its seeds, and a clean case is flagged when its review fails. With
catch = "flags", for a lens whose findings are fixed rather than blocking, an
error or warning finding at a seed catches it and any error or warning finding
flags a clean case. A review that could not decide is `undecided`, never a catch
or a clean pass. --record DIR copies each seat's reply envelope and the CLI
version to DIR/LENS/NAME/, so a live run becomes the recording a replay answers from.

Exit status: 0 every seed caught and no clean case flagged, 1 a seed missed or a
clean case flagged, else 2 when a case was undecided or the corpus is malformed.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATCH = ("blocks", "flags")
CORPUS_KEYS = {"lens", "catch", "author", "reviewers", "budget"}
FLAGGED = ("error", "warning")
# One fixed date makes each case's commits, and so the diff header a seat reads, the same on every run.
DATE = "2026-10-19T00:00:00Z"
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "corpus",
    "GIT_AUTHOR_EMAIL": "corpus@invalid",
    "GIT_COMMITTER_NAME": "corpus",
    "GIT_COMMITTER_EMAIL": "corpus@invalid",
    "GIT_AUTHOR_DATE": DATE,
    "GIT_COMMITTER_DATE": DATE,
}
# review.py's own budgets bound it near 10 minutes; this only stops a hang.
CASE_SECONDS = 900


class Malformed(Exception):
    pass


def read_toml(path):
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Malformed(f"{path.name}: {e}") from e


def corpus_of(root):
    """The corpus settings and its cases, [(name, case)], checked against their shape."""
    conf = read_toml(root / "corpus.toml")
    problems = [f"unknown key {k!r}" for k in sorted(set(conf) - CORPUS_KEYS)]
    if not isinstance(conf.get("lens"), str) or not conf["lens"].strip():
        problems.append("lens must name the review's lens")
    if conf.get("catch") not in CATCH:
        problems.append(f"catch must be one of {', '.join(CATCH)}")
    if not isinstance(conf.get("author"), str) or not conf["author"].strip():
        problems.append("author must name the model the cases are written as")
    reviewers = conf.get("reviewers")
    if not (
        isinstance(reviewers, list)
        and reviewers
        and all(isinstance(m, str) for m in reviewers)
    ):
        problems.append("reviewers must be a non-empty list of model ids")
    budget = conf.get("budget", {})
    if not (isinstance(budget, dict) and all(type(v) is int for v in budget.values())):
        problems.append("budget must be a table of integers")
    if not (root / "base").is_dir():
        problems.append("no base/ directory")
    if problems:
        raise Malformed(f"corpus.toml: {'; '.join(problems)}")
    cases = []
    for d in sorted(
        p
        for p in (root / "cases").iterdir()
        if p.is_dir() and not p.name.startswith(".")
    ):
        case = read_toml(d / "case.toml")
        seeds = case.get("seed", [])
        good_seeds = isinstance(seeds, list) and all(
            isinstance(s, dict)
            and set(s) == {"path", "lines"}
            and isinstance(s["path"], str)
            and isinstance(s["lines"], list)
            and len(s["lines"]) == 2
            and all(type(n) is int for n in s["lines"])
            and 1 <= s["lines"][0] <= s["lines"][1]
            for s in seeds
        )
        seeded = isinstance(case.get("defect"), str) and seeds and good_seeds
        clean = (
            isinstance(case.get("clean"), str)
            and "seed" not in case
            and "defect" not in case
        )
        if set(case) - {"defect", "seed", "clean"} or not (seeded or clean):
            raise Malformed(
                f"cases/{d.name}/case.toml: a defect with [[seed]] {{path, lines = [first, last]}}"
                ", or clean and no seed"
            )
        if not (d / "change").is_dir():
            raise Malformed(f"cases/{d.name}: no change/ directory")
        cases.append((d.name, {**case, "dir": d}))
    if not cases:
        raise Malformed(f"{root.name}: no case under cases/")
    return conf, cases


def row_toml(conf):
    """The one review row each case repository registers."""
    lines = [
        "[[gate]]",
        f"id = {json.dumps(conf['lens'] + '-review')}",
        'kind = "review"',
        'inputs = ["**"]',
        f"lens = {json.dumps(conf['lens'])}",
        f"reviewers = {json.dumps(conf['reviewers'])}",
    ]
    if conf.get("budget"):
        pairs = ", ".join(f"{k} = {v}" for k, v in conf["budget"].items())
        lines.append(f"budget = {{ {pairs} }}")
    return "\n".join(lines) + "\n"


def git(repo, *args):
    done = subprocess.run(
        ["git", *args],
        cwd=repo,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
    )
    if done.returncode:
        raise Malformed(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def build(root, conf, case, repo):
    """The case's repository at `repo`; its head commit."""
    shutil.copytree(root / "base", repo)
    (repo / ".github").mkdir(exist_ok=True)
    (repo / ".github/gates.toml").write_text(row_toml(conf))
    git(repo, "init", "-q", "--template=", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "tag", "base")
    shutil.copytree(case["dir"] / "change", repo, dirs_exist_ok=True)
    # copytree keeps each file's mtime, so a same-size change looks unchanged to the index.
    git(repo, "rm", "-r", "-q", "--cached", ".")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "change")
    return git(repo, "rev-parse", "HEAD")


def at_seed(finding, seeds):
    return any(
        finding.get("path") == s["path"]
        and s["lines"][0] <= finding.get("line", 0) <= s["lines"][1]
        for s in seeds
    )


def shown(f):
    return f"{f['id']} {f['severity']} {f['path']}:{f['line']}: {' '.join(str(f['claim']).split())}"


def score(conf, case, rec):
    """(outcome, line): outcome is caught, missed, clean, flagged or undecided."""
    if rec["verdict"] == "error":
        return "undecided", f"undecided: {rec.get('detail', '')}"
    findings = rec.get("review", {}).get("findings", [])
    if conf["catch"] == "blocks":
        hits = [f for f in findings if f.get("blocks")]
    else:
        hits = [f for f in findings if f.get("severity") in FLAGGED]
    if "clean" in case:
        if hits:
            return "flagged", "flagged: " + "; ".join(map(shown, hits))
        return "clean", f"clean: {rec['detail']}"
    caught = [f for f in hits if at_seed(f, case["seed"])]
    if caught:
        return "caught", "caught: " + "; ".join(map(shown, caught))
    where = ", ".join(
        f"{s['path']}:{s['lines'][0]}-{s['lines'][1]}" for s in case["seed"]
    )
    want = (
        "verified blocking finding"
        if conf["catch"] == "blocks"
        else "error or warning finding"
    )
    others = "; ".join(map(shown, findings)) or "no finding"
    return "missed", f"missed: no {want} at {where} ({others})"


def latest(store, gid, head):
    path = store / "records.jsonl"
    found = None
    if path.exists():
        for raw in path.read_text().splitlines():
            rec = json.loads(raw)
            if rec.get("gate") == gid and rec.get("head") == head:
                found = rec
    return found


def keep(rec, store, out):
    """Each seat's reply envelope and the CLI version into `out`, as claude-recorded.py reads them."""
    review = rec.get("review", {})
    out.mkdir(parents=True, exist_ok=True)
    if review.get("cli"):
        (out / "version.txt").write_text(review["cli"] + "\n")
    for seat in review.get("seats", []):
        if not str(seat.get("reply", "")).startswith("sha256:"):
            raise Malformed(f"the {seat['seat']} seat kept no reply to record")
        data = (store / "logs" / f"{seat['reply'][7:]}.log").read_bytes()
        json.loads(data)  # stderr mixed into the log would not replay; refuse it here.
        (out / f"{seat['seat'].replace(' ', '-')}.json").write_bytes(data)


def run_case(root, conf, name, case, store, record):
    gid = f"{conf['lens']}-review"
    with tempfile.TemporaryDirectory(prefix="corpus-case-") as tmp:
        repo = Path(tmp, "repo")
        head = build(root, conf, case, repo)
        argv = [
            sys.executable, str(HERE / "review.py"), "--gate", gid, "--base", "base",
            "--author", conf["author"], "--store", str(store),
        ]  # fmt: skip
        env = {**os.environ, **GIT_ENV, "GATES_CORPUS_CASE": f"{conf['lens']}/{name}"}
        try:
            done = subprocess.run(
                argv,
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                timeout=CASE_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return "undecided", f"undecided: review.py ran past {CASE_SECONDS}s"
    rec = latest(store, gid, head)
    if rec is None:
        last = (done.stdout + done.stderr).strip().splitlines()[-1:] or ["no output"]
        return (
            "undecided",
            f"undecided: review.py recorded no verdict (exit {done.returncode}): {last[0]}",
        )
    if record:
        keep(rec, store, record / conf["lens"] / name)
    return score(conf, case, rec)


def main():
    ap = argparse.ArgumentParser(
        description="Run a lens's seeded corpus through its review row and count catches and false alarms."
    )
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--store", required=True, type=Path)
    ap.add_argument("--case", action="append", help="run only this case; repeatable")
    ap.add_argument("--record", type=Path, help="copy each seat's reply here, per case")
    args = ap.parse_args()
    store = args.store.resolve()
    try:
        conf, cases = corpus_of(args.corpus.resolve())
        if args.case:
            unknown = sorted(set(args.case) - {n for n, _ in cases})
            if unknown:
                raise Malformed(f"no case {', '.join(unknown)}")
            cases = [(n, c) for n, c in cases if n in args.case]
        outcomes = {}
        for name, case in cases:
            outcome, line = run_case(
                args.corpus.resolve(), conf, name, case, store,
                args.record and args.record.resolve(),
            )  # fmt: skip
            outcomes[name] = outcome
            print(f"corpus: {conf['lens']}/{name}: {line}", flush=True)
    except Malformed as e:
        print(f"corpus: undecided: {e}")
        return 2
    seeded = sum("clean" not in c for _, c in cases)
    count = {
        o: sum(v == o for v in outcomes.values())
        for o in ("caught", "missed", "flagged", "undecided")
    }
    print(
        f"corpus: {conf['lens']}: caught {count['caught']} of {seeded} seeded,"
        f" {count['flagged']} false alarm{'s' * (count['flagged'] != 1)} on {len(cases) - seeded} clean,"
        f" {count['undecided']} undecided"
    )
    if count["missed"] or count["flagged"]:
        return 1
    return 2 if count["undecided"] else 0


if __name__ == "__main__":
    sys.exit(main())
