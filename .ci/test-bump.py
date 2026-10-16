#!/usr/bin/env python3
"""Regression tests for bin/gates.py, the staged lock bump and its rollback.

    .ci/test-bump.py

Network-free: gates and four callers are bare repositories in a temporary
directory, `gh` is .ci/fixtures/gh-stub.py and the token minter echoes a fake
token. What judges each bump is real: gates fixture releases built from this
tree run their own run/lock.sh, .ci/gitleaks/sync.sh and the whole secret scan,
gitleaks included, exactly as shared-secret-scan.yml runs them.

gates main is R1 -> R2 -> R3 -> R4 -> R5, tagged v1..v5. R2 is a good release
that moves two synced copies; R3 adds a rule that also matches ordinary prose
(a bad release); R4's checks run failed; R5's sync.sh writes one more file. F is
a commit on R1 that only a fork has. Callers start locked at R1.
"""

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATES_PY = ROOT / "bin/gates.py"
STUB = ROOT / ".ci/fixtures/gh-stub.py"
OWNER, BOT = "o", {"login": "hw-test[bot]", "databaseId": 4242}
STAGES = {"canary": (2, ["c1", "c2"]), "fleet": (5, ["f1"]), "products": (0, ["p1"])}
PROSE = "release-note-2026\n"
BAD_RULE = """
[[rules]]
id = "overbroad-release"
description = "A release rule that also matches ordinary prose"
regex = '''release-note-[0-9]{4}'''
"""

failed = 0
workdir = Path(tempfile.mkdtemp())
atexit.register(shutil.rmtree, workdir, True)
ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "fixture",
    "GIT_AUTHOR_EMAIL": "fixture@invalid",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@invalid",
}


def check(label, ok, detail=""):
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        failed = 1
        for line in str(detail).splitlines()[-25:]:
            print(f"     | {line}")


def git(cwd, *args, env=None):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
        env={**ENV, **(env or {})},
    ).stdout.strip()


def dated(days_ago):
    stamp = f"@{int(__import__('time').time()) - int(days_ago * 86400)} +0000"
    return {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}


