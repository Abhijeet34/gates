# refile-fix-sr-25: why fix-sr-25 did not land, and the fix row to re-file

This is a scout report on `main` at `cc7e3c44cd125c2335b835a24791b4dab63db650`, written 2026-11-19 for run `r20261119T064711-81409`.

## What I could not do

- **The findings store.** It is private, so I know finding sr-25 only from the cancelled task's text quoted in the contract.
- **`docs/findings.md`.** The contract cites it for "The weekly sweep", but it is not in this repository (`ls docs` gives `No such file or directory`). I did not check the sweep's rules against it.
- **The blocked head.** `74213a240da6` is not in this repository: `git cat-file -t 74213a240da6` gives `fatal: Not a valid object name`, and no branch under `git ls-remote origin` names sr-25 except an earlier refile scout's branch. So I could not read the gate output that blocked the fix.
  - The cause below is what any fix of this finding meets on `main` today, measured on my own trial fix.
  - I found it consistent with "its gate blocked it", but I have not proven it is the same gate.
- **The real CLI on the review runner.** I did not run the real `claude` CLI there, so the minimal set of variables it needs on that runner is not measured. See "Open choice".
- **Commits.** I committed only this report and `.fac/result.json`. The trial fix lived in a throwaway worktree, which I have deleted. Its full diff is in the appendix.

An earlier scout of this same task (branch `fac-remote/refile-fix-sr-25.r20261119T062108-48320`, commit `5a0b495`) reached the same conclusion. I rebuilt the trial fix and re-ran every measurement myself rather than relying on it; the results below are from this session.

## Why the fix did not land

**Every fix of sr-25 has to edit `run/review.py`, and the permissions-diff gate refuses any change to that file.** An automatic worker therefore cannot get it past the gate, however many rounds it is given.

- `run/permissions-diff.py:486`:
  ```
  ("run/review.py", "the review agent and its verifier's sandbox", None),
  ```
  `None` means "any change" (comment at `run/permissions-diff.py:450`).
- README.md, in "The permissions-diff gate" (around line 161): "Its refusal is the signal that sizes the change L and opens a class-2 decision; an automatic change may not land it."
- The README's surface table lists `run/review.py` with the change kind `any`.

I measured this on my trial commit:

```
$ python3 run/permissions-diff.py --base origin/main --head HEAD
permissions-diff: run/review.py changes the review agent and its verifier's sandbox
permissions-diff: refused: 1 change(s) to the protected surface; an automatic change may not land them: size the change L and open a class-2 decision
rc=1
```

A worker re-trying the change meets this refusal every round, which matches "returned past the gate's 2 rounds". The refusal does not say the fix is wrong. It says the change is a class-2 decision, which only the captain can open and a person has to land.

### A second trap, which may have cost a round

**The naive allowlist breaks three other suites.**

- `run/corpus.py`, `run/lens-eval.py` and `run/critic-eval.py` sit seats through `run/review.py`. They tell the recorded CLI which case to answer through `GATES_CORPUS_CASE` and `CLAUDE_RECORDED_REPLIES` (`.ci/fixtures/review/claude-recorded.py:27-30`).
- An allowlist without those two names makes every recorded run undecided. I measured it by removing them from my trial:

```
test-corpus:      FAIL measured_counts: design: no 'corpus: design: caught 5 of 6 seeded, ...' in: corpus: design/colour-literal: undecided: `claude --version` exited 1: Traceback ...
test-lens-eval:   FAIL measured_counts: no 'lens-eval: product-stakeholder: seeded caught: ...'
test-critic-eval: FAIL measured_counts: no 'critic-eval: claude-sonnet-5-5: wrong numbers held 6 of 6, ...'
```

With the two names kept, all four suites pass (see "Evidence").

## The finding is real and still open on `main`

**How the seat's environment is built today.**
- `run/review.py:108`: `SECRET_WORDS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL")`.
- `run/review.py:208-214`: `scrubbed(keep)` keeps every variable whose name contains none of those words.
- Its exception for `keep` is a prefix match (`k.startswith(keep)`), so it also keeps any name that merely starts with `CLAUDE_CODE_OAUTH_TOKEN`.

