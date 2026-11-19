# refile-fix-sr-25: why fix-sr-25 did not land, and the fix row to re-file

Scout report on `main` at `cc7e3c44cd125c2335b835a24791b4dab63db650`, 2026-11-19.

## What I could not do

- I could not read the private findings store, so I know sr-25 only from the cancelled task's text quoted in the contract.
- `docs/findings.md`, which the contract cites for "The weekly sweep", is not in this repository (`grep: docs/findings.md: No such file or directory`). It is presumably in the private repository, so I did not check the sweep's rules against it.
- The blocked head `74213a240da6` is not in this repository. `git cat-file -t 74213a240da6` gives `fatal: Not a valid object name`. No remote branch carries a `seat_env_is_an_allowlist` case or names sr-25. I checked by fetching every `refs/heads/*` and running `git grep` over them, and `git ls-remote origin | grep -i sr-25` finds nothing. So I could not read the gate's actual refusal. The cause below is what any fix of this finding meets on `main` today. I reproduced it with a trial fix.
- I did not run anything on the macOS runner the review rows are meant for. Which variables the real `claude` CLI needs there is not measured. See "Open choice" below.
- I committed only this report and `.fac/result.json`. The trial fix stayed in a throwaway worktree, which I have deleted. Its diff is reproduced below.

## Why the fix did not land

**Every fix of sr-25 must edit `run/review.py`, and permissions-diff refuses any change to that file outright.** So the fix cannot pass an automatic gate, however many rounds it gets.

- `run/permissions-diff.py:486`: `("run/review.py", "the review agent and its verifier's sandbox", None)`. Here `None` means "any change" (`run/permissions-diff.py:450`).
- `README.md:161`: "Its refusal is the signal that sizes the change L and opens a class-2 decision; an automatic change may not land it." `README.md:183` lists `run/review.py` with the change kind "any".

I measured this on a trial commit that carried the fix below:

```
$ python3 run/permissions-diff.py --base origin/main --head HEAD
permissions-diff: run/review.py changes the review agent and its verifier's sandbox
permissions-diff: refused: 1 change(s) to the protected surface; an automatic change may not land them: size the change L and open a class-2 decision
(exit 1)
```

That matches the cancellation: the gate blocked the fix twice and returned it past its 2 rounds. A worker re-trying the same change can only get the same refusal. The refusal does not mean the fix is wrong. It means this change is a class-2 decision that a person has to land.

There is a second trap, which I found by measuring. It may have cost the worker one of its rounds. A naive allowlist breaks three other suites. `run/lens-eval.py` and `run/critic.py` sit their seats through `review.sit`. `run/corpus.py`, `run/lens-eval.py` and `run/critic-eval.py` tell the recorded CLI which case to answer through `GATES_CORPUS_CASE`. The tests also pass `CLAUDE_RECORDED_REPLIES` (`.ci/fixtures/review/claude-recorded.py:27`). Both names travel in the seat's environment, so an allowlist that leaves them out breaks every recorded run:

```
test-corpus rc=1      FAIL measured_counts: ... corpus: design/colour-literal: undecided: `claude --version` exited 1: Traceback ...
test-lens-eval rc=1   FAIL seed_finding_catches: ... undecided: `claude --version` exited 1 ...
test-critic-eval rc=1 FAIL hold_inside_seed_catches: ... undecided: `claude --version` exited 1 ...
```

With the two names allowed, all four suites pass (see "Evidence").

## The finding is real and still open

- `run/review.py:108`: `SECRET_WORDS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL")`.
- `run/review.py:208-214`: `scrubbed(keep)` keeps every variable unless its name contains one of those words. The `keep` exception is a prefix match (`k.startswith(keep)`), so `CLAUDE_CODE_OAUTH_TOKEN_ANYTHING` is kept as well.
- It is used for every `claude` call: `run/review.py:665` (`cli`, which covers `auth status` and `--version`) and `run/review.py:721` (`sit`, which covers every seat).
- `README.md:1408` documents the denylist: "Every seat's environment drops credential-named variables except `CLAUDE_CODE_OAUTH_TOKEN`."
- The precedent the finding cites: `run/change-gates.py:67` has `COMMAND_ENV = ("PATH", "LANG", "TERM", "TMPDIR")`, and `:172` keeps only those plus `LC_*`. `run/review.py:107` and `:217-221` already do the same for a reproducing command (`REPRODUCE_ENV`).
- The current test cannot see the gap. The stub logs only the names in `WATCHED = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")` (`.ci/fixtures/review/claude-recorded.py:31`, `:59`). `PLANTED_ARBITRARY` is already planted (`.ci/test-review.py:42`), but only the reproduction case checks for it (`:311`).

