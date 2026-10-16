#!/usr/bin/env python3
"""Move gates' callers from one release to the next, a stage at a time.

    bin/gates.py bump <release>    open each lock bump the rollout allows today
    bin/gates.py rollback <repo>   move <repo>'s lock back to the one it replaced

Each pull request is one commit that GitHub makes itself, through
createCommitOnBranch on the builder App's token, so it carries GitHub's verified
signature. Before anything is written the commit is built in a local clone and
judged by the run/lock.sh and verify.sh its own CI will run. README.md,
"Bumping", owns the design; bin/rollout.toml holds the stages.
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import datetime
from pathlib import Path

LOCK = ".github/gates.lock"
ROLLOUT = Path(
    os.environ.get("GATES_ROLLOUT") or Path(__file__).with_name("rollout.toml")
)
# Check-run conclusions that are no verdict against the commit.
PASSING = {"success", "neutral", "skipped"}
# Reads the token from the environment, so it is never in a process's arguments.
HELPER = '!f() { test "$1" = get && echo username=x-access-token && echo "password=$GH_TOKEN"; }; f'


class Refused(Exception):
    pass


def sh(cmd, cwd=None, env=None, stdin=None, text=True):
    p = subprocess.run(
        cmd, cwd=cwd, env=env, input=stdin, capture_output=True, text=text
    )
    if p.returncode:
        err = (
            p.stdout + p.stderr
            if text
            else (p.stdout + p.stderr).decode(errors="replace")
        )
        raise Refused(
            err.replace("::error::", "").strip() or f"{cmd[0]} exited {p.returncode}"
        )
    return p.stdout.strip() if text else p.stdout


def parse_lock(text):
    """The lock's commit, None for no lock; anything else is refused, as run/lock.sh does."""
    body = "".join(re.sub(r"#.*", "", text).split())
    if body and not re.fullmatch(r"[0-9a-f]{40}", body):
        raise Refused(f"{LOCK} is not a single 40-hex gates commit: {body[:120]}")
    return body or None


class Gates:
    """A full clone of gates: run/lock.sh decides ancestry on it, and checks it out."""

    def __init__(self, cfg, tmp):
        self.cfg, self.dir = cfg, Path(tmp) / "gates"
        self.repository = f"{cfg['owner']}/{cfg['gates']}"
        sh(
            [
                "git",
                "-c",
                "credential.helper=",
                "clone",
                "--quiet",
                f"{cfg['git_url']}/{self.repository}.git",
                str(self.dir),
            ]
        )

    def git(self, *args):
        return sh(["git", "-C", str(self.dir), *args])

    def resolve(self, ref):
        try:
            return self.git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
        except Refused:
            # A commit this clone lacks is run/lock.sh's to refuse, by name.
            if re.fullmatch(r"[0-9a-f]{40}", ref):
                return ref
            raise Refused(f"{ref} is not a gates tag or commit") from None

    def descends(self, old, new):
        return (
            subprocess.run(
                ["git", "-C", str(self.dir), "merge-base", "--is-ancestor", old, new],
                capture_output=True,
            ).returncode
            == 0
        )


