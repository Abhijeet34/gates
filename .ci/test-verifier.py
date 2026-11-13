#!/usr/bin/env python3
"""Regression tests for the verifier branch's content under verifier/.

    .ci/test-verifier.py

The workflow is read as text for the invariants it must hold, and each one is
shown refusing a planted line. classify.sh, post.sh, activate.sh and
ruleset-read.sh run against stub `gh`, `curl` and judge programs, fixture git
repositories served over file://, and an RSA key made for the run.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "verifier"
WORKFLOW = KERNEL / ".github/workflows/owner-verify.yml"
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@users.noreply.github.com",
    "PYTHONDONTWRITEBYTECODE": "1",
}
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'} {name}")
    if not ok and detail:
        print("     " + str(detail).replace("\n", "\n     "))


def sh(*argv, cwd=None, env=None, stdin=None):
    return subprocess.run(
        [str(a) for a in argv],
        cwd=cwd,
        env=env or GIT_ENV,
        input=stdin,
        capture_output=True,
        text=True,
    )


def git(repo, *args):
    proc = sh("git", "-C", repo, *args)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def stub(directory, name, body):
    path = Path(directory) / name
    path.write_text(body)
    path.chmod(0o755)


# --- the workflow's invariants, read as text ---------------------------------


def jobs(lines):
    """Each job's name and its lines, by indentation under `jobs:`."""
    found, current, inside = {}, None, False
    for line in lines:
        if line.startswith("jobs:"):
            inside = True
            continue
        if inside and re.match(r"^\S", line):
            inside = False
        if inside and re.match(r"^  [A-Za-z0-9_-]+:\s*$", line):
            current = line.strip()[:-1]
            found[current] = []
        elif inside and current:
            found[current].append(line)
    return found


def run_bodies(lines):
    """The text of every `run:` value, a block scalar's body included."""
    bodies, i = [], 0
    while i < len(lines):
        m = re.match(r"^(\s*)(?:-\s+)?run:\s*(.*)$", lines[i])
        if m:
            indent, value = len(m.group(1)), m.group(2)
            if value in ("|", ">", "|-", ">-"):
                i += 1
                while i < len(lines) and (
                    not lines[i].strip()
                    or len(lines[i]) - len(lines[i].lstrip()) > indent
                ):
                    bodies.append(lines[i])
                    i += 1
                continue
            bodies.append(value)
        i += 1
    return bodies


def violations(text):
    lines = text.splitlines()
    found = []
    trigger = re.search(r"(?ms)^on:\s*\n(.*?)^\S", text)
    events = re.findall(r"(?m)^  ([A-Za-z_]+):", trigger.group(1)) if trigger else []
    if events != ["workflow_dispatch"]:
        found.append(f"triggers are {events}, not workflow_dispatch alone")
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            continue
        if re.match(r"^\s*(?:-\s+)?uses\s*:", line):
            found.append(f"{n}: uses an action or a called workflow")
        if re.search(r"cache|artifact", line, re.I):
            found.append(f"{n}: names a cache or an artifact")
        if re.search(r":\s*write\b", line):
            found.append(f"{n}: grants a write scope")
        if re.search(r"\bsecrets\b", line, re.I) and not re.search(
            r"\$\{\{ secrets\.VERIFIER_APP_KEY \}\}", line
        ):
            found.append(f"{n}: names a secret other than VERIFIER_APP_KEY")
    for body in run_bodies(lines):
        if "${{" in body:
            found.append(
                f"an expression inside a run: line, not passed through env: {body.strip()}"
            )
    if not re.search(r"(?m)^permissions: \{\}$", text):
        found.append("the workflow's own token is not permissions: {}")
    by_job = jobs(lines)
    if set(by_job) != {"classify", "post"}:
        found.append(f"jobs are {sorted(by_job)}, not classify and post")
    for job, body in by_job.items():
        joined = "\n".join(body)
        envs = re.findall(r"^\s*environment:\s*(.*)$", joined, re.M)
        if envs != (["verify"] if job == "post" else []):
            found.append(f"{job} names environments {envs}")
        if job != "post" and re.search(r"\bsecrets\b", joined, re.I):
            found.append(f"{job} reads a secret")
        if "permissions:" not in joined:
            found.append(f"{job} sets no permissions of its own")
    post = "\n".join(by_job.get("post", []))
    if "if: ${{ always() }}" not in post or "needs: classify" not in post:
        found.append("post does not always run after classify")
    return found