Some names that pass the denylist today and matter to a process holding the OAuth token:

- `SSH_AUTH_SOCK`
- `NODE_OPTIONS` (`--require` runs arbitrary code inside the CLI)
- `ANTHROPIC_BASE_URL` (sends the seat's traffic, with its token, to another host, which can also forge the seat's answer)
- `CLAUDE_CODE_USE_BEDROCK` / `_VERTEX`

The seats have no tools (`--tools ""`), so the model itself cannot read the environment. The exposure is the CLI process.

Current exposure is limited: `README.md:1466` says "No workflow calls `run/review.py`; wiring it beside the runner waits for the maintainer's weekly batch." The gap becomes live when that wiring lands, and that is the reason to fix it first rather than accept it.

## Evidence: a trial fix that meets the acceptance

The trial diff, on `cc7e3c4`:

```diff
--- a/run/review.py
-SECRET_WORDS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL")
+# What a seat's `claude` needs: where to run, its login, its locale and its proxy.
+SEAT_ENV = (
+    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TMPDIR",
+    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME",
+    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS",
+    "GATES_CORPUS_CASE", "CLAUDE_RECORDED_REPLIES",
+)  # fmt: skip
-def scrubbed(keep=()):
-    ... if k.startswith(keep) or not any(w in k.upper() for w in SECRET_WORDS)
+def seat_env():
+    """The few variables a seat's `claude` may inherit; every other name is dropped."""
+    return {k: v for k, v in os.environ.items() if k in SEAT_ENV or k.startswith("LC_")}
 (both call sites: env=scrubbed(("CLAUDE_CODE_OAUTH_TOKEN",)) -> env=seat_env())

--- a/.ci/fixtures/review/claude-recorded.py   (the call log also records every variable name)
+            "env": sorted(os.environ),

--- a/.ci/test-review.py
+def seat_env_is_an_allowlist(script):
+    f = Fixture(script)
+    f.reply("finder", {"findings": []})
+    why = expect("a clean review", f.review(), 0)
+    seen = [c["seat"] for c in f.calls() if "PLANTED_ARBITRARY" in c["env"]]
+    if not why and seen:
+        why = f"seats {seen} received PLANTED_ARBITRARY, a name no credential word marks"
+    return why
 CASES:
-    (seats_get_no_credentials, "the credential scrub", "if k.startswith(keep) or not any(w in k.upper() for w in SECRET_WORDS)", "if True"),
+    (seats_get_no_credentials, "the seat's environment allowlist", 'if k in SEAT_ENV or k.startswith("LC_")', "if True"),
+    (seat_env_is_an_allowlist, "the seat's environment allowlist", 'if k in SEAT_ENV or k.startswith("LC_")', "if True"),
```

Results of the trial, run from a worktree outside `/tmp`:

```
$ python3 .ci/test-review.py            -> review tests passed (rc 0)
ok   seat_env_is_an_allowlist
ok   without the seat's environment allowlist, seat_env_is_an_allowlist goes red: seats ['finder'] received PLANTED_ARBITRARY, a name no credential word marks
ok   without the seat's environment allowlist, seats_get_no_credentials goes red: seats ['finder', 'verifier-F1'] received a planted credential
$ out=$(python3 .ci/test-review.py) && printf '%s\n' "$out" | grep -qx 'ok   seat_env_is_an_allowlist'; echo $?   -> 0
test-corpus / test-lens-eval / test-critic / test-critic-eval  -> all passed
```

