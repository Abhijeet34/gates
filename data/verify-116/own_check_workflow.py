#!/usr/bin/env python3
"""Scout's own check, written from PR 116's claim only (before reading run/seats.py).

Claim: owner-verify.yml gains a `review` job in environment `review` with
contents: read and pull-requests: read; it is the only job reading
secrets.CLAUDE_CODE_OAUTH_TOKEN; classify needs review with if always();
no trigger added; no other job's permissions changed.

Usage: own_check_workflow.py BASE_WORKFLOW HEAD_WORKFLOW ; exit 0 iff all hold.
"""
import re, sys, yaml

def load(p):
    t = open(p).read()
    d = yaml.safe_load(t)
    on = d.get(True, d.get("on"))
    return t, d, on

bt, b, bon = load(sys.argv[1])
ht, h, hon = load(sys.argv[2])
fails = []
def need(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)

need(bon == hon, f"triggers unchanged: base={bon!r} head={hon!r}")
need(b.get("permissions") == h.get("permissions"), "top-level permissions unchanged")
hj, bj = h["jobs"], b["jobs"]
need("review" in hj, "head has a review job")
r = hj.get("review", {})
env = r.get("environment")
env = env.get("name") if isinstance(env, dict) else env
need(env == "review", f"review job environment is review (got {env!r})")
need(r.get("permissions") == {"contents": "read", "pull-requests": "read"},
     f"review permissions exactly contents/pull-requests read (got {r.get('permissions')!r})")
for name, job in bj.items():
    need(name in hj, f"job {name} still present")
    if name in hj:
        need(job.get("permissions") == hj[name].get("permissions"), f"job {name} permissions unchanged")
        need(job.get("environment") == hj[name].get("environment"), f"job {name} environment unchanged")
c = hj.get("classify", {})
needs = c.get("needs"); needs = [needs] if isinstance(needs, str) else (needs or [])
need("review" in needs, f"classify needs review (got {needs!r})")
need("always()" in str(c.get("if", "")), f"classify if always() (got {c.get('if')!r})")
for name, job in hj.items():
    s = yaml.safe_dump(job)
    uses = "CLAUDE_CODE_OAUTH_TOKEN" in s
    need(uses == (name == "review"), f"job {name} reads CLAUDE_CODE_OAUTH_TOKEN: {uses}")
secrets_head = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", ht))
secrets_base = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", bt))
need(secrets_head - secrets_base <= {"CLAUDE_CODE_OAUTH_TOKEN"},
     f"no new secret but CLAUDE_CODE_OAUTH_TOKEN (new: {sorted(secrets_head - secrets_base)})")
for bad in ("pull_request_target", "workflow_run", "issue_comment", "self-hosted"):
    need(bad not in ht, f"no {bad} in head workflow")
print("own-check-workflow:", "FAIL" if fails else "PASS")
sys.exit(1 if fails else 0)