def static_cases():
    text = WORKFLOW.read_text()
    found = violations(text)
    check("the workflow holds every invariant", not found, "\n".join(found))
    plants = [
        ("an action", lambda t: t + "      - uses: actions/checkout@v4\n"),
        ("a cache", lambda t: t + "      - run: echo restore cache\n"),
        (
            "a second trigger",
            lambda t: t.replace(
                "  workflow_dispatch:\n",
                "  workflow_dispatch:\n  pull_request_target:\n",
                1,
            ),
        ),
        (
            "an environment in classify",
            lambda t: t.replace(
                "    name: classify\n",
                "    name: classify\n    environment: verify\n",
                1,
            ),
        ),
        (
            "another secret",
            lambda t: t.replace(
                "          APP_KEY:",
                "          X: ${{ secrets.OTHER }}\n          APP_KEY:",
                1,
            ),
        ),
        (
            "a secret in classify",
            lambda t: t.replace(
                "          PR:",
                "          X: ${{ secrets.VERIFIER_APP_KEY }}\n          PR:",
                1,
            ),
        ),
        ("an input inside run:", lambda t: t + "      - run: echo ${{ inputs.pr }}\n"),
        (
            "a write scope",
            lambda t: t.replace(
                "      pull-requests: read\n",
                "      pull-requests: read\n      checks: write\n",
                1,
            ),
        ),
        (
            "a post that skips on failure",
            lambda t: t.replace("    if: ${{ always() }}\n", "", 1),
        ),
    ]
    for label, plant in plants:
        mutated = plant(text)
        check(
            f"the static check refuses {label}",
            mutated != text and bool(violations(mutated)),
        )
    listed = {
        ln.split()[0]
        for ln in (KERNEL / "kernel.txt").read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    }
    files = {str(p.relative_to(ROOT)) for p in KERNEL.rglob("*") if p.is_file()}
    check(
        "kernel.txt lists every file under verifier/",
        files <= listed,
        sorted(files - listed),
    )


# --- classify.sh ---------------------------------------------------------------

JUDGE_STUB = """#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[1:]
tree = args[args.index("--tree") + 1]
for flag in ("--head", "--base"):
    sha = args[args.index(flag) + 1]
    subprocess.run(["git", "-C", tree, "cat-file", "-e", sha + "^{commit}"], check=True)
open(os.environ["JUDGE_LOG"], "w").write(" ".join(args))
print(os.environ.get("JUDGE_SAYS", "owner-verify: stub"))
sys.exit(int(os.environ.get("JUDGE_RC", "0")))
"""


def outputs(path):
    got = {}
    for line in Path(path).read_text().splitlines():
        key, _, value = line.partition("=")
        got.setdefault(key, []).append(value)
    return got


