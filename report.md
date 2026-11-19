# refile-fix-sr-25: why fix-sr-25 did not land, and the class-2 fix row

Scout report on `main` at `cc7e3c44cd125c2335b835a24791b4dab63db650`, 2026-11-19, run `r20261119T071020-11093`.
The captain answered decision `sr-25-class-2-landing` with `refile-as-class-2`, so this run asks nothing more. It delivers the fix row below, sized L, for a person to land under that class-2 decision.

## What I could not do

- **I could not read the private findings store.** I know sr-25 only from the cancelled task's text in the contract.
- **`docs/findings.md` is not in this repository.** There is no `docs/` directory, so I did not check the sweep's rules against "The weekly sweep".
- **The blocked head `74213a240da6` is not reachable.** `git cat-file -t 74213a240da6` answers `Not a valid object name`. I could not read that round's refusal. The cause below is what every fix of sr-25 meets on `main`, and I reproduced it on a trial commit.
- **I did not run the real `claude` CLI on a review runner.** Which variables the real CLI needs there has not been measured (see "Residual on the allowlist's contents").

## Why the fix did not land

**Every fix of sr-25 has to edit `run/review.py`, and permissions-diff refuses any change to that file.** No automatic worker can land it, however many rounds it gets.

- `run/permissions-diff.py:486`: `("run/review.py", "the review agent and its verifier's sandbox", None)`. In `SURFACE`, `None` means any change.
- README.md, "The permissions-diff gate" (`README.md:161`): the refusal "sizes the change L and opens a class-2 decision; an automatic change may not land it."

Here is the gate on my trial commit, which carries the fix below:

```
$ python3 run/permissions-diff.py --base cc7e3c4 --head HEAD
permissions-diff: run/review.py changes the review agent and its verifier's sandbox
permissions-diff: refused: 1 change(s) to the protected surface; an automatic change may not land them: size the change L and open a class-2 decision
(exit 1)
```

That matches the cancellation: the gate blocked the fix every round, and the worker was returned past the gate's 2 rounds. The refusal does not say the fix is wrong. It says the fix needs a person under a class-2 decision.

There is also a second trap a worker can lose a round to. A naive allowlist drops `GATES_CORPUS_CASE` (`run/corpus.py:254`, `run/lens-eval.py`, `run/critic-eval.py`) and `CLAUDE_RECORDED_REPLIES` (the fixtures of `.ci/test-corpus.py` and `.ci/test-critic-eval.py`). The recorded CLI picks its reply by those names, so dropping them turns those suites red.

## The finding is real and still open on `main`

- `run/review.py:108`: `SECRET_WORDS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL")`.
- `run/review.py:208-214`: `scrubbed(keep)` keeps every variable whose name contains none of those words, plus any name that *starts with* a `keep` entry.
- `run/review.py:665` (`cli`: `auth status`, `--version`) and `run/review.py:721` (`sit`: every seat) pass `scrubbed(("CLAUDE_CODE_OAUTH_TOKEN",))`.
- `README.md:1408` documents that denylist: "Every seat's environment drops credential-named variables except `CLAUDE_CODE_OAUTH_TOKEN`."
- The precedent the finding cites: `run/change-gates.py:67` `COMMAND_ENV = ("PATH", "LANG", "TERM", "TMPDIR")` plus `LC_*` at `:172`. `run/review.py:107,217-221` already does the same for reproducing commands (`REPRODUCE_ENV`).
- Names the denylist passes to a process that holds the OAuth token:
  - `ANTHROPIC_BASE_URL`, which sends the token and the seat's traffic to another host that can forge the answer.
  - `NODE_OPTIONS`, which can `--require` code into the CLI.
  - `SSH_AUTH_SOCK`.
  - `CLAUDE_CODE_USE_BEDROCK` and `CLAUDE_CODE_USE_VERTEX`.
- **The current test cannot see the gap.** The recorded CLI logs only the names in `WATCHED` (`.ci/fixtures/review/claude-recorded.py:31,59`). `PLANTED_ARBITRARY` is planted (`.ci/test-review.py:42`), but only the reproduction case checks for it (`:311-322`).
- **Exposure today: none.** `README.md:1466`: "No workflow calls `run/review.py`; wiring it beside the runner waits for the maintainer's weekly batch." The gap goes live when review is wired.

## Evidence: the trial fix meets the acceptance

