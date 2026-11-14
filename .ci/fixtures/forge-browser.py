#!/usr/bin/env python3
"""A stand-in maintainer's browser on the stand-in forge, for .ci/test-verifier.py.

    FORGE=<state dir> forge-browser.py <url>

A sitting's start page: read its form as the forge would, keep the manifest
under a fresh code, and follow the redirect to the listener; with
<state>/wrong-state it first answers with another state, and with only that
(<state>/only-wrong-state) it never answers rightly. An installation page:
install the App on <state>/install-repo, unless <state>/no-install exists.
Each listener reply is appended to <state>/browser.jsonl.
"""

import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

state = Path(os.environ["FORGE"])
db_path = state / "forge.json"
url = sys.argv[1]


def load():
    return json.loads(db_path.read_text())


def save(db):
    tmp = db_path.with_suffix(f".{os.getpid()}")
    tmp.write_text(json.dumps(db, indent=1))
    tmp.replace(db_path)


def get(target):
    try:
        with urllib.request.urlopen(target, timeout=10) as r:
            status, body = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode()
    with open(state / "browser.jsonl", "a") as f:
        f.write(json.dumps({"url": target, "status": status, "body": body}) + "\n")
    return status, body


if url.startswith("http://127.0.0.1:"):
    status, page = get(url)
    action = html.unescape(re.search(r'<form[^>]* action="([^"]+)"', page)[1])
    manifest = json.loads(
        html.unescape(re.search(r'name="manifest" value="([^"]*)"', page)[1])
    )
    query = urllib.parse.parse_qs(urllib.parse.urlparse(action).query)
    with open(state / "form.json", "w") as f:
        json.dump({"action": action, "manifest": manifest}, f)
    db = load()
    code = f"code{len(db.setdefault('codes', {})) + len(db.get('apps', {}))}"
    db["codes"][code] = manifest
    save(db)
    redirect = manifest["redirect_url"]
    if (state / "wrong-state").exists() or (state / "only-wrong-state").exists():
        get(f"{redirect}?code={code}&state=not-the-sitting-s")
    if not (state / "only-wrong-state").exists():
        get(f"{redirect}?code={code}&state={query['state'][0]}")
elif m := re.match(r"https://forge\.invalid/apps/([^/]+)/installations/new", url):
    if not (state / "no-install").exists():
        db = load()
        installs = db.setdefault("installs", [])
        repo = (state / "install-repo").read_text().strip()
        installs.append(
            {
                "id": 7000 + len(installs),
                "app_id": db["apps"][m[1]]["id"],
                "repos": [repo],
            }
        )
        save(db)
else:
    sys.exit(f"forge-browser: no page at {url}")
