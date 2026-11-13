#!/usr/bin/env python3
"""The maintainer's one-command sitting: set a repository up to run the verifier.

    sitting.py OWNER/NAME <gates commit>

Five steps in the order invariant 8 sets: 1 the ruleset that keeps
refs/heads/verifier to admins (ruleset.json), 2 the branch at the kernel of
<gates commit>, written by activate.sh, 3 the `verify` and `review`
environments admitting that branch alone, 4 the verifier App made through the
manifest flow, its key stored as `verify`'s secret and its id and slug as its
variables, 5 the App installed on the repository. Each step reads the forge
first and writes only what differs, so a second run writes nothing and a run
after a failure resumes at the step that failed; no state is kept on disk.

The browser is needed twice, to confirm the App and to install it. The local
listener on 127.0.0.1 refuses a callback whose `state` is not this run's,
exchanges the code once, and replies with no secret; the key goes from the
conversion's reply to `gh secret set` on stdin and is never in a file or an
argument list. README.md, "The verifier branch", owns the design.
"""

import hmac
import html
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
WAIT = float(os.environ.get("SITTING_WAIT", "900"))
POLL = float(os.environ.get("SITTING_POLL", "5"))
ENVIRONMENTS = ("verify", "review")
POLICY = {"protected_branches": False, "custom_branch_policies": True}
writes = 0


class Refused(Exception):
    pass


def say(text):
    print(f"sitting: {text}", flush=True)


def gh(argv, stdin=None, write=False):
    global writes
    writes += write
    return subprocess.run(["gh", *argv], input=stdin, capture_output=True, text=True)


def api(method, path, body=None, missing_ok=False):
    argv = ["api", "-i", "-X", method, path]
    if body is not None:
        argv += ["--input", "-"]
    proc = gh(argv, None if body is None else json.dumps(body), write=method != "GET")
    head, _, rest = proc.stdout.replace("\r\n", "\n").partition("\n\n")
    m = re.match(r"HTTP/\S+ (\d{3})", head)
    status = int(m[1]) if m else 0
    if status == 404 and missing_ok:
        return None
    if not 200 <= status < 300:
        raise Refused(f"{method} {path} answered {status or 'nothing'}: {proc.stderr.strip()[:500]}")
    return json.loads(rest) if rest.strip() else {}


def run(argv, cwd=None):
    proc = subprocess.run([str(a) for a in argv], cwd=cwd, capture_output=True, text=True)
    if proc.returncode:
        raise Refused(f"{' '.join(str(a) for a in argv[:3])} failed: {(proc.stderr or proc.stdout).strip()[-1500:]}")
    return proc.stdout.strip()