I built the trial in a throwaway worktree off `cc7e3c4`, outside `/tmp`. It touches 3 files: +24 and −11 lines. The diff, abridged:

```diff
--- a/run/review.py
-SECRET_WORDS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL")
+# What a seat's `claude` needs: where to run, its login and its locale, plus the
+# names a recorded CLI reads to pick its reply.
+SEAT_ENV = (
+    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TMPDIR",
+    "CLAUDE_CODE_OAUTH_TOKEN", "GATES_CORPUS_CASE", "CLAUDE_RECORDED_REPLIES",
+)  # fmt: skip
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
 # in cli() and sit():
-    env=scrubbed(("CLAUDE_CODE_OAUTH_TOKEN",))
+    env=seat_env()

--- a/.ci/fixtures/review/claude-recorded.py
             "prompt": prompt,
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
 CASES = [
-    (seats_get_no_credentials, "the credential scrub", "if k.startswith(keep) or not any(w in k.upper() for w in SECRET_WORDS)", "if True"),
+    (seats_get_no_credentials, "the seat's environment allowlist", 'if k in SEAT_ENV or k.startswith("LC_")', "if True"),
+    (seat_env_is_an_allowlist, "the seat's environment allowlist", 'if k in SEAT_ENV or k.startswith("LC_")', "if True"),
```

Output from the trial worktree:

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
corpus rc=0  lens-eval rc=0  critic rc=0  critic-eval rc=0
```

The same acceptance on unchanged `main` fails, so the test detects the gap:

```
$ out=$(python3 .ci/test-review.py); printf '%s\n' "$out" | grep -qx 'ok   seat_env_is_an_allowlist'; echo $?
1
```

Notes for whoever lands it:

- **Move the old mutation row.** The `seats_get_no_credentials` row (`.ci/test-review.py:1086`) quotes the old `scrubbed` line. It must move to the new control, as in the diff, or the suite fails because the mutation matches 0 places.
- **Reword `README.md:1408` in the same change**, for example: "Every seat's environment is an allowlist, `SEAT_ENV` and `LC_*`; every other variable is dropped."
- **Run `.ci/test-review.py` from a checkout outside `/tmp`.** Under `/tmp`, the jail hides the fixture's fake home anyway, so the mutant of `reproduction_reads_no_home` stays green. That failure would not be this change's.
- **No `gates-proof` work is needed.** The change adds no gate file and changes no `.ci/gate-registry.toml` row.

## The fix row (re-filed as class 2)

| field | value |
|---|---|
| id | fix-sr-25 (re-filed) |
| class | 2, under the captain's answer `refile-as-class-2` to `sr-25-class-2-landing` |
| size | L, because permissions-diff refuses `run/review.py` |
| who lands it | a person, not an automatic worker |
| change | Build the seat environment (`cli` and `sit` in `run/review.py`) from an allowlist `SEAT_ENV` plus `LC_*`, as `run/change-gates.py` does for its commands. Record the env names in `.ci/fixtures/review/claude-recorded.py`. Add `seat_env_is_an_allowlist` to `.ci/test-review.py`, move the `seats_get_no_credentials` mutation row, and reword `README.md:1408`. The trial diff above is a working starting point. |
| acceptance | `out=$(python3 .ci/test-review.py) && printf '%s\n' "$out" \| grep -qx 'ok   seat_env_is_an_allowlist'` |
| finding | sr-25, in the private findings store |

## Residual on the allowlist's contents

The trial allowlist leaves out the proxy and CA variables (`HTTPS_PROXY`, `NO_PROXY`, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`) and the config-dir variables (`CLAUDE_CONFIG_DIR`, `XDG_CONFIG_HOME`). Those names redirect where the token goes, which is the same class of risk as `ANTHROPIC_BASE_URL`, and no test needs them.

If the real CLI on the review runner turns out to need one, it fails closed:

1. `harness()` (`run/review.py:672-689`) runs `claude auth status` through the same environment.
2. A missing login there raises `Undecided`.
3. `Undecided` is recorded as `error`, never as a pass.

So add a name only after a measured run on the runner shows the CLI needs it, and date that run.

## Recommendation

Land the fix row above as a class-2, size-L change by a person before `run/review.py` is wired into any workflow. Do not send it to an automatic worker: permissions-diff will refuse it the same way it refused fix-sr-25.
