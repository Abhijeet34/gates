#!/usr/bin/env python3
"""A stand-in forge's `gh`, for the sitting cases of .ci/test-verifier.py.

    FORGE=<state dir> forge-gh.py api|secret|variable ...

Serves the calls verifier/sitting.py and verifier/activate.sh make, from
<state>/forge.json. A secret is kept as its sha256 alone, so a scan of every
file after a run finds the value nowhere. Every call is appended to
<state>/calls.jsonl with `write` set for anything but a read; a call whose
"METHOD path" or "secret set NAME" contains <state>/fail's text fails once.
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

state = Path(os.environ["FORGE"])
db_path = state / "forge.json"
db = json.loads(db_path.read_text())


def save():
    # Replaced whole, so the browser and a poll never read half a file.
    tmp = db_path.with_suffix(f".{os.getpid()}")
    tmp.write_text(json.dumps(db, indent=1))
    tmp.replace(db_path)


def log(call, write):
    with open(state / "calls.jsonl", "a") as f:
        f.write(json.dumps({"call": call, "write": write}) + "\n")


def maybe_fail(call):
    fail = state / "fail"
    if fail.exists() and fail.read_text().strip() in call:
        fail.unlink()
        print(f"gh: injected failure at {call} (HTTP 502)", file=sys.stderr)
        sys.exit(1)


def respond(status, body, include):
    text = "" if body is None else json.dumps(body)
    if include:
        reason = {200: "OK", 201: "Created", 204: "No Content", 404: "Not Found"}
        print(f"HTTP/2.0 {status} {reason.get(status, 'Error')}")
        print("Content-Type: application/json; charset=utf-8\n")
    print(text)
    if status >= 400:
        print(f"gh: HTTP {status}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


def env_of(repo, name):
    return db.setdefault("envs", {}).setdefault(repo, {}).get(name)


def api(argv):
    method, include, body, path = "GET", False, None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-X":
            method = argv[i + 1]
            i += 2
        elif a == "-i":
            include = True
            i += 1
        elif a == "--paginate":
            i += 1
        elif a == "--input":
            body = json.loads(sys.stdin.read() or "null")
            i += 2
        elif a.startswith("-"):
            sys.exit(f"forge-gh: unknown flag {a}")
        else:
            path = a
            i += 1
    call = f"{method} {path}"
    write = method != "GET"
    log(call, write)
    maybe_fail(call)
    route = path.split("?")[0]
    if m := re.fullmatch(r"repos/[^/]+/gates/commits/([0-9a-f]{40})/check-runs", route):
        check = {"name": "checks", "app": {"id": 15368}, "status": "completed", "conclusion": "success"}
        respond(200, {"check_runs": [check]}, include)
    if m := re.fullmatch(r"users/([^/]+)", route):
        respond(200, {"login": m[1], "type": db.get("owner_type", "User")}, include)
    if m := re.fullmatch(r"repos/([^/]+/[^/]+)/rulesets(?:/(\d+))?", route):
        repo, rid = m[1], m[2]
        sets = db.setdefault("rulesets", {}).setdefault(repo, {})
        if method == "GET" and rid is None:
            respond(200, [{"id": int(k), "name": v["name"]} for k, v in sets.items()], include)
        if method == "GET" and rid in sets:
            respond(200, {"id": int(rid), "source": repo, **sets[rid]}, include)
        if method == "POST" and rid is None:
            rid = str(100 + len(sets))
            sets[rid] = body
            save()
            respond(201, {"id": int(rid), **body}, include)
        if method == "PUT" and rid in sets:
            sets[rid] = body
            save()
            respond(200, {"id": int(rid), **body}, include)
        respond(404, {"message": "Not Found"}, include)
    if m := re.fullmatch(r"repos/([^/]+/[^/]+)/environments/([^/]+)", route):
        repo, name = m[1], m[2]
        if method == "PUT":
            env = db["envs"].setdefault(repo, {}).setdefault(name, {"policies": [], "secrets": {}, "variables": {}})
            env["deployment_branch_policy"] = body["deployment_branch_policy"]
            save()
            respond(200, {"name": name, "deployment_branch_policy": env["deployment_branch_policy"]}, include)
        env = env_of(repo, name)
        if env is None:
            respond(404, {"message": "Not Found"}, include)
        respond(200, {"name": name, "deployment_branch_policy": env["deployment_branch_policy"]}, include)
    if m := re.fullmatch(r"repos/([^/]+/[^/]+)/environments/([^/]+)/deployment-branch-policies(?:/(\d+))?", route):
        env = env_of(m[1], m[2])
        if env is None:
            respond(404, {"message": "Not Found"}, include)
        policies = env["policies"]
        if method == "GET":
            respond(200, {"total_count": len(policies), "branch_policies": policies}, include)
        if method == "POST":
            policy = {"id": 500 + len(policies), **body}
            policies.append(policy)
            save()
            respond(200, policy, include)
        if method == "DELETE":
            env["policies"] = [p for p in policies if str(p["id"]) != m[3]]
            save()
            respond(204, None, include)
    if m := re.fullmatch(r"repos/([^/]+/[^/]+)/environments/([^/]+)/(secrets|variables)/([A-Z_]+)", route):
        env = env_of(m[1], m[2])
        kept = env and env[m[3]].get(m[4])
        if kept is None:
            respond(404, {"message": "Not Found"}, include)
        respond(200, {"name": m[4], **({"value": kept} if m[3] == "variables" else {})}, include)
    if m := re.fullmatch(r"app-manifests/([^/]+)/conversions", route):
        manifest = db.setdefault("codes", {}).pop(m[1], None)
        if manifest is None or method != "POST":
            respond(404, {"message": "Not Found"}, include)
        app_id = 900 + len(db.setdefault("apps", {}))
        slug = re.sub(r"[^a-z0-9]+", "-", manifest["name"].lower()).strip("-") + f"-{app_id}"
        db["apps"][slug] = {"id": app_id, "manifest": manifest}
        save()
        # The key, the webhook secret and the client secret exist only in this reply.
        respond(201, {
            "id": app_id, "slug": slug, "name": manifest["name"], "client_id": f"Iv1.{app_id}",
            "pem": os.environ["FORGE_PEM"], "webhook_secret": os.environ["FORGE_WEBHOOK"],
            "client_secret": os.environ["FORGE_CLIENT"],
        }, include)
    if route == "user/installations":
        respond(200, {"total_count": len(db.get("installs", [])), "installations": db.get("installs", [])}, include)
    if m := re.fullmatch(r"user/installations/(\d+)/repositories", route):
        found = [i for i in db.get("installs", []) if str(i["id"]) == m[1]]
        repos = [{"full_name": r} for r in (found[0]["repos"] if found else [])]
        respond(200 if found else 404, {"total_count": len(repos), "repositories": repos}, include)
    respond(404, {"message": f"forge-gh: no route for {call}"}, include)


def store(kind, argv):
    name, env, repo, value = argv[0], None, None, None
    i = 1
    while i < len(argv):
        flag, arg = argv[i], argv[i + 1]
        if flag == "--env":
            env = arg
        elif flag == "--repo":
            repo = arg
        elif flag == "--body":
            value = arg
        else:
            sys.exit(f"forge-gh: unknown flag {flag}")
        i += 2
    call = f"{kind} set {name}"
    log(call, True)
    if kind == "secret" and value is not None:
        sys.exit("forge-gh: a secret's value was in the argument list")
    maybe_fail(call)
    target = env_of(repo, env)
    if target is None:
        sys.exit(f"forge-gh: no environment {env} on {repo}")
    if kind == "secret":
        target["secrets"][name] = hashlib.sha256(sys.stdin.read().encode()).hexdigest()
    else:
        target["variables"][name] = value
    save()


cmd, rest = sys.argv[1], sys.argv[2:]
if cmd == "api":
    api(rest)
elif cmd in ("secret", "variable") and rest[:1] == ["set"]:
    store(cmd, rest[1:])
else:
    sys.exit(f"forge-gh: unknown command {sys.argv[1:]}")
