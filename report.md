# Scout report: gitleaks auto-install does nothing on a macOS arm64 worker

Task: `conflict-gates-120-followup-d9848b6d-followup-d6ddd112`. Base: `main` at `cc7e3c4`. This is a report only. Nothing in `run/` or `.ci/` changed on this branch.

## What I could not do

- **No macOS host.** This session ran on `Linux x86_64` (`uname -sm`). I could not run `.ci/check.sh gates-proof` on a real Darwin arm64 worker. Instead I faked `uname`, downloaded the real darwin release, checked its digest and inspected the binary with `file`. I never executed the Mach-O binary.
- **`8201e67` is not in this repository.** `git cat-file -t 8201e67` gives `Not a valid object name`. The commit the goal describes is in this repository as `71a0038` on `fac-remote/conflict-gates-120-followup-d9848b6d`, titled "fix(check): install gitleaks for gates-proof on a Linux worker". It has not been merged to `main`: on `main`, `check_gates-proof` still starts with a plain `need gitleaks` (`.ci/check.sh:220`). `8201e67` is presumably that change's id in another repository.
- **One test run polluted this container (now cleaned up).** I ran the prototype once without the install-dir override, and as root it put a Mach-O binary at this container's `/usr/local/bin/gitleaks`. I deleted it. The repository was not affected.

## What I found

### 1. The installer handles only Linux x64

`run/install-gitleaks.sh` on `main`:

| line | content | effect on Darwin arm64 |
|---|---|---|
| 7 | `GITLEAKS_SHA256="551f6fc8…70eb"` | one digest, for the linux_x64 tarball only |
| 9 | `url=…/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz` | always downloads the Linux ELF binary |
| 14 | `sha256sum --check --strict` | macOS has `shasum -a 256`; GNU `sha256sum` is not guaranteed to exist |
| 16 | `sudo install -m 0755 gitleaks /usr/local/bin/gitleaks` | needs sudo, which prompts or fails on an unattended worker. `/usr/local/bin` is not Homebrew's prefix on Apple Silicon (`/opt/homebrew/bin`) and may not exist |

If the script did run on a Mac, it would put a Linux ELF binary in `/usr/local/bin`. That binary fails with "exec format error", which is worse than the clear `missing tool` message.

### 2. The auto-install in `71a0038` is gated to Linux x86_64 only

```sh
if ! command -v gitleaks >/dev/null 2>&1 && [ "$(uname -sm)" = "Linux x86_64" ]; then
    bash -e run/install-gitleaks.sh
fi
need gitleaks 'brew install gitleaks, or run/install-gitleaks.sh on Linux x86_64' || return 1
```

On a Mac, `uname -sm` prints `Darwin arm64`, so the install never runs and `need` stops the suite with `missing tool: gitleaks`. That is exactly what the goal reports. The gate is correct for the current script, because it guards against installing the wrong binary. What is missing is a darwin path in the script. The same gate also skips Linux aarch64 (`uname -sm` prints `Linux aarch64`).

Even on Linux x86_64, the auto-install goes through line 16's `sudo`. On a worker without passwordless sudo it prompts or fails, though `need` still names the tool afterwards.

### 3. The upstream release has every asset we need, and our pin matches

I downloaded `gitleaks_8.30.1_checksums.txt` from the v8.30.1 release:

```
b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5  gitleaks_8.30.1_darwin_arm64.tar.gz
dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709  gitleaks_8.30.1_darwin_x64.tar.gz
e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080  gitleaks_8.30.1_linux_arm64.tar.gz
551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb  gitleaks_8.30.1_linux_x64.tar.gz
```

- The linux_x64 line matches the pinned digest at `run/install-gitleaks.sh:7`.
- I downloaded the darwin_arm64 tarball and hashed it locally. The hash matched its line above.
- The tarball contains `LICENSE`, `README.md` and `gitleaks`. The extracted `gitleaks` is `Mach-O 64-bit arm64 executable`, so the script's `tar -xzf gitleaks.tar.gz gitleaks` works unchanged.

### 4. What a fix touches

- **The regression test:** `.ci/test-install-gitleaks.sh:84-90` rewrites the script with `sed`, anchored on a single line `^url="https://github.com/gitleaks/gitleaks/releases/download/.*"$` and a single line `^GITLEAKS_SHA256="[0-9a-f]*"$`. If it can't find them it exits 2 ("the URL or digest line … moved"). Its stub `sudo` (lines 31-39) only remaps `/usr/local/bin/gitleaks`. A per-platform script breaks both, and the test must change in the same commit.
- **Unaffected:**
  - `.ci/gate-registry.toml:2174` lists `run/install-gitleaks.sh` under `[[exempt]]` as an installer, so no new registry row is needed.
  - CI calls the script only on `ubuntu-24.04` runners (`.github/workflows/ci.yml:126,151`, `shared-secret-scan.yml:24,69`), so those runs are unchanged as long as Linux x86_64 still selects the same URL and digest.