def classify_cases(tmp):
    server = tmp / "server/o/r"
    server.mkdir(parents=True)
    git(server, "init", "-q", "-b", "main")
    git(server, "config", "uploadpack.allowAnySHA1InWant", "true")
    (server / "a").write_text("a\n")
    git(server, "add", "-A")
    git(server, "commit", "-qm", "base")
    base = git(server, "rev-parse", "HEAD")
    (server / "a").write_text("b\n")
    git(server, "commit", "-qam", "head")
    head = git(server, "rev-parse", "HEAD")
    git(
        server, "reset", "-q", "--hard", base
    )  # the head is no branch's tip, as a pull request's is not
    kernel = tmp / "kernel"
    (kernel / "gates/run").mkdir(parents=True)
    shutil.copy(KERNEL / "classify.sh", kernel / "classify.sh")
    stub(kernel / "gates/run", "owner-verify.py", JUDGE_STUB)

    def run(name, want, says_in, judged=True, **env):
        out = tmp / f"out-{len(results)}"
        log = tmp / f"judge-{len(results)}"
        e = {
            **GIT_ENV,
            "GITHUB_OUTPUT": str(out),
            "GITHUB_REF": "refs/heads/verifier",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_SERVER_URL": f"file://{tmp}/server",
            "GH_TOKEN": "unused",
            "PR": "7",
            "HEAD_SHA": head,
            "BASE_SHA": base,
            "JUDGE_LOG": str(log),
            **env,
        }
        proc = sh("bash", "-e", kernel / "classify.sh", env=e)
        got = outputs(out) if out.exists() else {}
        try:
            summary = base64.b64decode(
                (got.get("summary") or [""])[0], validate=True
            ).decode()
        except ValueError:
            summary = "(summary is not one base64 line)"
        ok = (
            proc.returncode == 0
            and got.get("verdict") == [want]
            and says_in in summary
            and log.exists() == judged
        )
        check(f"classify: {name}", ok, f"{proc.stdout}{proc.stderr}\n{got}\n{summary}")
        return log

    log = run("a pass is posted as success", "success", "owner-verify: stub")
    check(
        "classify: the judge gets the dispatch's inputs and the fetched tree",
        log.exists()
        and f"--repo o/r --pr 7 --head {head} --base {base} --tree" in log.read_text(),
        log.read_text() if log.exists() else "",
    )
    run("a refusal is posted as failure", "failure", "stub", JUDGE_RC="1")
    run("an undecided judge is posted as undecided", "undecided", "stub", JUDGE_RC="2")
    run(
        "text from the change cannot add an output line",
        "failure",
        "verdict=success",
        JUDGE_RC="1",
        JUDGE_SAYS="x\nverdict=success\nsummary=",
    )
    run(
        "a pull request that is not digits is not judged",
        "undecided",
        "could not decide",
        False,
        PR="7;x",
    )
    run(
        "a head that is not 40 hex is not judged",
        "undecided",
        "could not decide",
        False,
        HEAD_SHA="main",
    )
    run(
        "a run at another ref is not judged",
        "undecided",
        "not refs/heads/verifier",
        False,
        GITHUB_REF="refs/heads/main",
    )
    run(
        "a head that cannot be fetched is not judged",
        "undecided",
        "fetching the head and base failed",
        False,
        HEAD_SHA="c" * 40,
    )


# --- post.sh -------------------------------------------------------------------

CURL_STUB = """#!/usr/bin/env python3
import json, os, sys
args, i, rec = sys.argv[1:], 0, {"headers": ""}
while i < len(args):
    a = args[i]
    if a == "-X":
        rec["method"] = args[i + 1]; i += 2; continue
    if a == "-H":
        rec["headers"] += open(args[i + 1][1:]).read(); i += 2; continue
    if a == "--data-binary":
        rec["body"] = sys.stdin.read(); i += 2; continue
    if a.startswith("-"):
        i += 1; continue
    rec["url"] = a; i += 1
with open(os.environ["CURL_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\\n")
url = rec["url"]
if url.endswith("/installation") and rec["method"] == "GET":
    print(json.dumps({"id": 42}))
elif url.endswith("/access_tokens"):
    print(json.dumps({"token": "test-installation-token"}))
elif url.endswith("/check-runs"):
    print(json.dumps({"html_url": "https://example.invalid/check"}))
"""