def open_url(url):
    say(f"  opening {url} (open it yourself if no browser appears)")
    browser = os.environ.get("SITTING_BROWSER")
    if browser:
        subprocess.Popen([browser, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def shape(ruleset):
    """What an apply sets, in a fixed order, as ruleset-read.sh compares it."""
    return {
        **{k: ruleset.get(k) for k in ("name", "target", "enforcement", "conditions")},
        "bypass_actors": sorted(
            ({k: a.get(k) for k in ("actor_id", "actor_type", "bypass_mode")} for a in ruleset.get("bypass_actors") or []),
            key=lambda a: (a["actor_type"], a["actor_id"]),
        ),
        "rules": sorted(
            ({"type": r["type"], "parameters": r.get("parameters")} for r in ruleset.get("rules") or []),
            key=lambda r: r["type"],
        ),
    }


def step_ruleset(repo, _commit):
    want = json.loads((HERE / "ruleset.json").read_text())
    found = [r for r in api("GET", f"repos/{repo}/rulesets?includes_parents=false&per_page=100") if r["name"] == want["name"]]
    if not found:
        api("POST", f"repos/{repo}/rulesets", want)
        return True
    if shape(api("GET", f"repos/{repo}/rulesets/{found[0]['id']}")) == shape(want):
        return False
    api("PUT", f"repos/{repo}/rulesets/{found[0]['id']}", want)
    return True


def step_branch(repo, commit):
    global writes
    url = os.environ.get("SITTING_GIT_URL") or f"{SERVER}/{repo}.git"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "verifier"
        if run(["git", "ls-remote", url, "refs/heads/verifier"]):
            run(["git", "clone", "-q", "--no-tags", "-b", "verifier", url, work])
            recorded = work / "gates/COMMIT"
            if recorded.exists() and recorded.read_text().strip() == commit:
                return False
            # A later kernel is written by the branch's own activate.sh (invariant 9).
            run(["bash", work / "activate.sh", commit], cwd=work)
        else:
            work.mkdir()
            run(["bash", HERE / "activate.sh", "--init", commit], cwd=work)
        writes += 1
        run(["git", "-C", work, "push", "-q", url, "verifier:refs/heads/verifier"])
    return True


def step_environments(repo, _commit):
    changed = False
    for name in ENVIRONMENTS:
        base = f"repos/{repo}/environments/{name}"
        env = api("GET", base, missing_ok=True)
        if env is None or env.get("deployment_branch_policy") != POLICY:
            api("PUT", base, {"deployment_branch_policy": POLICY})
            changed = True
        listed = api("GET", f"{base}/deployment-branch-policies?per_page=100")["branch_policies"]
        keep = [p for p in listed if p.get("name") == "verifier" and p.get("type", "branch") == "branch"][:1]
        for policy in listed:
            if policy not in keep:
                api("DELETE", f"{base}/deployment-branch-policies/{policy['id']}")
                changed = True
        if not keep:
            api("POST", f"{base}/deployment-branch-policies", {"name": "verifier", "type": "branch"})
            changed = True
    return changed


class Listener:
    """127.0.0.1 only; hands back one code whose state is this run's, and nothing else."""

    def __init__(self, action, manifest_for):
        self.state = secrets.token_urlsafe(32)
        self.code = None
        self.done = threading.Event()
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, text):
                body = f"<!doctype html><meta charset=utf-8><title>sitting</title><p>{html.escape(text)}</p>{self.extra}"
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self):
                self.extra = ""
                url = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(url.query)
                if url.path == "/start" and not listener.done.is_set():
                    target = f"{action}?state={urllib.parse.quote(listener.state)}"
                    manifest = json.dumps(manifest_for(listener.url("/created")))
                    self.extra = (
                        f'<form id=f method=post action="{html.escape(target)}">'
                        f'<input type=hidden name="manifest" value="{html.escape(manifest)}">'
                        "<button>Create the App</button></form><script>document.getElementById('f').submit()</script>"
                    )
                    return self.reply(200, "Sending the verifier App's manifest to the forge.")
                if url.path != "/created" or listener.done.is_set():
                    return self.reply(404, "Nothing here.")
                state = (query.get("state") or [""])[0]
                if not hmac.compare_digest(state.encode(), listener.state.encode()):
                    return self.reply(400, "Refused: the state is not this sitting's, so the code was not used.")
                code = (query.get("code") or [""])[0]
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", code):
                    return self.reply(400, "Refused: the code is not one the forge issues.")
                listener.code = code
                listener.done.set()
                return self.reply(200, "The App is created. Return to the terminal.")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, path):
        return f"http://127.0.0.1:{self.server.server_address[1]}{path}"

    def wait(self):
        try:
            open_url(self.url("/start"))
            self.done.wait(WAIT)
            return self.code
        finally:
            self.server.shutdown()
            self.server.server_close()