- **Callers:** the script is gates content, so the change reaches locked callers through a lock bump (AGENTS.md). No `.gitleaks.toml` or hook file changes, so no `sync.sh` run is needed.
- **Other tools:** `check_gates-proof` also `need`s exiftool, yq, jq and git-lfs (`.ci/check.sh:221-225`). On a bare Mac those still stop the suite, each with a `brew install` hint. Fixing gitleaks alone moves the stop to the next missing tool unless the worker already has those four.

## Prototype and evidence

I wrote a prototype in the session scratchpad (not committed). It:
- picks the asset and its digest with a `case` on `uname -s`/`uname -m`. It uses no associative arrays, so it still works under macOS's `/bin/bash` 3.2.
- refuses an unknown platform by name.
- uses `sha256sum` when it exists and falls back to `shasum -a 256`.
- installs into `${GITLEAKS_INSTALL_DIR:-/usr/local/bin}`, without sudo when the directory is writable.

I ran it once per platform with a stub `uname` and the real release downloads:

```
== Darwin arm64   gitleaks.tar.gz: OK   Mach-O 64-bit arm64 executable
== Darwin x86_64  gitleaks.tar.gz: OK   Mach-O 64-bit x86_64 executable
== Linux aarch64  gitleaks.tar.gz: OK   ELF 64-bit LSB executable, ARM aarch64
== Linux x86_64   gitleaks.tar.gz: OK   ELF 64-bit LSB executable, x86-64
== FreeBSD amd64  install-gitleaks: no pinned release for FreeBSD amd64
```

I also ran it on a PATH with only `shasum` and no `sha256sum`, as on macOS, with stub uname `Darwin arm64`:

```
gitleaks.tar.gz: OK
rc=0
…/out/gitleaks: Mach-O 64-bit arm64 executable
```

With a corrupted digest it refused and installed nothing:

```
gitleaks.tar.gz: FAILED
shasum: WARNING: 1 computed checksum did NOT match
rc=1 exists=no
```

I also ran the existing `.ci/test-install-gitleaks.sh` against the prototype. It failed as expected, which confirms the test must change with the script:

```
the URL or digest line of run/install-gitleaks.sh moved; update this test
test rc=2
```

## Recommendation

1. **Make `run/install-gitleaks.sh` platform-aware:**
   - select `asset` and `GITLEAKS_SHA256` from `uname -s`/`uname -m`, covering `Linux x86_64`, `Linux aarch64`, `Darwin arm64` and `Darwin x86_64`, with the four digests above. Refuse any other platform naming `uname -sm`.
   - build `url` from `${asset}` on one line, so the test's sed anchor survives.
   - verify with `sha256sum` when present, otherwise `shasum -a 256` (both with `--check --strict`).
   - install into `${GITLEAKS_INSTALL_DIR:-/usr/local/bin}`, using `sudo` only when that directory is not writable.

   Keep the Linux x86_64 version, URL and digest byte-identical, so CI and locked callers see no behaviour change.
2. **In `.ci/check.sh` `check_gates-proof`** (rebased onto `71a0038`, or replacing it): drop the `Linux x86_64` gate. When `gitleaks` is missing, run the installer with `GITLEAKS_INSTALL_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/gates/bin"` and prepend that directory to `PATH` for the suite. That needs no sudo on any worker and leaves `~/.gitconfig` and `~/.git-hooks` alone. If the install fails, for example on an unsupported platform, the existing `need gitleaks` still refuses naming the tool. Update the remedy text to `brew install gitleaks, or run/install-gitleaks.sh`.
3. **Update `.ci/test-install-gitleaks.sh` in the same commit:**
   - point a stub `uname` at a fixed platform, or replace the `case`'s selected digest through an override variable.
   - install via `GITLEAKS_INSTALL_DIR="$workdir/dest"` instead of the stub `sudo`.
   - add cases for:
     - an unsupported `uname` being refused;
     - the `shasum` fallback, with `sha256sum` hidden from PATH;
     - Linux x86_64 still selecting the `linux_x64` URL and pinned digest.
   - run it under `bash -e`, as AGENTS.md requires.
4. **Validate on the real macOS arm64 worker:** with gitleaks absent, run `.ci/check.sh gates-proof` and confirm it gets past `missing tool: gitleaks`. Exiftool, yq, jq and git-lfs still need `brew install` there.
