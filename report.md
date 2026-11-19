# refile-fix-sr-25: why fix-sr-25 did not land, and the fix row to re-file

This is a scout report on `main` at `cc7e3c44cd125c2335b835a24791b4dab63db650`, dated 2026-11-19, run `r20261119T070050-46089`.

## What I could not do

- **The findings store is private.** I could not read it, so I know finding sr-25 only from the cancelled task's text quoted in the contract.
- **`docs/findings.md` is not in this repository.** The contract cites its section "The weekly sweep", but there is no `docs/` directory here. I did not check the sweep's rules against it.
- **The blocked head `74213a240da6` is not on any branch here.** `git ls-remote origin | grep -iE "sr-25|74213a"` lists only the two earlier `refile-fix-sr-25` scout branches. So I could not read the gate's actual refusal. The cause below is what any fix of sr-25 meets on `main`, and I reproduced it on a trial commit.
- **The real CLI was not measured.** I did not run the real `claude` CLI on the review runner, so which variables it needs there is not measured.

## Earlier runs of this task

This is the third run. The two earlier runs pushed `fac-remote/refile-fix-sr-25.r20261119T062108-48320` and `…r20261119T064711-81409`. Both reached the conclusion below.

Both runs failed for one reason only: their `.fac/result.json` question's first line was 287 characters, over the 120 a phone shows. This run reached the same finding on its own. Its question starts with one sentence of 91 characters, and the detail is on the lines after it.

## Why the fix did not land

**Any fix of sr-25 has to edit `run/review.py`, and permissions-diff refuses every change to that file.** No automatic worker can land it, however many rounds the gate gives.

- `run/permissions-diff.py:486` lists `("run/review.py", "the review agent and its verifier's sandbox", None)`. In that table, `None` means "any change" (the comment above `SURFACE`).
- The README section "The permissions-diff gate" says what follows: a refusal sizes the change L and opens a class-2 decision, and an automatic change may not land it.

I ran the gate on a trial commit that carried the fix below:

```
$ python3 run/permissions-diff.py --base cc7e3c4 --head HEAD
permissions-diff: run/review.py changes the review agent and its verifier's sandbox
permissions-diff: refused: 1 change(s) to the protected surface; an automatic change may not land them: size the change L and open a class-2 decision
(exit 1)
```

That matches the cancellation: the gate blocked the fix in every round, and the worker was returned past its 2 rounds. The refusal does not say the fix is wrong. It says this change needs a class-2 decision and a person to land it.

A second trap may also have cost the worker a round. A naive allowlist breaks other suites, for two reasons:

- `run/corpus.py:254`, `run/lens-eval.py:170` and `run/critic-eval.py:157` name the recorded case to the CLI through `GATES_CORPUS_CASE`.
- The fixtures pass `CLAUDE_RECORDED_REPLIES` (`.ci/test-corpus.py:74`, `.ci/test-critic-eval.py:110`).

Both names travel in the seat's environment. An allowlist that drops them sends `claude --version` down in `test-corpus`, `test-lens-eval` and `test-critic-eval`.

## The finding is real and still open on `main`

- `run/review.py:108` sets `SECRET_WORDS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL")`.
- `run/review.py:208-214`: `scrubbed(keep)` keeps every variable whose name contains none of those words. The `keep` exception is a prefix match (`k.startswith(keep)`), so a name such as `CLAUDE_CODE_OAUTH_TOKEN_X` is kept too.
- It is used on every `claude` call:
  - `run/review.py:665`, in `cli`, which runs `auth status` and `--version`.
  - `run/review.py:721`, in `sit`, which runs every seat.