On `main`, unchanged, `python3 .ci/test-review.py` also passes, so the trial broke nothing it ran.

Two sharp edges for whoever lands the fix:

- The existing `seats_get_no_credentials` mutation row (`.ci/test-review.py:1086`) quotes the old `scrubbed` line. It must move to the new control, or the suite fails with `the mutation matches 0 places`.
- Run `.ci/test-review.py` from a checkout outside `/tmp`. `reproduction_reads_no_home` makes its fake home under the repository root, and under `/tmp` the jail hides it anyway, so the case's mutant "still passes" (`.ci/test-review.py:338`). My first trial worktree in the scratchpad failed that way, and the failure was not the fix's.

`README.md:1408` should be reworded in the same change, for example: "Every seat's environment is an allowlist, `SEAT_ENV` and `LC_*`; every other variable is dropped." The change touches no `.ci/gate-registry.toml` row and adds no gate file, so `gates-proof` needs no new row.

## Open choice inside the allowlist

The trial passes the proxy variables (`HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`, `NODE_EXTRA_CA_CERTS`) because a runner behind a proxy may need them. They are also a way to redirect a seat's traffic, which is the same class of risk as `ANTHROPIC_BASE_URL`.

No test depends on them. I recommend leaving them out unless a measured run of the real CLI on the review runner needs them, and adding them then with that date recorded. `CLAUDE_CONFIG_DIR` and `XDG_CONFIG_HOME` are in the trial only to keep the login readable when it is not under `HOME`. They come under the same rule.

## Recommendation: re-file the fix row, sized L with a class-2 decision

I recommend re-filing the fix, not accepting a residual. The defect is real, the fix is small and proven by the requested test, and the risk becomes live as soon as review is wired. The reason it failed is a routing reason: an automatic worker cannot land a change to `run/review.py`. So the row has to come with the class-2 decision, and a person lands it. Sending it back to an automatic worker unchanged will be cancelled the same way.

The fix row for fac finding triage (finding sr-25):

- **text:** Build the review seats' environment from an allowlist instead of a denylist of credential words.
  - In `run/review.py`, replace `SECRET_WORDS`/`scrubbed()` (`:108`, `:208-214`, used at `:665` and `:721`) with a `SEAT_ENV` tuple plus `LC_*`, as `run/change-gates.py:67` and `REPRODUCE_ENV` already do. The tuple holds `PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `TERM`, `TMPDIR`, `CLAUDE_CODE_OAUTH_TOKEN`, and the recorded-CLI names `GATES_CORPUS_CASE` and `CLAUDE_RECORDED_REPLIES`, without which `test-corpus`, `test-lens-eval` and `test-critic-eval` go red. Add proxy, CA or config-dir variables only if a measured run on the review runner needs them.
  - Have `.ci/fixtures/review/claude-recorded.py` log every variable name a call received.
  - Add `seat_env_is_an_allowlist` to `.ci/test-review.py`: no seat receives the planted `PLANTED_ARBITRARY`. Give it a mutation row on the allowlist line, and move `seats_get_no_credentials`'s mutation to that line too.
  - Reword `README.md:1408`.
  - `run/review.py` is on permissions-diff's surface (`run/permissions-diff.py:486`, any change), so this change is sized L. It needs the captain's class-2 decision, and a person lands it; an automatic worker cannot pass the gate.
- **acceptance:**
  ```
  out=$(python3 .ci/test-review.py) && printf '%s\n' "$out" | grep -qx 'ok   seat_env_is_an_allowlist'
  ```
  I recommend also running `python3 .ci/test-corpus.py && python3 .ci/test-lens-eval.py && python3 .ci/test-critic.py && python3 .ci/test-critic-eval.py`, to catch the allowlist trap above.

If the captain declines the class-2 change, the residual to accept is this: until review is wired, nothing runs `run/review.py`, so nothing is exposed. Once it is wired, any variable outside the five credential words reaches the CLI that holds the OAuth token, including `ANTHROPIC_BASE_URL`, `NODE_OPTIONS` and `SSH_AUTH_SOCK`. If accepted, that acceptance should expire when review is wired.