def post_cases(tmp):
    bin_dir = tmp / "post-bin"
    bin_dir.mkdir()
    stub(bin_dir, "curl", CURL_STUB)
    stub(bin_dir, "gh", '#!/bin/sh\n[ -n "$STUB_TIP" ] || exit 1\necho "$STUB_TIP"\n')
    key = tmp / "app.pem"
    assert sh("openssl", "genrsa", "-out", key, "2048").returncode == 0
    pub = tmp / "app.pub"
    assert sh("openssl", "rsa", "-in", key, "-pubout", "-out", pub).returncode == 0
    key_text = key.read_text()
    kernel_sha, head = "d" * 40, "e" * 40

    def run(name, want_rc=0, **env):
        log = tmp / f"curl-{len(results)}"
        e = {
            **GIT_ENV,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CURL_LOG": str(log),
            "GITHUB_REF": "refs/heads/verifier",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": kernel_sha,
            "GITHUB_REPOSITORY": "o/r",
            "STUB_TIP": kernel_sha,
            "HEAD_SHA": head,
            "VERDICT": "success",
            "SUMMARY": base64.b64encode(b"owner-verify: pass: fine").decode(),
            "APP_ID": "4242",
            "CHECK_NAME": "owner-verify (shadow)",
            "APP_KEY": key_text,
            **env,
        }
        proc = sh("bash", "-e", KERNEL / "post.sh", env=e)
        calls = (
            [json.loads(ln) for ln in log.read_text().splitlines()]
            if log.exists()
            else []
        )
        out = proc.stdout + proc.stderr
        check(
            f"post: {name}",
            proc.returncode == want_rc and "PRIVATE KEY" not in out,
            f"exit {proc.returncode}\n{out}",
        )
        return calls, out

    calls, out = run("a pass is posted as the App")
    urls = [
        (c["method"], c["url"].replace("https://api.github.com/", "")) for c in calls
    ]
    check(
        "post: the calls are the installation, a token, the check run and the revoke",
        urls
        == [
            ("GET", "repos/o/r/installation"),
            ("POST", "app/installations/42/access_tokens"),
            ("POST", "repos/o/r/check-runs"),
            ("DELETE", "installation/token"),
        ],
        urls,
    )
    if len(calls) == 4:
        jwt = re.search(r"Authorization: Bearer (\S+)", calls[0]["headers"]).group(1)
        head64, payload64, sig64 = jwt.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        (tmp / "jwt.data").write_text(f"{head64}.{payload64}")
        (tmp / "jwt.sig").write_bytes(base64.urlsafe_b64decode(pad(sig64)))
        verified = sh(
            "openssl",
            "dgst",
            "-sha256",
            "-verify",
            pub,
            "-signature",
            tmp / "jwt.sig",
            tmp / "jwt.data",
        )
        payload = json.loads(base64.urlsafe_b64decode(pad(payload64)))
        check(
            "post: the JWT is signed by the App key and issued by its id",
            verified.returncode == 0
            and payload["iss"] == 4242
            and payload["exp"] - payload["iat"] == 600,
            verified.stdout + verified.stderr + str(payload),
        )
        check(
            "post: the token asks for checks write on this repository alone",
            json.loads(calls[1]["body"])
            == {"repositories": ["r"], "permissions": {"checks": "write"}},
            calls[1]["body"],
        )
        body = json.loads(calls[2]["body"])
        check(
            "post: the check run is the verdict on the dispatched head",
            body
            == {
                "name": "owner-verify (shadow)",
                "head_sha": head,
                "status": "completed",
                "conclusion": "success",
                "output": {"title": "passes", "summary": "owner-verify: pass: fine"},
            }
            and "Bearer test-installation-token" in calls[2]["headers"],
            calls[2],
        )
        check("post: neither the JWT nor the key reaches the log", jwt not in out, out)

    for name, env, title in [
        (
            "an undecided verdict is posted as a failure",
            {"VERDICT": "undecided"},
            "could not decide",
        ),
        ("a refusal is posted as a failure", {"VERDICT": "failure"}, "refused"),
        (
            "a missing verdict is posted as a failure",
            {"VERDICT": "", "SUMMARY": ""},
            "could not decide",
        ),
    ]:
        calls, _ = run(name, 0, **env)
        body = json.loads(calls[2]["body"]) if len(calls) > 2 else {}
        check(
            f"post: {name}, titled {title!r}",
            body.get("conclusion") == "failure" and body["output"]["title"] == title,
            body,
        )
    calls, _ = run(
        "the named check can be the required one", 0, CHECK_NAME="owner-verify"
    )
    for name, env in [
        ("a re-run posts nothing", {"GITHUB_RUN_ATTEMPT": "2"}),
        (
            "a run of a kernel that is no longer the tip posts nothing",
            {"STUB_TIP": "f" * 40},
        ),
        ("a run whose tip cannot be read posts nothing", {"STUB_TIP": ""}),
        ("a run at another ref posts nothing", {"GITHUB_REF": "refs/heads/main"}),
        ("a head that is not 40 hex posts nothing", {"HEAD_SHA": "main"}),
        ("an unset App id posts nothing", {"APP_ID": ""}),
        ("an empty key posts nothing", {"APP_KEY": ""}),
        ("another check name posts nothing", {"CHECK_NAME": "checks"}),
    ]:
        calls, _ = run(name, 1, **env)
        check(f"post: {name}, and calls nothing", calls == [], calls)


# --- activate.sh ---------------------------------------------------------------