- `README.md:1408` documents the current behaviour as a denylist: "Every seat's environment drops credential-named variables except `CLAUDE_CODE_OAUTH_TOKEN`."
- The precedent the finding cites is `run/change-gates.py:67` and `:172`: `COMMAND_ENV = ("PATH", "LANG", "TERM", "TMPDIR")` plus `LC_*`. `run/review.py:107` and `:217-221` already do the same for reproducing commands (`REPRODUCE_ENV`).
- **The current test cannot see the gap.** The recorded CLI logs only the names in `WATCHED` (`.ci/fixtures/review/claude-recorded.py:31`, `:59`). `PLANTED_ARBITRARY` is already planted (`.ci/test-review.py:42`), but only the reproduction case checks for it (`:313`).

The denylist passes names that matter to a process holding the OAuth token:

- `ANTHROPIC_BASE_URL` sends the seat's traffic, with the token, to another host, which can then forge the seat's answer.
- `NODE_OPTIONS` can `--require` code into the CLI.
- `SSH_AUTH_SOCK` passes through.
- `CLAUDE_CODE_USE_BEDROCK` and `CLAUDE_CODE_USE_VERTEX` pass through.

Seats run with no tools (`--tools ""`), so the model cannot read the environment. The exposure is the CLI process itself.

Today nothing is exposed. `README.md:1466` says: "No workflow calls `run/review.py`; wiring it beside the runner waits for the maintainer's weekly batch." The gap goes live when review is wired.

## Evidence: a trial fix that meets the acceptance

I made this trial in a throwaway worktree off `cc7e3c4`, outside `/tmp`, then deleted the worktree. The diff, without index lines:

```diff
--- a/run/review.py
+++ b/run/review.py
@@ -105,7 +105,12 @@
 REPRODUCE_ENV = ("PATH", "LANG", "TERM")
-SECRET_WORDS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL")
+# What a seat's `claude` needs: where to run, its login and its locale, plus the
+# names a recorded CLI reads to pick its reply.
+SEAT_ENV = (
+    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TMPDIR",
+    "CLAUDE_CODE_OAUTH_TOKEN", "GATES_CORPUS_CASE", "CLAUDE_RECORDED_REPLIES",
+)  # fmt: skip
@@ -205,13 +210,9 @@
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
@@ cli() and sit()
-    env=scrubbed(("CLAUDE_CODE_OAUTH_TOKEN",))
+    env=seat_env()

--- a/.ci/fixtures/review/claude-recorded.py
+++ b/.ci/fixtures/review/claude-recorded.py
             "prompt": prompt,
+            "env": sorted(os.environ),

--- a/.ci/test-review.py
+++ b/.ci/test-review.py
+def seat_env_is_an_allowlist(script):
+    f = Fixture(script)
+    f.reply("finder", {"findings": []})
+    why = expect("a clean review", f.review(), 0)
+    seen = [c["seat"] for c in f.calls() if "PLANTED_ARBITRARY" in c["env"]]
+    if not why and seen:
+        why = f"seats {seen} received PLANTED_ARBITRARY, a name no credential word marks"
+    return why
 CASES = [
-    (seats_get_no_credentials, "the credential scrub", "if k.startswith(keep) or not any(w in k.upper() for w in SECRET_WORDS)", "if True"),
+    (seats_get_no_credentials, "the seat's environment allowlist", 'if k in SEAT_ENV or k.startswith("LC_")', "if True"),
+    (seat_env_is_an_allowlist, "the seat's environment allowlist", 'if k in SEAT_ENV or k.startswith("LC_")', "if True"),
```

What the trial printed:

```
$ out=$(python3 .ci/test-review.py); echo rc=$?
ok   seats_get_no_credentials
ok   without the seat's environment allowlist, seats_get_no_credentials goes red: seats ['finder', 'verifier-F1'] received a planted credential
ok   seat_env_is_an_allowlist
ok   without the seat's environment allowlist, seat_env_is_an_allowlist goes red: seats ['finder'] received PLANTED_ARBITRARY, a name no credential word marks
review tests passed
rc=0
$ printf '%s\n' "$out" | grep -qx 'ok   seat_env_is_an_allowlist'; echo $?
0
$ for t in corpus lens-eval critic critic-eval; do python3 .ci/test-$t.py; done
corpus rc=0 / lens-eval rc=0 / critic rc=0 / critic-eval rc=0
```