**Where it is used.** Every `claude` invocation goes through it:
- `run/review.py:665`: `cli`, which runs `auth status` and `--version`.
- `run/review.py:721`: `sit`, which runs every seat.

**What the docs say.** `README.md:1408`: "Every seat's environment drops credential-named variables except `CLAUDE_CODE_OAUTH_TOKEN`."

**The precedent the finding cites.**
- `run/change-gates.py:67` sets `COMMAND_ENV = ("PATH", "LANG", "TERM", "TMPDIR")`, and `:172` keeps only those plus `LC_*`.
- `run/review.py` already does the same for a reproducing command (`REPRODUCE_ENV`, `:107`, `:217-221`).

**Why the current test misses it.**
- The stub records only the names in `WATCHED = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")` (`.ci/fixtures/review/claude-recorded.py:31`).
- `PLANTED_ARBITRARY` is already planted (`.ci/test-review.py:42`), but only the reproduction's environment is checked for it.

**What passes the denylist today.** These names reach the CLI process that holds the OAuth token:

| Variable | Why it matters |
| --- | --- |
| `ANTHROPIC_BASE_URL` | Redirects the seat's traffic, token included, and lets that host forge the seat's verdict. |
| `NODE_OPTIONS` | Its `--require` runs code inside the CLI. |
| `SSH_AUTH_SOCK` | The SSH agent's socket. |
| `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` | Switch the CLI to another provider. |

The seats have no tools (`--tools ""`), so the model itself cannot read the environment; the exposure is the CLI process.

**Current exposure.** `README.md:1466`: "No workflow calls `run/review.py`; wiring it beside the runner waits for the maintainer's weekly batch." The gap goes live when that wiring lands.

## Evidence: a trial fix that meets the acceptance

The trial (full diff in the appendix) makes four changes:
- It replaces `SECRET_WORDS`/`scrubbed()` with a `SEAT_ENV` tuple plus `LC_*`, used at both call sites.
- The recorded CLI logs every variable name a call received (`"env": sorted(os.environ)`).
- It adds the case `seat_env_is_an_allowlist`: on a clean review, no seat receives `PLANTED_ARBITRARY`.
- It gives that case a mutation row on the allowlist line, and moves `seats_get_no_credentials`'s mutation row there too. The old row quotes the `scrubbed` line, so leaving it would fail the suite with `the mutation matches 0 places`.

Run from a worktree under `/home/user`, not `/tmp`. Under `/tmp` the `reproduction_reads_no_home` mutant still passes, which is not caused by the fix.

```
$ out=$(python3 .ci/test-review.py); echo rc=$?
rc=0
ok   seats_get_no_credentials
ok   without the seat's environment allowlist, seats_get_no_credentials goes red: seats ['finder', 'verifier-F1'] received a planted credential
ok   seat_env_is_an_allowlist
ok   without the seat's environment allowlist, seat_env_is_an_allowlist goes red: seats ['finder'] received PLANTED_ARBITRARY, a name no credential word marks
review tests passed
$ printf '%s\n' "$out" | grep -qx 'ok   seat_env_is_an_allowlist'; echo acceptance=$?
acceptance=0
test-corpus rc=0
test-lens-eval rc=0
test-critic rc=0
test-critic-eval rc=0
```

The mutation line shows that, with the allowlist neutered back to "keep everything", the new case goes red on `PLANTED_ARBITRARY`. That is the finding as `main` has it.

## Open choice inside the allowlist