class Fixture:
    def __init__(self, callers=("c1", "c2", "f1", "p1"), commit_msg=True):
        self.root = Path(tempfile.mkdtemp(dir=workdir))
        self.state = self.root / "state"
        self.remotes = self.state / "remotes" / OWNER
        self.remotes.mkdir(parents=True)
        (self.state / "checks").mkdir()
        (self.state / "viewer.json").write_text(json.dumps(BOT))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "gh").write_text(f'#!/bin/sh\nexec python3 "{STUB}" "$@"\n')
        (self.bin / "mint").write_text('#!/bin/sh\necho "tok-$1"\n')
        for f in self.bin.iterdir():
            f.chmod(0o755)
        self.build_gates()
        for name in callers:
            self.caller(name, commit_msg=commit_msg)
        lines = [
            f'owner = "{OWNER}"',
            'gates = "gates"',
            f'git_url = "file://{self.state / "remotes"}"',
        ]
        lines.append(f'mint = ["{self.bin / "mint"}"]')
        for name, (hold, repos) in STAGES.items():
            lines += [
                "[[stage]]",
                f'name = "{name}"',
                f"hold_days = {hold}",
                f"repos = {json.dumps(repos)}",
            ]
        self.rollout = self.root / "rollout.toml"
        self.rollout.write_text("\n".join(lines) + "\n")
        self.env = {
            **ENV,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GH_STUB": str(self.state),
            "GATES_ROLLOUT": str(self.rollout),
        }

    def bare(self, name):
        path = self.remotes / f"{name}.git"
        git(self.root, "init", "-q", "--bare", "-b", "main", str(path))
        git(path, "config", "uploadpack.allowFilter", "true")
        git(path, "config", "uploadpack.allowAnySHA1InWant", "true")
        return path

    def build_gates(self):
        src = self.root / "gates-src"
        for rel in git(ROOT, "ls-files").splitlines():
            if (ROOT / rel).is_file():
                (src / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, src / rel)
        git(self.root, "init", "-q", "-b", "main", str(src))
        (src / "run/bump-identity").write_text(f"{BOT['login']} {BOT['databaseId']}\n")
        self.R = {}

        def release(n, change=None):
            if change:
                change()
            git(src, "add", "-A")
            git(src, "commit", "-qm", f"release {n}")
            git(src, "tag", f"v{n}")
            self.R[n] = git(src, "rev-parse", "HEAD")

        def append(rel, text):
            with open(src / rel, "a") as f:
                f.write(text)

        release(1)
        release(
            2,
            lambda: (
                append(".gitleaks.toml", "# release 2\n"),
                append(".githooks/pre-push", "# release 2\n"),
            ),
        )
        release(3, lambda: append(".gitleaks.toml", BAD_RULE))
        release(4, lambda: append("README.md", "release 4\n"))
        (self.state / "checks" / self.R[4]).write_text("failure")
        sync = src / ".ci/gitleaks/sync.sh"
        anchor = "    # core.hooksPath is repository config"
        release(
            5,
            lambda: sync.write_text(
                sync.read_text().replace(
                    anchor,
                    '    [ "$CHECK" = 1 ] || echo extra >"$target/extra.txt"\n'
                    + anchor,
                    1,
                )
            ),
        )
        git(src, "checkout", "-q", "-b", "fork", self.R[1])
        append("README.md", "fork only\n")
        git(src, "commit", "-qam", "fork only")
        self.F = git(src, "rev-parse", "HEAD")
        git(src, "checkout", "-q", "main")
        self.bare("gates")
        git(src, "push", "-q", "--tags", str(self.remotes / "gates.git"), "main")
        self.gates_src = src

    def copies(self, work, sha, commit_msg=True):
        pairs = [
            (".gitleaks.toml", ".gitleaks.toml"),
            (".githooks/pre-push", ".githooks/pre-push"),
        ]
        if commit_msg:
            pairs.append(
                (".githooks/commit-msg", "system-maintenance/git-hooks/commit-msg")
            )
        for dst, src in pairs:
            (work / dst).parent.mkdir(parents=True, exist_ok=True)
            (work / dst).write_text(git(self.gates_src, "show", f"{sha}:{src}") + "\n")
            if dst != ".gitleaks.toml":
                (work / dst).chmod(0o755)

    def caller(self, name, commit_msg=True):
        work = self.root / "work" / name
        git(self.root, "init", "-q", "-b", "main", str(work))
        (work / ".github/workflows").mkdir(parents=True)
        (work / ".github/workflows/ci.yml").write_text(
            f"jobs:\n  secret-scan:\n    uses: {OWNER}/gates/.github/workflows/shared-secret-scan.yml@{self.R[1]} # v1\n"
        )
        (work / "README.md").write_text(f"{name}, a caller\n")
        self.copies(work, self.R[1], commit_msg)
        git(work, "add", "-A")
        git(work, "commit", "-qm", "init", env=dated(20))
        (work / ".github/gates.lock").write_text(f"{self.R[1]}\n")
        git(work, "add", "-A")
        git(work, "commit", "-qm", "lock gates at R1", env=dated(15))
        self.bare(name)
        git(work, "push", "-q", str(self.remotes / f"{name}.git"), "main")

    def gates(self, *args):
        p = subprocess.run(
            [sys.executable, str(GATES_PY), *args],
            capture_output=True,
            text=True,
            env=self.env,
        )
        return p.returncode, p.stdout + p.stderr

    def calls(self):
        log = self.state / "calls.jsonl"
        return (
            [json.loads(line) for line in log.read_text().splitlines()]
            if log.exists()
            else []
        )

    def writes(self):
        return [
            c
            for c in self.calls()
            if c["method"] != "GET"
            or "createCommitOnBranch" in json.dumps(c["body"] or "")
        ]

    def pulls(self):
        f = self.state / "pulls.json"
        return json.loads(f.read_text()) if f.exists() else []

    def branches(self, name):
        return git(
            self.remotes / f"{name}.git",
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads/gates-lock/",
        ).split()

    def merge(self, name, days_ago, conclusion="success"):
        """Squash-merge the caller's open bump pull request, as GitHub would."""
        repo = self.remotes / f"{name}.git"
        pr = next(
            p
            for p in self.pulls()
            if p["repo"] == f"{OWNER}/{name}" and p["state"] == "open"
        )
        tree = git(repo, "rev-parse", f"{pr['head']}^{{tree}}")
        merged = git(
            repo,
            "commit-tree",
            tree,
            "-p",
            "main",
            "-m",
            pr["title"],
            env=dated(days_ago),
        )
        git(repo, "update-ref", "refs/heads/main", merged)
        git(repo, "update-ref", "-d", f"refs/heads/{pr['head']}")
        pr["state"] = "closed"
        (self.state / "pulls.json").write_text(
            json.dumps(
                self.pulls()[: pr["number"] - 1] + [pr] + self.pulls()[pr["number"] :]
            )
        )
        (self.state / "checks" / merged).write_text(conclusion)
        return merged

    def push(self, name, rel, text):
        work = self.root / "push" / name
        if not work.exists():
            git(
                self.root, "clone", "-q", f"file://{self.remotes / name}.git", str(work)
            )
        git(work, "pull", "-q", "--ff-only")
        (work / rel).write_text(text)
        git(work, "add", "-A")
        git(work, "commit", "-qm", f"add {rel}")
        git(work, "push", "-q", "origin", "main")
        return git(work, "rev-parse", "HEAD")

    def ci(self, name, ref, base=None, author=BOT):
        """shared-secret-scan.yml's steps on <ref>: lock.sh, then the scan."""
        d = Path(tempfile.mkdtemp(dir=self.root))
        git(d, "clone", "-q", f"file://{self.remotes / name}.git", "caller")
        git(d / "caller", "fetch", "-q", "origin", ref)
        git(d / "caller", "checkout", "-q", "--detach", ref)
        pin = re.search(
            r"@([0-9a-f]{40})", (d / "caller/.github/workflows/ci.yml").read_text()
        ).group(1)
        git(d, "clone", "-q", f"file://{self.remotes / 'gates'}.git", "gates")
        git(d / "gates", "checkout", "-q", "--detach", pin)
        env = {
            **self.env,
            "GATES_DIR": str(d / "gates"),
            "GATES_REPOSITORY": f"{OWNER}/gates",
            "BOOTSTRAP_SHA": pin,
            "GH_TOKEN": "ci",
        }
        env |= {
            "EVENT_NAME": "pull_request" if base else "push",
            "BASE_SHA": base or "",
        }
        env |= {"PR_AUTHOR": author["login"], "PR_AUTHOR_ID": str(author["databaseId"])}
        script = 'bash -e "$GATES_DIR/run/lock.sh" && bash -e "$GATES_DIR/run/secret-scan.sh"'
        p = subprocess.run(
            ["bash", "-c", script],
            cwd=d / "caller",
            capture_output=True,
            text=True,
            env=env,
        )
        return p.returncode, p.stdout + p.stderr


