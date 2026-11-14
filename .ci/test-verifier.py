#!/usr/bin/env python3
"""Regression tests for the verifier branch's content under verifier/.

    .ci/test-verifier.py

The workflow is read as text for the invariants it must hold, and each one is
shown refusing a planted line. review.sh, classify.sh, post.sh, activate.sh and
ruleset-read.sh run against stub `gh`, `curl`, seats and judge programs, fixture
git repositories served over file://, and an RSA key made for the run.
"""

import base64
import json
import os
import re
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
# Each job's environment, and the secret it alone reads.
ENVIRONMENTS = {"review": "review", "classify": None, "post": "verify"}
SECRETS = {"review": ["CLAUDE_CODE_OAUTH_TOKEN"], "post": ["VERIFIER_APP_KEY"]}


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
            r"\$\{\{ secrets\.(VERIFIER_APP_KEY|CLAUDE_CODE_OAUTH_TOKEN) \}\}", line
        ):
            found.append(
                f"{n}: names a secret other than VERIFIER_APP_KEY and CLAUDE_CODE_OAUTH_TOKEN"
            )
    for body in run_bodies(lines):
        if "${{" in body:
            found.append(
                f"an expression inside a run: line, not passed through env: {body.strip()}"
            )
    if not re.search(r"(?m)^permissions: \{\}$", text):
        found.append("the workflow's own token is not permissions: {}")
    by_job = jobs(lines)
    if set(by_job) != set(ENVIRONMENTS):
        found.append(f"jobs are {sorted(by_job)}, not {sorted(ENVIRONMENTS)}")
    for job, body in by_job.items():
        joined = "\n".join(body)
        envs = re.findall(r"^\s*environment:\s*(.*)$", joined, re.M)
        want = ENVIRONMENTS.get(job)
        if envs != ([want] if want else []):
            found.append(f"{job} names environments {envs}")
        # Each environment's secret is read by the one job in it, and by no other.
        read = re.findall(r"\$\{\{ secrets\.([A-Za-z0-9_]+) \}\}", joined)
        if read != SECRETS.get(job, []):
            found.append(f"{job} reads the secrets {read}")
        if "permissions:" not in joined:
            found.append(f"{job} sets no permissions of its own")
    for job, after in (("classify", "review"), ("post", "classify")):
        body = "\n".join(by_job.get(job, []))
        if "if: ${{ always() }}" not in body or f"needs: {after}" not in body:
            found.append(f"{job} does not always run after {after}")
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
            "a classify that skips when the review fails",
            lambda t: t.replace("    if: ${{ always() }}\n", "", 1),
        ),
        (
            "a post that skips on failure",
            lambda t: t.replace(
                "    needs: classify\n    if: ${{ always() }}\n",
                "    needs: classify\n",
                1,
            ),
        ),
        (
            "the seats' login in post",
            lambda t: t.replace(
                "          APP_KEY:",
                "          X: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}\n          APP_KEY:",
                1,
            ),
        ),
        (
            "the App key in review",
            lambda t: t.replace(
                "          CLAUDE_CODE_OAUTH_TOKEN:",
                "          X: ${{ secrets.VERIFIER_APP_KEY }}\n          CLAUDE_CODE_OAUTH_TOKEN:",
                1,
            ),
        ),
        (
            "a review outside its environment",
            lambda t: t.replace("    environment: review\n", "", 1),
        ),
        (
            "a review that writes",
            lambda t: t.replace(
                "      pull-requests: read\n    outputs:\n      verdict: ${{ steps.seats",
                "      pull-requests: write\n    outputs:\n      verdict: ${{ steps.seats",
                1,
            ),
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
log = " ".join(args)
if "--review" in args:
    log += "\\nreview: " + open(args[args.index("--review") + 1]).read()
open(os.environ["JUDGE_LOG"], "w").write(log)
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
    run("a not-enrolled judge is posted as neutral", "neutral", "stub", JUDGE_RC="3")
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
    verdict = json.dumps({"head": head, "blocking": 0})
    log = run(
        "the review's verdict and summary reach the judge and the check",
        "success",
        "seats: approve: all 2 seats approve",
        REVIEW_VERDICT=base64.b64encode(verdict.encode()).decode(),
        REVIEW_SUMMARY=base64.b64encode(
            b"seats: approve: all 2 seats approve"
        ).decode(),
    )
    check(
        "classify: the judge is given the review's verdict as its --review file",
        log.exists()
        and "--review " in log.read_text()
        and f"review: {verdict}" in log.read_text(),
        log.read_text() if log.exists() else "",
    )
    log = run("no review verdict gives the judge none", "success", "owner-verify: stub")
    check(
        "classify: with no review verdict the judge gets no --review",
        log.exists() and "--review" not in log.read_text(),
        log.read_text() if log.exists() else "",
    )
    log = run(
        "a review verdict that is not base64 is not given to the judge",
        "success",
        "the review job's verdict is not base64",
        REVIEW_VERDICT="!!not base64!!",
    )
    check(
        "classify: a verdict that is not base64 reaches the judge as no --review",
        log.exists() and "--review" not in log.read_text(),
        log.read_text() if log.exists() else "",
    )


# --- review.sh -----------------------------------------------------------------

LOGIN = "fixture-login"  # noqa: S105 not a credential: the stub only echoes it back

SEATS_STUB = """#!/usr/bin/env python3
import json, os, shutil, sys
args = sys.argv[1:]
log = open(os.environ["SEATS_LOG"], "a")
if "--plan" in args:
    log.write(json.dumps({"plan": args}) + "\\n")
    print(os.environ.get("PLAN_SAYS", "seats: plan: 2 seats sit"))
    sys.exit(int(os.environ.get("PLAN_RC", "0")))
tree = args[args.index("--tree") + 1]
for flag in ("--head", "--base"):
    sha = args[args.index(flag) + 1]
    subprocess = __import__("subprocess")
    subprocess.run(["git", "-C", tree, "cat-file", "-e", sha + "^{commit}"], check=True)
rc = int(os.environ.get("SEATS_RC", "0"))
log.write(json.dumps({"args": args, "claude": shutil.which("claude"),
                      "token": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")}) + "\\n")
if rc in (0, 1):
    head = args[args.index("--head") + 1]
    open(args[args.index("--out") + 1], "w").write(json.dumps({"head": head, "blocking": rc}))
print(os.environ.get("SEATS_SAYS", "seats: stub"))
sys.exit(rc)
"""

FETCH_STUB = """#!/usr/bin/env python3
import os, shutil, sys
args = sys.argv[1:]
open(os.environ["CURL_LOG"], "a").write(" ".join(args) + "\\n")
if os.environ.get("CURL_RC", "0") != "0":
    print("curl: (22) The requested URL returned error: 404", file=sys.stderr)
    sys.exit(int(os.environ["CURL_RC"]))
shutil.copy(os.environ["CLI_TGZ"], args[args.index("-o") + 1])
"""


def review_cases(tmp):
    server = tmp / "review-server/o/r"
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
    git(server, "reset", "-q", "--hard", base)
    cli = tmp / "cli/package"
    cli.mkdir(parents=True)
    stub(cli, "claude", "#!/bin/sh\necho fake\n")
    tgz = tmp / "cli.tgz"
    assert sh("tar", "-czf", tgz, "-C", tmp / "cli", "package").returncode == 0
    digest = sh(
        "bash", "-c", f"openssl dgst -sha512 -binary {tgz} | base64 | tr -d '\\n'"
    ).stdout
    source = (KERNEL / "review.sh").read_text()
    pinned = re.search(r"(?m)^CLI_SHA512=(\S+)$", source)
    bin_dir = tmp / "review-bin"
    bin_dir.mkdir()
    stub(bin_dir, "curl", FETCH_STUB)

    def kernel(sha512):
        k = Path(tempfile.mkdtemp(dir=tmp))
        (k / "gates/run").mkdir(parents=True)
        (k / "review.sh").write_text(
            source.replace(pinned.group(0), f"CLI_SHA512={sha512}")
        )
        stub(k / "gates/run", "seats.py", SEATS_STUB)
        return k

    ours, real = kernel(digest), kernel(pinned.group(1))

    def run(name, want, says_in, k=ours, **env):
        n = len(results)
        out, seats_log, curl_log = (
            tmp / f"rout-{n}",
            tmp / f"seats-{n}",
            tmp / f"curl-{n}",
        )
        e = {
            **GIT_ENV,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GITHUB_OUTPUT": str(out),
            "GITHUB_REF": "refs/heads/verifier",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_SERVER_URL": f"file://{tmp}/review-server",
            "GH_TOKEN": "unused",
            "PR": "7",
            "HEAD_SHA": head,
            "BASE_SHA": base,
            "CLAUDE_CODE_OAUTH_TOKEN": LOGIN,
            "SEATS_LOG": str(seats_log),
            "CURL_LOG": str(curl_log),
            "CLI_TGZ": str(tgz),
            **env,
        }
        proc = sh("bash", "-e", k / "review.sh", env=e)
        got = outputs(out) if out.exists() else {}
        try:
            summary = base64.b64decode(
                (got.get("summary") or [""])[0], validate=True
            ).decode()
            verdict = base64.b64decode(
                (got.get("verdict") or ["?"])[0], validate=True
            ).decode()
        except ValueError:
            summary, verdict = "(not one base64 line)", "?"
        ok = (
            proc.returncode == 0
            and len(got.get("verdict", [])) == 1
            and (json.loads(verdict) if verdict else None) == want
            and says_in in summary
        )
        check(
            f"review: {name}",
            ok,
            f"{proc.stdout}{proc.stderr}\n{got}\n{verdict}\n{summary}",
        )
        sat = (
            [json.loads(x) for x in seats_log.read_text().splitlines()]
            if seats_log.exists()
            else []
        )
        return sat, curl_log.exists()

    sat, fetched = run(
        "an approval is handed on as blocking 0",
        {"head": head, "blocking": 0},
        "seats: stub",
    )
    check(
        "review: the plan runs first, then the seats with the pinned CLI first on PATH and the login",
        len(sat) == 2
        and "plan" in sat[0]
        and fetched
        and str(sat[1]["claude"]).endswith("/bin/claude")
        and sat[1]["token"] == LOGIN
        and f"--repo o/r --pr 7 --head {head} --base {base} --tree"
        in " ".join(sat[1]["args"]),
        sat,
    )
    run(
        "a hold is handed on as blocking",
        {"head": head, "blocking": 1},
        "seats: stub",
        SEATS_RC="1",
    )
    run("an undecided review hands on no verdict", None, "seats: stub", SEATS_RC="2")
    sat, fetched = run(
        "a head with nothing for the seats installs nothing and sits nobody",
        None,
        "no seat sits",
        PLAN_RC="3",
        PLAN_SAYS="seats: no seat sits: no path under the base's verified list changed",
    )
    check(
        "review: nothing for the seats fetches no CLI and sits nobody",
        len(sat) == 1 and not fetched,
        sat,
    )
    sat, _ = run(
        "a CLI tarball off its pinned sha512 is refused", None, "not the pinned", k=real
    )
    check("review: an unpinned CLI sits nobody", len(sat) == 1, sat)
    sat, _ = run(
        "a failed download sits nobody",
        None,
        "downloading the CLI failed",
        CURL_RC="22",
    )
    check("review: no CLI, no seat", len(sat) == 1, sat)
    sat, _ = run(
        "an empty login sits nobody",
        None,
        "CLAUDE_CODE_OAUTH_TOKEN is empty",
        CLAUDE_CODE_OAUTH_TOKEN="",
    )
    check("review: no login, no seat", len(sat) == 1, sat)
    run(
        "text from the change cannot add an output line",
        None,
        "verdict=e30=",
        SEATS_RC="2",
        SEATS_SAYS="x\nverdict=e30=\nsummary=",
    )
    for name, env, says in [
        (
            "a pull request that is not digits sits nobody",
            {"PR": "7;x"},
            "could not decide",
        ),
        (
            "a run at another ref sits nobody",
            {"GITHUB_REF": "refs/heads/main"},
            "not refs/heads/verifier",
        ),
        (
            "a head that cannot be fetched sits nobody",
            {"HEAD_SHA": "c" * 40},
            "fetching the head and base failed",
        ),
    ]:
        sat, fetched = run(name, None, says, **env)
        check(f"review: {name}, and fetches nothing", sat == [] and not fetched, sat)


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
        "a not-enrolled verdict is posted as neutral",
        0,
        VERDICT="neutral",
        SUMMARY=base64.b64encode(b"owner-verify: not enrolled: x").decode(),
    )
    body = json.loads(calls[2]["body"]) if len(calls) > 2 else {}
    check(
        "post: a not-enrolled verdict is neutral, titled 'not enrolled'",
        body.get("conclusion") == "neutral"
        and body["output"]["title"] == "not enrolled",
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


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        static_cases()
        review_cases(tmp)
        classify_cases(tmp)
        post_cases(tmp)
        activate_cases(tmp)
        ruleset_cases(tmp)
    failed = results.count(False)
    if failed:
        print(f"verifier: {failed} of {len(results)} check(s) failed", file=sys.stderr)
        return 1
    print(f"verifier: all {len(results)} checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