My trial leaves out the proxy and CA variables (`HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `NODE_EXTRA_CA_CERTS`) and the config-dir variables (`CLAUDE_CONFIG_DIR`, `XDG_CONFIG_HOME`). No test needs them.

- **The proxy and CA variables** can redirect a seat's traffic, which is the same class of risk as `ANTHROPIC_BASE_URL`.
- **The config-dir variables** are needed only if the review runner's login is not under `HOME`.

I recommend adding any of them only after a measured run of the real CLI on the review runner needs it, and recording that date in a comment.

## Recommendation: re-file the fix, sized L, with a class-2 decision

**Re-file the fix; do not accept a residual.**
- The defect is real and the fix is small.
- The fix is proven by the requested regression test, with no other suite broken.
- The risk goes live as soon as review is wired.

The fix failed for a routing reason, not a technical one. If the row goes back to an automatic worker unchanged, it will be cancelled the same way, so it must carry the class-2 decision and be landed by a person.

### Fix row for fac finding triage (finding sr-25)

**Text.** Build the review seats' environment from an allowlist instead of a denylist of credential words.
- In `run/review.py`, replace `SECRET_WORDS`/`scrubbed()` (`:108`, `:208-214`, called at `:665` and `:721`) with `SEAT_ENV` plus `LC_*`, as `run/change-gates.py:67` and `REPRODUCE_ENV` already do.
- `SEAT_ENV` holds `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `TERM`, `TMPDIR`, `CLAUDE_CODE_OAUTH_TOKEN`, and the recorded-CLI names `GATES_CORPUS_CASE` and `CLAUDE_RECORDED_REPLIES`. Without those two names, test-corpus, test-lens-eval and test-critic-eval go red.
- Add proxy, CA or config-dir variables only after a measured run on the review runner needs them.
- Have `.ci/fixtures/review/claude-recorded.py` log every variable name a call received.
- Add `seat_env_is_an_allowlist` to `.ci/test-review.py` with a mutation row on the allowlist line, and move `seats_get_no_credentials`'s mutation row to that line.
- Reword `README.md:1408`, for example: "Every seat's environment is an allowlist, `SEAT_ENV` and `LC_*`; every other variable is dropped."
- `run/review.py` is on the permissions-diff surface (`run/permissions-diff.py:486`, any change). The change is therefore sized L, needs the captain's class-2 decision, and must be landed by a person.

**Acceptance.**
```
out=$(python3 .ci/test-review.py) && printf '%s\n' "$out" | grep -qx 'ok   seat_env_is_an_allowlist'
```
I also recommend running `python3 .ci/test-corpus.py && python3 .ci/test-lens-eval.py && python3 .ci/test-critic.py && python3 .ci/test-critic-eval.py`, to catch the allowlist trap.

### If the captain declines the class-2 change

The residual to accept is this:
- **Until review is wired:** nothing runs `run/review.py`, so nothing is exposed.
- **Once it is wired:** every variable whose name contains none of the five credential words reaches the CLI that holds the OAuth token, including `ANTHROPIC_BASE_URL`, `NODE_OPTIONS` and `SSH_AUTH_SOCK`.

Any acceptance should expire when review is wired.

## Appendix: the trial diff (on `cc7e3c4`)

```diff
diff --git a/.ci/fixtures/review/claude-recorded.py b/.ci/fixtures/review/claude-recorded.py
index 4f95d09..14d7caa 100755
--- a/.ci/fixtures/review/claude-recorded.py
+++ b/.ci/fixtures/review/claude-recorded.py
@@ -57,6 +57,7 @@ def main():
             "seat": seat,
             "cwd": sorted(os.listdir(".")),
             "credentials": [k for k in WATCHED if k in os.environ],
+            "env": sorted(os.environ),
             "prompt": prompt,
         }
         log.write(json.dumps(call) + "\n")
diff --git a/.ci/test-review.py b/.ci/test-review.py
index 31a215e..8e31f09 100755
--- a/.ci/test-review.py
+++ b/.ci/test-review.py
@@ -308,6 +308,16 @@ def seats_get_no_credentials(script):
     return why
 
 
+def seat_env_is_an_allowlist(script):
+    f = Fixture(script)
+    f.reply("finder", {"findings": []})
+    why = expect("a clean review", f.review(), 0)
+    seen = [c["seat"] for c in f.calls() if "PLANTED_ARBITRARY" in c["env"]]
+    if not why and seen:
+        why = f"seats {seen} received PLANTED_ARBITRARY, a name no credential word marks"
+    return why
+
+
 def reproduction_gets_allowlisted_env(script):
     f = Fixture(script)
     probe = 'echo "ssh=${SSH_AUTH_SOCK:-unset} other=${PLANTED_ARBITRARY:-unset} path=${PATH:+set}"; exit 1'
@@ -1083,7 +1093,8 @@ CASES = [
     (finder_timeout_errors, "a seat's wall-time budget", "timeout=max(seconds, 0.001)", "timeout=None"),
     (verifier_timeout_errors, "the verifier pass's deadline", 'deadline = time.monotonic() + budget["verifier_seconds"]', "deadline = time.monotonic() + 3600"),
     (api_key_login_refused, "the subscription login", "method not in AUTH_METHODS", "False"),
-    (seats_get_no_credentials, "the credential scrub", "if k.startswith(keep) or not any(w in k.upper() for w in SECRET_WORDS)", "if True"),
+    (seats_get_no_credentials, "the seat's environment allowlist", 'if k in SEAT_ENV or k.startswith("LC_")', "if True"),
+    (seat_env_is_an_allowlist, "the seat's environment allowlist", 'if k in SEAT_ENV or k.startswith("LC_")', "if True"),
     (reproduction_gets_allowlisted_env, "the reproduction's environment allowlist", "if k in REPRODUCE_ENV or k.startswith(\"LC_\")", "if True"),
     (seats_sit_fresh_and_bare, "a toolless seat", '"--tools", "", ', ""),
     (independence_labels, "the context-only label", 'if other != "unknown" and family(model) != family(other)', 'if family(model) != family(other)'),
diff --git a/run/review.py b/run/review.py
index 971c365..ab77bc0 100644
--- a/run/review.py
+++ b/run/review.py
@@ -105,7 +105,11 @@ SEVERITIES = ("error", "warning", "nit")
 # provider's credentials are not the fleet's harness path.
 AUTH_METHODS = ("claude.ai", "oauth_token")
 REPRODUCE_ENV = ("PATH", "LANG", "TERM")
-SECRET_WORDS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL")
+# What a seat's `claude` needs: where to run, its login and its locale.
+SEAT_ENV = (
+    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TMPDIR",
+    "CLAUDE_CODE_OAUTH_TOKEN", "GATES_CORPUS_CASE", "CLAUDE_RECORDED_REPLIES",
+)  # fmt: skip
 OUTPUT_TAIL = 4000  # bytes of a reproduction's output the verifier reads
 # Other tasks' scratch and the sockets of local services live under these.
 SHARED_ROOTS = ("/tmp", "/var/folders", "/run", "/var/run")  # noqa: S108 hidden, never used
@@ -205,13 +209,9 @@ class Undecided(Exception):
     """The review could not establish a verdict; recorded as `error`, never a pass."""
 
 
-def scrubbed(keep=()):
-    """This environment without any variable whose name marks a credential, but `keep`."""
-    return {
-        k: v
-        for k, v in os.environ.items()
-        if k.startswith(keep) or not any(w in k.upper() for w in SECRET_WORDS)
-    }
+def seat_env():
+    """The few variables a seat's `claude` may inherit; every other name is dropped."""
+    return {k: v for k, v in os.environ.items() if k in SEAT_ENV or k.startswith("LC_")}
 
 
 def reproduce_env(home, tmp):
@@ -662,7 +662,7 @@ def route(rows, lenses, base, head, given, author, store, say):
 def cli(argv):
     try:
         done = bounded(
-            ["claude", *argv], CLI_SECONDS, env=scrubbed(("CLAUDE_CODE_OAUTH_TOKEN",))
+            ["claude", *argv], CLI_SECONDS, env=seat_env()
         )
     except FileNotFoundError as e:
         raise Undecided("claude is not on PATH, so no seat can sit") from e
@@ -718,7 +718,7 @@ def sit(seat, system, prompt, seconds, limit, budget, store, record):
         try:
             done = bounded(
                 argv, seconds, prompt.encode(), cwd=empty,
-                env=scrubbed(("CLAUDE_CODE_OAUTH_TOKEN",)),
+                env=seat_env(),
             )  # fmt: skip
         except FileNotFoundError as e:
             raise Undecided("claude is not on PATH, so no seat can sit") from e
```