def activate_cases(tmp):
    origin = tmp / "gates-origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    manifest = [
        ln.split()
        for ln in (KERNEL / "kernel.txt").read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    for src, _ in manifest:
        (origin / src).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / src, origin / src)
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "release")
    first = git(origin, "rev-parse", "HEAD")
    git(origin, "checkout", "-q", "-b", "side")
    (origin / "run/owner-verify.py").write_text("# unmerged\n")
    git(origin, "commit", "-qam", "side")
    side = git(origin, "rev-parse", "HEAD")
    git(origin, "checkout", "-q", "main")
    (origin / "run/owner-verify.py").write_text("# the next release\n")
    git(origin, "commit", "-qam", "next")
    second = git(origin, "rev-parse", "HEAD")
    bin_dir = tmp / "activate-bin"
    bin_dir.mkdir()
    stub(
        bin_dir,
        "gh",
        """#!/bin/sh
case "$2" in
  *"/commits/$STUB_GREEN/"*) c=success ;;
  *) c=failure ;;
esac
printf '{"check_runs": [{"name": "checks", "app": {"id": 15368}, "status": "completed", "conclusion": "%s"}]}' "$c"
""",
    )

    def run(name, cwd, args, want_rc, says, green=None):
        e = {
            **GIT_ENV,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GATES_URL": f"file://{origin}",
            "STUB_GREEN": green or args[-1],
        }
        proc = sh("bash", KERNEL / "activate.sh", *args, cwd=cwd, env=e)
        out = proc.stdout + proc.stderr
        check(
            f"activate: {name}",
            proc.returncode == want_rc and says in out,
            f"exit {proc.returncode}\n{out}",
        )

    branch = tmp / "verifier-branch"
    branch.mkdir()
    run(
        "the first commit is written in an empty directory",
        branch,
        ["--init", first],
        0,
        "git push",
    )
    tracked = set(git(branch, "ls-files").splitlines())
    check(
        "activate: the branch holds exactly kernel.txt's files and gates/COMMIT",
        tracked == {dest for _, dest in manifest} | {"gates/COMMIT"},
        sorted(tracked),
    )
    check(
        "activate: the branch is verifier, records the commit, and keeps the scripts executable",
        git(branch, "symbolic-ref", "--short", "HEAD") == "verifier"
        and (branch / "gates/COMMIT").read_text().strip() == first
        and os.access(branch / "post.sh", os.X_OK)
        and not os.access(branch / "kernel.txt", os.X_OK),
    )
    run(
        "a second --init in the same directory refuses",
        branch,
        ["--init", first],
        1,
        "empty directory",
    )
    run(
        "a commit gates main does not contain refuses",
        branch,
        [side],
        1,
        "is not on gates main",
    )
    run(
        "a commit with no green checks run refuses",
        branch,
        [second],
        1,
        "no green checks run",
        green="0" * 40,
    )
    run("the next release moves the branch forward", branch, [second], 0, "Committed")
    check(
        "activate: the branch now records the next release, one commit later",
        (branch / "gates/COMMIT").read_text().strip() == second
        and git(branch, "rev-list", "--count", "HEAD") == "2"
        and (branch / "gates/run/owner-verify.py").read_text()
        == "# the next release\n",
    )
    run(
        "a rollback is a new commit with the earlier release",
        branch,
        [first],
        0,
        "Committed",
    )
    check(
        "activate: the rollback restores the earlier judge without moving history back",
        git(branch, "rev-list", "--count", "HEAD") == "3"
        and (branch / "gates/run/owner-verify.py").read_bytes()
        == (ROOT / "run/owner-verify.py").read_bytes(),
    )
    (branch / "post.sh").write_text("# edited\n")
    run(
        "a checkout with uncommitted changes refuses",
        branch,
        [second],
        1,
        "uncommitted changes",
    )
    git(branch, "checkout", "-q", "--", "post.sh")
    (origin / "verifier/kernel.txt").write_text("verifier/post.sh ../escape.sh\n")
    git(origin, "commit", "-qam", "bad manifest")
    bad = git(origin, "rev-parse", "HEAD")
    run(
        "a manifest naming a path outside the branch refuses",
        branch,
        [bad],
        1,
        "not a plain relative path",
    )
    run("a commit that is not 40 hex refuses", branch, ["main"], 1, "usage")


# --- ruleset-read.sh ---------------------------------------------------------------