def refused(label, fix, args, finding):
    rc, out = fix.gates(*args)
    check(f"{label}: refused naming the finding", rc != 0 and finding in out, out)
    left = [b for name in STAGES["canary"][1] for b in fix.branches(name)]
    check(
        f"{label}: nothing was written to GitHub",
        not fix.writes() and not left,
        fix.writes(),
    )


# --- the staged bump -----------------------------------------------------------

fix = Fixture()
rc, out = fix.gates("bump", "v2")
opened = sorted(p["repo"] for p in fix.pulls())
check(
    "bump opens a pull request in each canary and nowhere else",
    rc == 0 and opened == ["o/c1", "o/c2"],
    out,
)
check(
    "bump says the later stages wait on the canaries",
    re.search(r"f1: waiting", out) and re.search(r"p1: waiting", out),
    out,
)

c1 = fix.remotes / "c1.git"
branch = f"gates-lock/{fix.R[2][:12]}"
tip = git(c1, "rev-parse", branch)
check(
    "the bump is one commit on the caller's main",
    git(c1, "rev-parse", f"{tip}^") == git(c1, "rev-parse", "main"),
)
check(
    "the bump commit is the builder's, made by GitHub",
    git(c1, "log", "-1", "--format=%an", tip) == BOT["login"],
)
names = git(c1, "diff", "--name-only", "main", tip).split()
check(
    "the bump moves the lock and the copies the release changed, nothing else",
    names == [".githooks/pre-push", ".github/gates.lock", ".gitleaks.toml"],
    names,
)
check(
    "the lock names the release",
    git(c1, "show", f"{tip}:.github/gates.lock") == fix.R[2],
)
mode = git(c1, "ls-tree", tip, ".githooks/pre-push").split()[0]
check("the hook keeps its executable mode", mode == "100755", mode)
pr = fix.pulls()[0]
check(
    "the pull request is opened from the bump branch to main",
    pr["head"] == branch and pr["base"] == "main",
    pr,
)
rc, verdict = fix.ci("c1", tip, base=git(c1, "rev-parse", "main"))
check(
    "the bump pull request's own CI passes every lock control and the scan",
    rc == 0 and "gates locked at" in verdict,
    verdict,
)

before = len(fix.writes())
rc, out = fix.gates("bump", "v2")
check(
    "a second run opens nothing new",
    rc == 0 and len(fix.writes()) == before and "already open" in out,
    out,
)

held = Fixture()
held.gates("bump", "v2")
held.merge("c1", 1)
held.merge("c2", 1)
rc, out = held.gates("bump", "v2")
check(
    "the fleet waits while the canaries' 2-day hold runs",
    "f1: waiting" in out and len(held.pulls()) == 2,
    out,
)

red = Fixture()
red.gates("bump", "v2")
red.merge("c1", 3, conclusion="failure")
red.merge("c2", 3)
rc, out = red.gates("bump", "v2")
check(
    "the fleet waits while a canary's merge is not green",
    "f1: waiting" in out and len(red.pulls()) == 2,
    out,
)

