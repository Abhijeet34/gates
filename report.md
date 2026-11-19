# refile-fix-sr-18: why fix-sr-18 did not land, and the fix row to re-file

Scout report on `main` at `cc7e3c44cd125c2335b835a24791b4dab63db650`, 2026-11-19.

## What I could not do

- I could not read the private findings store, so sr-18 and sr2-15 are known to me only through the cancelled task's text quoted in the contract.
- `docs/findings.md`, which the contract cites for "The weekly sweep", does not exist in this repository (`sed: can't read docs/findings.md`). It presumably lives in the private repository, so I did not check the sweep's rules against it.
- `api.github.com` is refused by this session's proxy (HTTP 403), so I could not cross-check the zizmor tarball digest below against GitHub's asset digest. I checked it only against the bytes I downloaded and the binary's own version output.
- I changed nothing but this report. Because this task is a scout, the fix itself is left to the re-filed row.

## Why the fix did not land

The cause was the launch, not the code. Both runs of fix-sr-18 died before a cloud session was reported (`launch: no cloud session was reported (signal: killed)`, with terminal control bytes in the output). No branch reached the repository. Nothing in the tree blocks the change: three of its four parts can be done as stated, and the fourth is already done but its acceptance names the wrong file.

## What I found, part by part

### 1. `actions/checkout` persisting credentials in `ci.yml`: still open

```
$ grep -c 'actions/checkout@' .github/workflows/ci.yml        -> 6
$ grep -c 'persist-cred[a-z]*: false' .github/workflows/ci.yml -> 0
```

The six checkouts are at `.github/workflows/ci.yml:42`, `:110`, `:119`, `:147`, `:174` and `:204`. None of them sets `persist-credentials: false`. Every `shared-*.yml` already sets it, for example `shared-secret-scan.yml:44` and `shared-gate-shadow.yml:42`, so `ci.yml` is the only workflow left without it.

Nothing in the CI jobs uses the persisted token after checkout:

- `grep -n 'git push\|git fetch\|origin' .ci/check.sh` finds nothing.
- The `secrets` job's `fetch-depth: 0` (`ci.yml:120-122`) fetches during the checkout itself, so it does not need the token afterwards.

Adding `with: persist-credentials: false` to each of the six checkouts is therefore safe. The checkout at `:119` already has a `with:` block, so the input goes into that block.

`run/permissions-diff.py` covers `.github/workflows/*.yml`, but only through "a workflow token's permissions" (`run/permissions-diff.py:452` onward). This edit leaves the `permissions:` blocks alone, so that gate should not refuse it.

### 2. `pipx install zizmor==1.29.0` without a digest: still open

`ci.yml:79` installs zizmor by version only. The same step checks osv-scanner (`:83`) and actionlint (`:87`) with `sha256sum --check --strict`.

Two digests can replace the bare pipx install. I measured both on 2026-11-19:

| Artifact | sha256 | Source |
|---|---|---|
| `zizmor-x86_64-unknown-linux-gnu.tar.gz` (GitHub release v1.29.0) | `dd96df044a6e8538d5f423790f453bdd03d49e5b2bcc38214acc41a2f1297839` | Downloaded and hashed. It contains one file, `zizmor`, and `./zizmor --version` prints `zizmor 1.29.0`. |
| `zizmor-1.29.0-py3-none-manylinux_2_28_x86_64.whl` (PyPI) | `587b99c2e1b34575c6c8565c2bfde415ca8bc0310f5589f19bc948c8dea10a20` | Read from PyPI's JSON API. |

I recommend the tarball: download it with curl, run `sha256sum --check --strict`, then `sudo install -m 0755 zizmor /usr/local/bin/zizmor`. That matches the two installs beside it. The acceptance also refuses any remaining `pipx install zizmor==`.

Before relying on the tarball digest, the fixer should cross-check it against the release's published digest, which I could not reach (see above).

The README's "Pins" and "Install" text should record the digest in the same commit:

- `README.md:590` is the pin row.
- `README.md:600` still tells people to run `uv tool install zizmor==1.29.0` with no digest.

### 3. The mutation fixture's npm packages: already done; the acceptance names the wrong file

`npm ci` from a committed lockfile is already in place:

- `.ci/test-mutation.py:527-535` (`node_modules()`) copies `run/mutation-judge/package.json` and `run/mutation-judge/package-lock.json`, then runs `npm ci --no-audit --no-fund --ignore-scripts`.
- `.ci/test-mutation.py:35` is `JUDGE_LOCK = ROOT / "run/mutation-judge/package-lock.json"`.
- `run/mutation.py:247` refuses an install whose Stryker differs from that lock.

The acceptance instead requires `test -f .ci/fixtures/mutation/package-lock.json`. That file does not exist, and the fixture keeps no manifest there on purpose. `.ci/fixtures/mutation/build.sh:10-11` says: "The manifests are written here rather than kept beside the sources, so this repository holds no package manifest for the dependency graph to read."

Committing a lockfile at that path to satisfy the test would go against that design and add a second lock that nothing runs `npm ci` from. As written, the acceptance can only pass if the fixer does something that has no effect, or something harmful.

The other half of that acceptance clause already passes:

```
$ python3 .ci/test-mutation.py
test-mutation (stub): 31 of 31 cases passed
```

## Recommendation: re-file as a fix row with a corrected acceptance

Re-file the fix rather than accept a residual risk. Parts 1 and 2 are small, have a known fix, and change no behaviour CI relies on. I found no residual worth accepting.

Proposed row for fac finding triage (findings sr-18 and sr2-15):

- **text:** Harden gates' own CI installs. Set `persist-credentials: false` on every `actions/checkout` in `.github/workflows/ci.yml` (six uses, at lines 42, 110, 119, 147, 174 and 204 on `cc7e3c4`). Replace `pipx install zizmor==1.29.0` with the v1.29.0 release tarball `zizmor-x86_64-unknown-linux-gnu.tar.gz`, verified by `sha256sum --check --strict` (`dd96df044a6e8538d5f423790f453bdd03d49e5b2bcc38214acc41a2f1297839`; cross-check against the release's published digest first), the same way actionlint and osv-scanner are installed. Record the digest in README.md's pins and install text. The mutation judge already installs with `npm ci` from `run/mutation-judge/package-lock.json`, so leave it as it is.
- **acceptance:**
  ```
  [ "$(grep -c 'actions/checkout@' .github/workflows/ci.yml)" = "$(grep -c 'persist-cred[a-z]*: false' .github/workflows/ci.yml)" ] && ! grep -q 'pipx install zizmor==' .github/workflows/ci.yml && grep -q 'sha256sum --check' <(grep -A3 'zizmor-x86_64-unknown-linux-gnu' .github/workflows/ci.yml) && test -f run/mutation-judge/package-lock.json && grep -q 'npm", "ci"' .ci/test-mutation.py && python3 .ci/test-mutation.py
  ```

The only change from the original regression test is the lockfile clause. The original `test -f .ci/fixtures/mutation/package-lock.json` becomes a check on the lock that is actually used, plus a check that `npm ci` installs from it. I also added a check that the zizmor download is verified.

If the captain wants the original acceptance kept word for word, it can pass only by committing an unused lockfile under `.ci/fixtures/mutation/`. That is the captain's call. I recommend against it, for the reason in part 3.

The launch failure (`signal: killed` before a session was reported) happened twice. It is a fac/launcher problem, separate from this finding, and should be looked at before the row is dispatched again.