class Caller:
    """One caller: the builder's token for it and a clone of its default branch."""

    def __init__(self, cfg, gates, name, tmp):
        self.cfg, self.gates, self.name = cfg, gates, name
        self.full = f"{cfg['owner']}/{name}"
        self.env = {**os.environ, "GH_TOKEN": sh([*cfg["mint"], name])}
        self.dir = Path(tmp) / "callers" / name
        self.git(
            "clone",
            "--quiet",
            "--filter=blob:none",
            f"{cfg['git_url']}/{self.full}.git",
            str(self.dir),
            cwd=tmp,
        )
        self.base = self.git("rev-parse", "HEAD")
        self.branch = self.git("symbolic-ref", "--short", "HEAD")
        self.lock = parse_lock(self.show(self.base, LOCK))
        self.pin = self.bootstrap()

    def git(self, *args, cwd=None):
        cfg = [
            "-c",
            "credential.helper=",
            "-c",
            f"credential.helper={HELPER}",
            "-c",
            "core.hooksPath=/dev/null",
        ]
        return sh(["git", *cfg, *args], cwd=cwd or self.dir, env=self.env)

    def show(self, rev, path):
        try:
            return self.git("show", f"{rev}:{path}")
        except Refused:
            return ""

    def api(self, path, method="GET", body=None):
        cmd = [
            "gh",
            "api",
            path,
            *(["-X", method] if method != "GET" else []),
            *(["--input", "-"] if body else []),
        ]
        out = sh(cmd, env=self.env, stdin=json.dumps(body) if body else None)
        return json.loads(out) if out else None

    def bootstrap(self):
        """The gates commit the caller's workflows pin, which its CI runs run/lock.sh from."""
        uses = rf"uses: *{re.escape(self.gates.repository)}/\.github/workflows/shared-[A-Za-z0-9_-]+\.ya?ml@([^ #]+)"
        try:
            refs = set(
                self.git(
                    "grep", "-h", "-o", "-E", uses, "--", ".github/workflows"
                ).splitlines()
            )
        except Refused:
            refs = set()
        pins = {self.gates.resolve(r.rpartition("@")[2]) for r in refs}
        if len(pins) != 1:
            raise Refused(
                f"{self.name} pins {len(pins)} gates commits in its workflows, so no one bootstrap judges its lock"
            )
        return pins.pop()

    def last_change(self):
        """The first-parent commit that last changed the lock, and when, or None."""
        out = self.git(
            "log", "--first-parent", "-1", "--format=%H %ct", self.base, "--", LOCK
        )
        return (out.split()[0], int(out.split()[1])) if out else None

    def green(self, sha):
        runs = self.api(
            f"repos/{self.full}/commits/{sha}/check-runs?filter=latest&per_page=100"
        )["check_runs"]
        return bool(runs) and all(
            r["status"] == "completed" and r["conclusion"] in PASSING for r in runs
        )

    def judge(self, identity):
        """run/lock.sh from the caller's bootstrap, as a pull request by <identity> would meet it."""
        try:
            script = self.gates.git("show", f"{self.pin}:run/lock.sh")
        except Refused:
            script = ""
        if not script:
            raise Refused(
                f"{self.name}'s bootstrap {self.pin[:12]} predates run/lock.sh, so its CI reads no lock"
            )
        lock_sh = self.dir.parent / f"{self.name}-lock.sh"
        lock_sh.write_text(script + "\n")
        env = {
            **self.env,
            "GATES_DIR": str(self.gates.dir),
            "GATES_REPOSITORY": self.gates.repository,
        }
        env |= {
            "BOOTSTRAP_SHA": self.pin,
            "EVENT_NAME": "pull_request",
            "BASE_SHA": self.base,
        }
        env |= {
            "PR_AUTHOR": identity["login"],
            "PR_AUTHOR_ID": str(identity["databaseId"]),
        }
        return sh(["bash", "-e", str(lock_sh)], cwd=self.dir, env=env)

    def propose(self, lock, headline, body):
        """One signed commit moving the lock to <lock> (None removes it) with its copies; opens the pull request."""
        target = lock or self.pin
        branch = f"gates-lock/{target[:12]}"
        if open_pr := self.api(
            f"repos/{self.full}/pulls?state=open&head={self.cfg['owner']}:{branch}"
        ):
            return f"already open {open_pr[0]['html_url']}"
        identity = self.api("graphql", body={"query": "{viewer{login databaseId}}"})[
            "data"
        ]["viewer"]
        commit = [
            "-c",
            "user.name=gates",
            "-c",
            "user.email=gates@invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
        ]

        # The lock alone first, so none of the release's own code runs here
        # until run/lock.sh has proved it is on gates main with green checks.
        path = self.dir / LOCK
        if lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(lock + "\n")
        else:
            path.unlink()
        self.git("add", "-A")
        self.git(*commit, "-m", headline)
        self.judge(identity)
        self.gates.git("checkout", "--quiet", "--detach", target)
        # The release's own scripts never see a token: they need none, and this
        # one can write to the caller.
        tokenless = {
            k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")
        }
        sync = self.gates.dir / ".ci/gitleaks/sync.sh"
        sh(["bash", str(sync), str(self.dir)], env=tokenless)
        self.git("add", "-A")
        self.git(*commit, "--amend", "--no-edit")
        verdict = self.judge(identity)
        verify = self.gates.dir / "run/secret-scan/verify.sh"
        sh(
            ["bash", "-e", str(verify)],
            cwd=self.dir,
            env={**tokenless, "GATES_DIR": str(self.gates.dir)},
        )

        changes = {"additions": [], "deletions": []}
        for line in self.git(
            "diff", "--raw", "--no-renames", self.base, "HEAD"
        ).splitlines():
            old, new, *_, status = line[1:].split("\t")[0].split()
            rel = line.split("\t")[1]
            if status == "D":
                changes["deletions"].append({"path": rel})
                continue
            if new != (old if status == "M" else "100644"):
                raise Refused(
                    f"{self.name}: {rel} must be {new}, and createCommitOnBranch cannot set a file mode, so GitHub would commit "
                    f"{'100644' if status == 'A' else old}. Push it once with .ci/gitleaks/sync.sh, then bump."
                )
            content = sh(
                ["git", "-C", str(self.dir), "show", f"HEAD:{rel}"],
                env=self.env,
                text=False,
            )
            changes["additions"].append(
                {"path": rel, "contents": base64.b64encode(content).decode()}
            )

        ref = f"repos/{self.full}/git/refs"
        self.delete(branch)
        self.api(ref, "POST", {"ref": f"refs/heads/{branch}", "sha": self.base})
        try:
            mutation = "mutation($i:CreateCommitOnBranchInput!){createCommitOnBranch(input:$i){commit{oid}}}"
            i = {
                "branch": {"repositoryNameWithOwner": self.full, "branchName": branch},
                "expectedHeadOid": self.base,
            }
            i |= {
                "message": {"headline": headline, "body": body},
                "fileChanges": changes,
            }
            oid = self.api("graphql", body={"query": mutation, "variables": {"i": i}})[
                "data"
            ]["createCommitOnBranch"]["commit"]["oid"]
            made = self.api(f"repos/{self.full}/commits/{oid}")
            if not made["commit"]["verification"]["verified"]:
                raise Refused(f"{self.name}: {oid} is not verified by GitHub")
            if (made["author"]["login"], made["author"]["id"]) != (
                identity["login"],
                identity["databaseId"],
            ):
                raise Refused(
                    f"{self.name}: {oid} is authored by {made['author']['login']}, not {identity['login']}"
                )
            self.git("fetch", "--quiet", "origin", f"refs/heads/{branch}")
            if self.git("rev-parse", "FETCH_HEAD^{tree}") != self.git(
                "rev-parse", "HEAD^{tree}"
            ):
                raise Refused(
                    f"{self.name}: {oid} is not the commit that was checked (its tree differs)"
                )
            pr = self.api(
                f"repos/{self.full}/pulls",
                "POST",
                {"title": headline, "head": branch, "base": self.branch, "body": body},
            )
        except Refused:
            self.delete(branch)
            raise
        notes = [line for line in verdict.splitlines() if line.startswith("rollback:")]
        return " ".join(["opened", pr["html_url"], *notes])

    def delete(self, branch):
        try:
            self.api(f"repos/{self.full}/git/refs/heads/{branch}", "DELETE")
        except Refused:
            pass