go = Fixture()
go.gates("bump", "v2")
go.merge("c1", 3)
go.merge("c2", 3)
rc, out = go.gates("bump", "v2")
opened = sorted(p["repo"] for p in go.pulls() if p["state"] == "open")
check(
    "the fleet opens once every canary merged green and held 2 days",
    rc == 0 and opened == ["o/f1"],
    out,
)
check("the products wait on the fleet", "p1: waiting" in out, out)

# --- refusals: each must be red before bin/gates.py writes anything --------------

fork = Fixture()
refused(
    "a release that is not on gates main",
    fork,
    ["bump", fork.F],
    "which is not on gates main",
)
refused(
    "a release with no green checks run",
    Fixture(),
    ["bump", "v4"],
    "which has no green checks run",
)
other = Fixture()
(other.state / "viewer.json").write_text(
    json.dumps({"login": "someone", "databaseId": 1})
)
refused(
    "a token that is not the bump identity",
    other,
    ["bump", "v2"],
    "its author someone (1) is not the bump identity",
)
refused(
    "a release whose sync writes more than the copies",
    Fixture(),
    ["bump", "v5"],
    "together with other content: extra.txt",
)
refused(
    "a hook the caller lacks, which GitHub would add 100644",
    Fixture(commit_msg=False),
    ["bump", "v2"],
    "cannot set a file mode",
)
refused(
    "a release name that is neither a tag nor a commit",
    Fixture(),
    ["bump", "v9"],
    "is not a gates tag or commit",
)

for label, switch, finding in [
    ("a commit GitHub did not sign", "unsigned", "is not verified"),
    (
        "a commit whose tree differs from the one checked",
        "drop-modes",
        "is not the commit that was checked",
    ),
]:
    fix = Fixture()
    (fix.state / switch).touch()
    rc, out = fix.gates("bump", "v2")
    left = fix.branches("c1") + fix.branches("c2")
    check(
        f"{label}: refused, its branch deleted, no pull request",
        rc != 0 and finding in out and not left and not fix.pulls(),
        out,
    )

# --- rollback: a bad release reaches main, and one commit restores the verdict ---

fix = Fixture()
fix.gates("bump", "v3")
fix.merge("c1", 0)
rc, verdict = fix.ci("c1", "main")
check("the bad release's own bump passes, so it merges", rc == 0, verdict)
head = fix.push("c1", "notes.txt", PROSE)
rc, verdict = fix.ci("c1", head)
check(
    "a later commit on main is red under the bad release",
    rc != 0 and "leaks found: 1" in verdict,
    verdict,
)

rc, out = fix.gates("rollback", "c1")
c1 = fix.remotes / "c1.git"
branch = f"gates-lock/{fix.R[1][:12]}"
check(
    "rollback opens one pull request",
    rc == 0 and fix.pulls()[-1]["head"] == branch,
    out,
)
tip = git(c1, "rev-parse", branch)
check(
    "the rollback is one commit on that same head",
    git(c1, "rev-parse", f"{tip}^") == head,
)
check(
    "the rollback moves only the lock and the copies",
    git(c1, "diff", "--name-only", head, tip).split()
    == [".githooks/pre-push", ".github/gates.lock", ".gitleaks.toml"],
)
check(
    "the rollback commit is the builder's, made by GitHub",
    git(c1, "log", "-1", "--format=%an", tip) == BOT["login"],
)
rc, verdict = fix.ci("c1", tip, base=head)
check(
    "one CI run on the rollback restores the previous verdict",
    rc == 0 and "rollback: .github/gates.lock returns to" in verdict,
    verdict,
)
rc, verdict = fix.ci("c1", tip, base=head, author={"login": "someone", "databaseId": 1})
check(
    "the same rollback by another author is refused",
    rc != 0 and "is not the bump identity" in verdict,
    verdict,
)

fix.merge("c1", 0)
rc, out = fix.gates("rollback", "c1")
check(
    "a second rollback, which would re-apply the bad release, is refused",
    rc != 0
    and "its last change was a rollback" in out
    and fix.pulls()[-1]["state"] == "closed",
    out,
)

fresh = Fixture(callers=("c1",))
git(
    fresh.remotes / "c1.git",
    "update-ref",
    "refs/heads/main",
    git(fresh.remotes / "c1.git", "rev-parse", "main^"),
)
rc, out = fresh.gates("rollback", "c1")
check(
    "rollback refuses a caller whose lock never changed",
    rc != 0 and "has never changed its lock" in out,
    out,
)

if failed:
    print("test-bump: FAILED", file=sys.stderr)
    sys.exit(1)
print(
    "test-bump: the rollout stages, every refusal writes nothing, and a rollback restores the verdict"
)