Three things for whoever lands the fix:

- **Move the old mutation row.** The `seats_get_no_credentials` row (`.ci/test-review.py:1086`) quotes the old `scrubbed` line. It must move to the new control, or the suite fails because its mutation matches nothing.
- **Run `.ci/test-review.py` from a checkout outside `/tmp`.** `reproduction_reads_no_home` builds its fake home under the repository root. Under `/tmp` the jail hides that home anyway, so the case's mutant stays green, and that failure is not the fix's.
- **Reword `README.md:1408` in the same change**, for example: "Every seat's environment is an allowlist, `SEAT_ENV` and `LC_*`; every other variable is dropped."

The change touches no `.ci/gate-registry.toml` row and adds no gate file, so `gates-proof` needs no new row.

On proxy variables: my trial left out `HTTPS_PROXY`, `NODE_EXTRA_CA_CERTS` and the config-dir variables. They redirect where the token goes, which is the same class of risk as `ANTHROPIC_BASE_URL`. No test needs them. Add one only after a measured run on the review runner shows the CLI needs it, and record the date of that run.

## Recommendation: re-file the fix row, sized L under a class-2 decision

I recommend re-filing rather than accepting a residual:

- The defect is real.
- The fix is small, and the requested test proves it.
- The risk goes live the moment review is wired.

The fix failed for a routing reason, not a design one: an automatic worker cannot land a change to `run/review.py`. So the row must carry the class-2 decision and go to a person. Sent back to an automatic worker unchanged, it will be cancelled again. `.fac/result.json` asks the captain this question.

### Fix row for fac finding triage (finding sr-25)

**text:** Build the review seats' environment from an allowlist, replacing the denylist of credential words. Size the change L. `run/review.py` is on permissions-diff's surface for any change (`run/permissions-diff.py:486`), so it needs the captain's class-2 decision and a person to land it. The steps:

1. In `run/review.py`, replace `SECRET_WORDS` and `scrubbed()` (`:108`, `:208-214`, used at `:665` and `:721`) with a `SEAT_ENV` tuple plus `LC_*`, as `run/change-gates.py:67` and `REPRODUCE_ENV` already do.
   - `SEAT_ENV` holds `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `TERM`, `TMPDIR` and `CLAUDE_CODE_OAUTH_TOKEN`.
   - It also holds the recorded-CLI names `GATES_CORPUS_CASE` and `CLAUDE_RECORDED_REPLIES`, without which `test-corpus`, `test-lens-eval` and `test-critic-eval` go red.
   - Add proxy, CA or config-dir variables only once a measured run on the review runner needs them.
2. Have `.ci/fixtures/review/claude-recorded.py` log every variable name each call received.
3. Add the case `seat_env_is_an_allowlist` to `.ci/test-review.py`: no seat receives the planted `PLANTED_ARBITRARY`. Give it a mutation row on the allowlist line, and move the `seats_get_no_credentials` mutation to that line too.
4. Reword `README.md:1408`.

**acceptance:**

```
out=$(python3 .ci/test-review.py) && printf '%s\n' "$out" | grep -qx 'ok   seat_env_is_an_allowlist'
```

I also recommend running `python3 .ci/test-corpus.py && python3 .ci/test-lens-eval.py && python3 .ci/test-critic.py && python3 .ci/test-critic-eval.py` to catch the allowlist trap above.

### The residual, if the captain declines the class-2 change

Until review is wired, nothing runs `run/review.py`, so nothing is exposed. Once it is wired, any variable whose name holds none of the five credential words reaches the CLI that holds the OAuth token, among them `ANTHROPIC_BASE_URL`, `NODE_OPTIONS` and `SSH_AUTH_SOCK`. If the captain accepts this residual, the acceptance should expire when review is wired.