def say(stage, name, text):
    print(f"{stage['name']:<9} {name}: {text}", flush=True)


def bump(cfg, release):
    rc, now, opens = 0, time.time(), True
    with tempfile.TemporaryDirectory() as tmp:
        gates = Gates(cfg, tmp)
        target = gates.resolve(release)
        for stage in cfg["stage"]:
            merged = []
            for name in stage["repos"]:
                if not opens:
                    say(stage, name, "waiting on the previous stage")
                    continue
                try:
                    c = Caller(cfg, gates, name, tmp)
                    if c.lock and gates.descends(target, c.lock):
                        sha, when = c.last_change()
                        green = c.green(sha)
                        say(
                            stage,
                            name,
                            f"merged {datetime.fromtimestamp(when):%Y-%m-%d %H:%M}, checks {'green' if green else 'NOT green'}",
                        )
                        merged += [when] if green else [None]
                        continue
                    frm = c.lock or c.pin
                    compare = f"https://github.com/{gates.repository}/compare/{frm}...{target}"
                    body = f"Moves {LOCK} from {frm} to {target} ({release}), with the copies .ci/gitleaks/sync.sh writes.\n\n"
                    body += f"Changes: {compare}\nStage: {stage['name']}\nRoll back: bin/gates.py rollback {name}\n"
                    say(
                        stage,
                        name,
                        c.propose(
                            target,
                            f"chore(gates): lock gates at {target[:12]} ({release})",
                            body,
                        ),
                    )
                except Refused as e:
                    say(stage, name, f"REFUSED: {e}")
                    rc = 1
                merged.append(None)
            settled = (
                opens and len(merged) == len(stage["repos"]) and None not in merged
            )
            if settled and now < max(merged) + stage["hold_days"] * 86400:
                print(
                    f"{stage['name']:<9} holds until {datetime.fromtimestamp(max(merged) + stage['hold_days'] * 86400):%Y-%m-%d %H:%M}"
                )
                settled = False
            opens = settled
    return rc


def rollback(cfg, name):
    with tempfile.TemporaryDirectory() as tmp:
        c = Caller(cfg, Gates(cfg, tmp), name, tmp)
        last = c.last_change()
        if not last:
            raise Refused(
                f"{name} has never changed its lock, so there is nothing to roll back"
            )
        prev = parse_lock(c.show(f"{last[0]}^", LOCK))
        # After a rollback the lock it replaced is the bad release, so a second
        # rollback would re-apply it; a release that moves forward is a bump.
        if c.gates.descends(c.lock or c.pin, prev or c.pin):
            raise Refused(
                f"{name}'s lock replaced {prev or 'no lock'}, which is not older, so its last change was a rollback; bump forward instead"
            )
        body = f"Moves {LOCK} from {c.lock or 'no lock'} back to {prev or 'no lock'}, the lock that {last[0]} replaced, "
        body += "with the copies .ci/gitleaks/sync.sh writes at that commit.\n"
        print(
            f"{name}: {c.propose(prev, f'revert(gates): roll the lock back to {(prev or c.pin)[:12]}', body)}"
        )
    return 0


def main(argv):
    if len(argv) != 2 or argv[0] not in ("bump", "rollback"):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    cfg = tomllib.loads(ROLLOUT.read_text())
    try:
        return (bump if argv[0] == "bump" else rollback)(cfg, argv[1])
    except Refused as e:
        print(f"gates: REFUSED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
