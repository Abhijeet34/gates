#!/usr/bin/env python3
"""A stand-in for `gh api` over fixture bare repositories, for .ci/test-bump.py.

    GH_STUB=<state dir> gh-stub.py api [-X METHOD] <path> [--input -]

Serves the calls bin/gates.py makes, from <state>/remotes/<owner>/<repo>.git.
createCommitOnBranch behaves as measured on GitHub on 2026-10-16: an added file
is created 100644, a modified one keeps its mode, a workflow file is refused
without `workflows` permission, and the commit is authored by the token's bot.
Every call is appended to <state>/calls.jsonl; switches are files in <state>.
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

state = Path(os.environ["GH_STUB"])


def out(obj):
    print(json.dumps(obj))
    sys.exit(0)


def error(msg, code=1):
    print(f"gh: {msg}", file=sys.stderr)
    sys.exit(code)


def git(repo, *args, env=None, input=None):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input,
        capture_output=True,
        check=True,
        env={**os.environ, **(env or {})},
    ).stdout


def bare(owner, name):
    path = state / "remotes" / owner / f"{name}.git"
    if not path.is_dir():
        error("Not Found (HTTP 404)")
    return path


def viewer():
    return json.loads((state / "viewer.json").read_text())


def commit_on_branch(i):
    owner, name = i["branch"]["repositoryNameWithOwner"].split("/")
    repo, branch = bare(owner, name), i["branch"]["branchName"]
    changes = i.get("fileChanges", {})
    paths = [a["path"] for a in changes.get("additions", [])]
    paths += [d["path"] for d in changes.get("deletions", [])]
    if any(p.startswith(".github/workflows/") for p in paths):
        out(
            {
                "data": {"createCommitOnBranch": None},
                "errors": [
                    {
                        "type": "FORBIDDEN",
                        "message": "Resource not accessible by integration",
                    }
                ],
            }
        )
    head = git(repo, "rev-parse", f"refs/heads/{branch}").decode().strip()
    if head != i["expectedHeadOid"]:
        out(
            {
                "data": None,
                "errors": [
                    {"message": "Expected branch to point to " + i["expectedHeadOid"]}
                ],
            }
        )
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": f"{tmp}/index"}
        git(repo, "read-tree", head, env=env)
        for a in changes.get("additions", []):
            blob = (
                git(
                    repo,
                    "hash-object",
                    "-w",
                    "--stdin",
                    input=base64.b64decode(a["contents"]),
                )
                .decode()
                .strip()
            )
            ls = git(repo, "ls-tree", head, "--", a["path"]).decode().split()
            mode = ls[0] if ls else "100644"
            if (state / "drop-modes").exists():
                mode = "100644"
            git(
                repo,
                "update-index",
                "--add",
                "--cacheinfo",
                f"{mode},{blob},{a['path']}",
                env=env,
            )
        for d in changes.get("deletions", []):
            git(repo, "update-index", "--force-remove", d["path"], env=env)
        tree = git(repo, "write-tree", env=env).decode().strip()
    who = viewer()
    ident = {
        "GIT_AUTHOR_NAME": who["login"],
        "GIT_AUTHOR_EMAIL": f"{who['databaseId']}+{who['login']}@users.noreply.github.com",
    }
    ident |= {
        "GIT_COMMITTER_NAME": "GitHub",
        "GIT_COMMITTER_EMAIL": "noreply@github.com",
    }
    msg = i["message"]["headline"] + (
        "\n\n" + i["message"]["body"] if i["message"].get("body") else ""
    )
    oid = (
        git(repo, "commit-tree", tree, "-p", head, "-m", msg, env=ident)
        .decode()
        .strip()
    )
    git(repo, "update-ref", f"refs/heads/{branch}", oid, head)
    record = {
        "commit": {"verification": {"verified": not (state / "unsigned").exists()}},
        "author": {"login": who["login"], "id": who["databaseId"]},
    }
    (state / "commits").mkdir(exist_ok=True)
    (state / "commits" / oid).write_text(json.dumps(record))
    out(
        {
            "data": {
                "createCommitOnBranch": {
                    "commit": {
                        "oid": oid,
                        "url": f"https://github.test/{owner}/{name}/commit/{oid}",
                    }
                }
            }
        }
    )


def main(argv):
    if argv[:1] != ["api"]:
        error(f"unstubbed: {argv}")
    method, path, body, rest = "GET", None, None, argv[1:]
    while rest:
        arg = rest.pop(0)
        if arg == "-X":
            method = rest.pop(0)
        elif arg == "--input":
            rest.pop(0)
            body = json.load(sys.stdin)
        elif path is None:
            path = arg
    with open(state / "calls.jsonl", "a") as log:
        log.write(
            json.dumps(
                {
                    "method": method,
                    "path": path,
                    "token": os.environ.get("GH_TOKEN"),
                    "body": body,
                }
            )
            + "\n"
        )

    if path == "graphql":
        if "createCommitOnBranch" in body["query"]:
            commit_on_branch(body["variables"]["i"])
        if "viewer" in body["query"]:
            out({"data": {"viewer": viewer()}})
        error("unstubbed graphql")

    m = re.fullmatch(r"repos/([^/]+)/([^/?]+)/(.*)", path.split("?")[0])
    if not m:
        error(f"unstubbed path {path}")
    owner, name, sub = m.groups()
    repo = bare(owner, name)
    query = dict(kv.split("=", 1) for kv in path.partition("?")[2].split("&") if kv)
    pulls = state / "pulls.json"
    prs = json.loads(pulls.read_text()) if pulls.exists() else []

    if m2 := re.fullmatch(r"commits/([0-9a-f]{40})/check-runs", sub):
        f = state / "checks" / m2.group(1)
        conclusion = f.read_text().strip() if f.exists() else "success"
        out(
            {
                "total_count": 1,
                "check_runs": [
                    {
                        "name": "checks",
                        "status": "completed",
                        "conclusion": conclusion,
                        "app": {"id": 15368},
                    }
                ],
            }
        )
    if m2 := re.fullmatch(r"commits/([0-9a-f]{40})", sub):
        f = state / "commits" / m2.group(1)
        out(json.loads(f.read_text()) if f.exists() else error("Not Found (HTTP 404)"))
    if sub == "pulls" and method == "GET":
        out(
            [
                p
                for p in prs
                if p["repo"] == f"{owner}/{name}"
                and p["state"] == query.get("state")
                and f"{owner}:{p['head']}" == query.get("head")
            ]
        )
    if sub == "pulls" and method == "POST":
        n = len(prs) + 1
        pr = {
            "repo": f"{owner}/{name}",
            "state": "open",
            "number": n,
            "html_url": f"https://github.test/{owner}/{name}/pull/{n}",
        }
        prs.append(pr | {k: body[k] for k in ("head", "base", "title", "body")})
        pulls.write_text(json.dumps(prs))
        out(pr)
    if sub == "git/refs" and method == "POST":
        ref = body["ref"]
        if (
            subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "--verify", "-q", ref],
                capture_output=True,
            ).returncode
            == 0
        ):
            error("Reference already exists (HTTP 422)")
        git(repo, "update-ref", ref, body["sha"])
        out({"ref": ref, "object": {"sha": body["sha"]}})
    if sub.startswith("git/refs/heads/") and method == "DELETE":
        ref = "refs/" + sub.removeprefix("git/refs/")
        if subprocess.run(
            ["git", "-C", str(repo), "update-ref", "-d", ref], capture_output=True
        ).returncode:
            error("Reference does not exist (HTTP 422)")
        sys.exit(0)
    error(f"unstubbed {method} {path}")


main(sys.argv[1:])