def step_key(repo, _commit):
    base = f"repos/{repo}/environments/verify"
    held = [api("GET", f"{base}/{kind}/{name}", missing_ok=True) for kind, name in
            (("secrets", "VERIFIER_APP_KEY"), ("variables", "VERIFIER_APP_ID"), ("variables", "VERIFIER_APP_SLUG"))]
    if all(held):
        return False
    owner = repo.split("/")[0]
    org = api("GET", f"users/{owner}")["type"] == "Organization"
    action = f"{SERVER}/organizations/{owner}/settings/apps/new" if org else f"{SERVER}/settings/apps/new"

    def manifest(redirect):
        return {
            "name": f"verifier {repo}"[:34],
            "url": f"{SERVER}/{repo}",
            "hook_attributes": {"url": f"{SERVER}/{repo}", "active": False},
            "redirect_url": redirect,
            "public": False,
            "default_permissions": {"checks": "write", "metadata": "read"},
            "default_events": [],
        }

    say("  confirm the App in the browser; nothing else is asked of you there")
    code = Listener(action, manifest).wait()
    if code is None:
        raise Refused(f"no callback with this sitting's state came within {WAIT:.0f} s, so no App was made from it")
    app = api("POST", f"app-manifests/{code}/conversions")
    slug, app_id = app["slug"], str(app["id"])
    say(f"  created the App {slug} ({app_id})")
    stored = gh(["secret", "set", "VERIFIER_APP_KEY", "--env", "verify", "--repo", repo], stdin=app.pop("pem"), write=True)
    app.clear()
    if stored.returncode:
        raise Refused(
            f"storing the key failed: {stored.stderr.strip()[:500]}; the App {slug} ({app_id}) has no stored key "
            "and its key cannot be read again, so delete that App in its settings"
        )
    for name, value in (("VERIFIER_APP_ID", app_id), ("VERIFIER_APP_SLUG", slug)):
        proc = gh(["variable", "set", name, "--env", "verify", "--repo", repo, "--body", value], write=True)
        if proc.returncode:
            raise Refused(f"setting {name} failed: {proc.stderr.strip()[:500]}; delete the App {slug} ({app_id})")
    return True


def installed(repo, app_id):
    for inst in api("GET", "user/installations?per_page=100")["installations"]:
        if str(inst.get("app_id")) == app_id:
            listed = api("GET", f"user/installations/{inst['id']}/repositories?per_page=100")["repositories"]
            if any(r["full_name"].lower() == repo.lower() for r in listed):
                return True
    return False


def step_install(repo, _commit):
    base = f"repos/{repo}/environments/verify/variables"
    app_id, slug = (api("GET", f"{base}/{name}")["value"] for name in ("VERIFIER_APP_ID", "VERIFIER_APP_SLUG"))
    if installed(repo, app_id):
        return False
    url = f"{SERVER}/apps/{slug}/installations/new"
    say(f"  install the App on {repo} alone in the browser")
    open_url(url)
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        time.sleep(POLL)
        if installed(repo, app_id):
            return True
    raise Refused(f"the App {slug} is not installed on {repo}; install it at {url}, choosing {repo}")


STEPS = [
    ("the ruleset keeping refs/heads/verifier to admins", step_ruleset),
    ("the verifier branch at the kernel", step_branch),
    ("the verify and review environments", step_environments),
    ("the verifier App and its key", step_key),
    ("the App's installation", step_install),
]


def main(argv):
    if len(argv) != 2 or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", argv[0]) or not re.fullmatch(r"[0-9a-f]{40}", argv[1]):
        print("usage: sitting.py OWNER/NAME <40-hex gates commit>", file=sys.stderr)
        return 2
    repo, commit = argv
    for n, (what, step) in enumerate(STEPS, 1):
        try:
            changed = step(repo, commit)
        except Refused as e:
            print(f"sitting: refused at step {n}, {what}: {e}", file=sys.stderr)
            print(f"sitting: run the same command again to resume at step {n}; {writes} writes made", file=sys.stderr)
            return 1
        say(f"step {n} {what}: {'applied' if changed else 'in place'}")
    say(f"done, {writes} writes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