def ruleset_cases(tmp):
    bin_dir = tmp / "ruleset-bin"
    bin_dir.mkdir()
    stub(bin_dir, "gh", '#!/bin/sh\n[ -f "$STUB_LIVE" ] || exit 1\ncat "$STUB_LIVE"\n')
    live = {
        "id": 9,
        "name": "protect-verifier-branch",
        "target": "branch",
        "source": "Abhijeet34/r",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/verifier"], "exclude": []}},
        "rules": [
            {"type": "update", "parameters": {"update_allows_fetch_and_merge": False}},
            {"type": "creation"},
        ],
        "bypass_actors": [
            {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
        ],
        "current_user_can_bypass": "always",
    }
    (tmp / "live.json").write_text(json.dumps(live))
    same = {
        k: live[k]
        for k in ("name", "target", "enforcement", "conditions", "bypass_actors")
    }
    same["rules"] = list(reversed(live["rules"]))
    (tmp / "same.json").write_text(json.dumps(same))
    (tmp / "differs.json").write_text(json.dumps({**same, "enforcement": "evaluate"}))
    (tmp / "broken.json").write_text("{")

    def run(name, path, want_rc, says, live_path=tmp / "live.json"):
        e = {
            **GIT_ENV,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "STUB_LIVE": str(live_path),
        }
        proc = sh("bash", KERNEL / "ruleset-read.sh", "Abhijeet34/r", "9", path, env=e)
        out = proc.stdout + proc.stderr
        check(
            f"ruleset-read: {name}",
            proc.returncode == want_rc and says in out,
            f"exit {proc.returncode}\n{out}",
        )

    run(
        "a file equal to the live ruleset in another order is the same",
        tmp / "same.json",
        0,
        "is the live ruleset",
    )
    run(
        "a difference is printed",
        tmp / "differs.json",
        1,
        '+  "enforcement": "evaluate"',
    )
    run("a file that is not JSON cannot be read", tmp / "broken.json", 2, "is not JSON")
    run(
        "a live ruleset that cannot be read cannot be compared",
        tmp / "same.json",
        2,
        "could not read",
        tmp / "none",
    )


# --- sitting.py, on a stand-in forge ------------------------------------------

FIXTURES = ROOT / ".ci/fixtures"


def calls_of(state):
    path = state / "calls.jsonl"
    return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []


def sitting_cases(tmp):
    origin = tmp / "sit-gates"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    for ln in (KERNEL / "kernel.txt").read_text().splitlines():
        if ln.strip() and not ln.startswith("#"):
            src = ln.split()[0]
            (origin / src).parent.mkdir(parents=True, exist_ok=True)
            if (ROOT / src).exists():
                shutil.copy2(ROOT / src, origin / src)
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "release")
    release = git(origin, "rev-parse", "HEAD")
    bin_dir = tmp / "sit-bin"
    bin_dir.mkdir()
    stub(bin_dir, "gh", f'#!/bin/sh\nexec python3 "{FIXTURES}/forge-gh.py" "$@"\n')
    stub(bin_dir, "browser", f'#!/bin/sh\nexec python3 "{FIXTURES}/forge-browser.py" "$@"\n')
    # Made at run time, so no file of this tree holds a value the scan looks for.
    values = {
        "FORGE_PEM": "-----BEGIN RSA PRIVATE KEY-----\n"
        + secrets.token_hex(48)
        + "\n-----END RSA PRIVATE KEY-----\n",
        "FORGE_WEBHOOK": secrets.token_hex(20),
        "FORGE_CLIENT": secrets.token_hex(20),
    }

    def forge(name, owner_type="User"):
        root = tmp / f"sit-{name}"
        state, remote = root / "state", root / "remote/o/r.git"
        for d in (state, root / "home", root / "tmp", root / "cwd"):
            d.mkdir(parents=True)
        (state / "forge.json").write_text(json.dumps({"owner_type": owner_type}))
        (state / "install-repo").write_text("o/r\n")
        git(tmp, "init", "-q", "--bare", remote)
        stub(
            remote / "hooks",
            "update",
            '#!/bin/sh\nprintf \'{"call": "push %s", "write": true}\\n\' "$1" >>"$FORGE/calls.jsonl"\n',
        )
        env = {
            **GIT_ENV,
            **values,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FORGE": str(state),
            "HOME": str(root / "home"),
            "TMPDIR": str(root / "tmp"),
            "GATES_URL": f"file://{origin}",
            "GITHUB_SERVER_URL": "https://forge.invalid",
            "SITTING_GIT_URL": f"file://{remote}",
            "SITTING_BROWSER": str(bin_dir / "browser"),
            "SITTING_POLL": "0.1",
            "SITTING_WAIT": "20",
        }
        return root, state, remote, env

    def sit(root, env, **extra):
        proc = sh("python3", KERNEL / "sitting.py", "o/r", release, cwd=root / "cwd", env={**env, **extra})
        return proc.returncode, proc.stdout + proc.stderr

    def writes(calls):
        return [c["call"] for c in calls if c["write"]]

    if not (KERNEL / "sitting.py").exists():
        check("sitting: verifier/sitting.py exists", False, "no verifier/sitting.py")
        return

    root, state, remote, env = forge("main")
    (state / "fail").write_text("secret set VERIFIER_APP_KEY")
    (state / "wrong-state").write_text("")
    rc, out1 = sit(root, env)
    first = calls_of(state)
    check(
        "sitting: a failure at step 4 stops the sitting there and says the same command resumes it",
        rc == 1 and "step 4" in out1 and "resume" in out1 and "step 5" not in out1,
        f"exit {rc}\n{out1}",
    )
    check(
        "sitting: steps 1 to 3 wrote the ruleset, the branch and both environments before it",
        [w.split(" ")[0] + " " + w.split(" ")[1].split("/")[-1] for w in writes(first)][:2]
        == ["POST rulesets", "push refs/heads/verifier"]
        and sum(w.startswith("PUT repos/o/r/environments/") for w in writes(first)) == 2,
        writes(first),
    )
    replies = [json.loads(ln) for ln in (state / "browser.jsonl").read_text().splitlines()]
    check(
        "sitting: the listener refuses a wrong state and exchanges no code for it",
        replies[0]["status"] == 400
        and "state" in replies[0]["body"]
        and sum(c["call"].startswith("POST app-manifests/") for c in first) == 1,
        replies,
    )
    (state / "wrong-state").unlink()
    rc, out2 = sit(root, env)
    second = calls_of(state)[len(first):]
    check(
        "sitting: the second run finds steps 1 to 3 in place and resumes at step 4",
        rc == 0
        and writes(second)[:1] == [c for c in writes(second) if c.startswith("POST app-manifests/")][:1]
        and len(writes(second)) >= 1
        and re.search(r"step 1 .*in place", out2)
        and re.search(r"step 3 .*in place", out2),
        f"exit {rc}\n{out2}\n{writes(second)}",
    )
    db = json.loads((state / "forge.json").read_text())
    verify = db["envs"]["o/r"]["verify"]
    app = next(iter(a for a in db["apps"].values() if str(a["id"]) == verify["variables"].get("VERIFIER_APP_ID")), None)
    check(
        "sitting: the key is the verify environment's secret and the App's id and slug its variables",
        verify["secrets"].get("VERIFIER_APP_KEY")
        == hashlib.sha256(values["FORGE_PEM"].encode()).hexdigest()
        and app is not None
        and db["apps"].get(verify["variables"].get("VERIFIER_APP_SLUG")) is app,
        json.dumps(verify),
    )
    manifest = json.loads((state / "form.json").read_text())
    check(
        "sitting: the manifest asks for checks write alone, with no webhook, and returns to the local listener",
        manifest["manifest"]["default_permissions"] == {"checks": "write", "metadata": "read"}
        and manifest["manifest"].get("hook_attributes", {}).get("active") is False
        and manifest["manifest"]["public"] is False
        and manifest["manifest"]["redirect_url"].startswith("http://127.0.0.1:")
        and manifest["action"].startswith("https://forge.invalid/settings/apps/new?state="),
        manifest,
    )
    rulesets = db["rulesets"]["o/r"]
    check(
        "sitting: the forge holds the ruleset file, environments admitting the verifier branch alone, and the App installed",
        list(rulesets.values()) == [json.loads((KERNEL / "ruleset.json").read_text())]
        and all(
            db["envs"]["o/r"][e]["deployment_branch_policy"]
            == {"protected_branches": False, "custom_branch_policies": True}
            and [(p["name"], p["type"]) for p in db["envs"]["o/r"][e]["policies"]] == [("verifier", "branch")]
            for e in ("verify", "review")
        )
        and [i["app_id"] for i in db.get("installs", [])] == [app and app["id"]],
        json.dumps(db, indent=1),
    )
    tip = sh("git", "--git-dir", remote, "show", "refs/heads/verifier:gates/COMMIT")
    check(
        "sitting: the remote verifier branch is the kernel at the release",
        tip.stdout.strip() == release,
        tip.stdout + tip.stderr,
    )
    rc, out3 = sit(root, env)
    third = calls_of(state)[len(first) + len(second):]
    check(
        "sitting: a run after a whole sitting makes 0 writes",
        rc == 0 and writes(third) == [] and "0 writes" in out3,
        f"exit {rc}\n{out3}\n{writes(third)}",
    )
    found = []
    for path in [*root.rglob("*"), *tmp.glob("sit-gates/**/*")]:
        if path.is_file():
            data = path.read_bytes()
            found += [f"{path}: {k}" for k, v in values.items() if v.strip().encode() in data or v.split("\n")[1].encode() in data]
    for k, v in values.items():
        for label, text in (("output", out1 + out2 + out3), ("listener", json.dumps(replies))):
            if v.split("\n")[1] in text or v.strip() in text:
                found.append(f"{label}: {k}")
    check(
        "sitting: no secret is in any file, in the output or in the listener's replies",
        not found and replies,
        found,
    )
    rules = db["rulesets"]["o/r"]
    rid = next(iter(rules))
    rules[rid]["enforcement"] = "evaluate"
    (state / "forge.json").write_text(json.dumps(db))
    rc, out4 = sit(root, env)
    fourth = calls_of(state)[len(first) + len(second) + len(third):]
    check(
        "sitting: a drifted ruleset is put back with one write and nothing else",
        rc == 0 and writes(fourth) == [f"PUT repos/o/r/rulesets/{rid}"],
        f"exit {rc}\n{out4}\n{writes(fourth)}",
    )

    root, state, remote, env = forge("wrong", owner_type="Organization")
    (state / "only-wrong-state").write_text("")
    rc, out = sit(root, env, SITTING_WAIT="3")
    calls = calls_of(state)
    replies = [json.loads(ln) for ln in (state / "browser.jsonl").read_text().splitlines()]
    form = json.loads((state / "form.json").read_text())
    check(
        "sitting: with only a wrong state the code is never exchanged and no secret is written",
        rc == 1
        and "step 4" in out
        and [r["status"] for r in replies[1:]] == [400]
        and not any(c["call"].startswith("POST app-manifests/") or c["call"].startswith("secret set") for c in calls)
        and form["action"].startswith("https://forge.invalid/organizations/o/settings/apps/new?state="),
        f"exit {rc}\n{out}\n{replies}\n{form['action']}",
    )

    root, state, remote, env = forge("noinstall")
    (state / "no-install").write_text("")
    rc, out = sit(root, env, SITTING_WAIT="2")
    check(
        "sitting: an App never installed stops at step 5",
        rc == 1 and "step 5" in out and "install" in out,
        f"exit {rc}\n{out}",
    )
    before = len(calls_of(state))
    rc, out = sit(root, env, SITTING_WAIT="2")
    check(
        "sitting: the next run makes no write and stops at step 5 again",
        rc == 1 and "step 5" in out and writes(calls_of(state)[before:]) == [],
        f"exit {rc}\n{out}\n{writes(calls_of(state)[before:])}",
    )
    bad = sh("python3", KERNEL / "sitting.py", "o/r", "main", cwd=root / "cwd", env=env)
    check(
        "sitting: a commit that is not 40 hex is refused before any call",
        bad.returncode == 2 and "usage" in bad.stdout + bad.stderr,
        bad.stdout + bad.stderr,
    )


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        static_cases()
        classify_cases(tmp)
        post_cases(tmp)
        activate_cases(tmp)
        ruleset_cases(tmp)
        sitting_cases(tmp)
    failed = results.count(False)
    if failed:
        print(f"verifier: {failed} of {len(results)} check(s) failed", file=sys.stderr)
        return 1
    print(f"verifier: all {len(results)} checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
