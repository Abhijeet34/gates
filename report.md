# artifact-scan: unlistable directories, non-regular files and symlinked arguments

## What I could not do

- The contract says to apply "the report's diff", but that earlier report was not in this repository or in the task text. Issue #20 on gates is the unrelated `gates bump` pull request. So I wrote the diff below myself, working from the goal's three behaviours. Before applying it, check it against the original report if that report exists.
- This was a scout run, so the change is not on the branch. Its full diff is in the last section of this report, ready to apply with `git apply` on `main` at `b1067fc`. I opened no pull request.
- I did not run CI. I ran locally the two things the acceptance names (`test-artifact-scan.sh` and `gates-proof`), as a non-root user with a real home path and hostname, which is how a GitHub runner sees them. Details are below.

## What I found (on `main` at b1067fc)

`system-maintenance/git-hooks/artifact-scan.py` has three ways to let an artifact pass without reading it, or to never finish:

1. **A directory it cannot list passes.** `files_under` (artifact-scan.py:68) calls `os.walk` with no `onerror`. `os.walk` silently skips a directory whose listing fails, so every file inside goes unread, and the scan exits 0. Measured: a bundle whose `inner/` is mode 000, with the home path planted in `inner/main`, gives `expected exit 2, got 0`.
2. **A FIFO, socket or device is opened as a file.** `read_artifact` (artifact-scan.py:79) calls `open(path, "rb").read()` on anything that is not a symlink. On a FIFO that call blocks forever. Measured: the new test hung on the old scanner and was killed by `timeout` with exit 124. On a device file, the read has no end.
3. **A symlinked argument is not followed.** `files_under` walks only `isdir(path) and not islink(path)`, and `read_artifact` reads a symlink's target text only. So `artifact-scan.py MyApp.app`, where `MyApp.app` is a link to a build output, scans just the link's path string and passes. A symlinked binary argument also passes the same way.

The module docstring (artifact-scan.py:1-16) and README.md, "Release artifacts" (system-maintenance/git-hooks/README.md:353,356), describe exit 2 only for a "missing, unreadable or absent argument". They do not cover any of the three cases above.

## The change

- **`artifact-scan.py`**
  - **Arguments are followed.** An argument that resolves to a directory is walked. Any other argument is `stat`ed through the link and its content is read.
  - **Symlinks inside a walk are unchanged.** They are still not followed: their target text is scanned, and a linked directory is never descended. The existing case-6 assertions still pass.
  - **An unlistable directory exits 2.** `os.walk(..., onerror=_walk_error)` raises a new `Unreadable` error, which `main` turns into exit 2. The path in the message is redacted.
  - **A non-regular file exits 2 and is never opened.** Any path that is not a regular file and not a walked symlink (a FIFO, socket or device) raises `Unreadable("not a regular file")` before `open`, and exits 2.
  - **Dangling or looping links exit 2.** `stat`/`open` errors become `Unreadable`, so a dangling or looping symlinked argument also exits 2.
  - **Docs.** The docstring now states all of the above.
- **README.md, "Release artifacts"**
  - States that an argument is followed, so a symlinked bundle is walked and a symlinked binary is read.
  - States that a symlink inside a walk has its target text read and is not followed.
  - Adds to the exit-2 list "a directory it cannot list, or a FIFO, socket or device (which it never opens)".
  - The test line now mentions the symlinked argument.
- **`test-artifact-scan.sh`**: six new assertions, which takes it from 26 to 32 under a non-root user:
  - a directory that cannot be listed → 2 (non-root only, as the existing unreadable-file case is)
  - a FIFO argument → 2
  - a FIFO inside a bundle → 2
  - a symlinked bundle argument → 1
  - its nested binary is reported under the link's path
  - a symlinked binary argument → 1

  The FIFO cases run with a background writer on the FIFO. That way a regressed scanner that opens the FIFO reads EOF and fails the assertion instead of hanging the suite.
- **No registry change.** The `artifact-scan` row in `.ci/gate-registry.toml` (its red, clean, run and finding) is unchanged and still proves the gate.

## Evidence

All runs were in a clone of the branch, as user `scantest` (home `/home/scantester`), with `unshare -u` setting the hostname to `scanbox-host`:

```
$ bash system-maintenance/git-hooks/test-artifact-scan.sh
ok 32 assertion(s)

$ bash .ci/check.sh gates-proof
ok   artifact-scan
gates-proof: 120 gates registered, 360 cases executed: each red input refused naming its finding, each clean input passed, each neutered gate let its red input through
gates-proof regression tests passed
all checks passed
```

Red first: the new test run against `main`'s scanner (`ARTIFACT_SCAN=<old artifact-scan.py>`) with the FIFO writer in place:

```
FAIL a directory that cannot be listed is a usage error, not a pass (expected exit 2, got 0)
FAIL a FIFO argument is a usage error, not a pass (expected exit 2, got 0)
FAIL a FIFO inside a bundle is a usage error, not a pass (expected exit 2, got 0)
FAIL a symlinked bundle argument is walked and refused (expected exit 1, got 0)
FAIL the walked bundle's nested binary is reported (no 'machine\.home-path +1 .*app-link/Contents/MacOS/main' in the output)
FAIL a symlinked binary argument is read and refused (expected exit 1, got 0)
6 of 32 assertion(s) failed
```

All 26 existing assertions still pass. Lint at the pinned version: `uvx ruff@0.16.0 check .` prints "All checks passed!", and `format --check .` prints "190 files already formatted". shellcheck is not installed in this container, so I did not run it.

Sharp edges I noticed:

- **The suite can't prove the gate as root.** `gates-proof`'s `artifact-scan` row fails as root in this container, on `main` too ("red input was not refused"). Root's home `/root` is shorter than the 6-character needle minimum (exposure-scan.py:158), and the hostname `vm` is shorter than 4. This is a property of the environment, not of the change. A runner's home (`/home/runner`) passes.
- **`bash -e` stops the test early.** `test-artifact-scan.sh` cannot run under `bash -e`: `scan` returns non-zero on purpose. That is also true on `main`. `.ci/check.sh:164` runs it with plain `bash`, which is correct. The AGENTS.md `bash -e` rule covers only `run/` scripts.

## Recommendation

Apply the diff below in a gates pull request; it is complete, linted and verified as described above. Then check that CI's `shell` suite prints `ok 32 assertion(s)` and that `gates-proof` passes. Before merging, compare it with the original report, which I could not find.

## The diff

```diff
diff --git a/system-maintenance/git-hooks/README.md b/system-maintenance/git-hooks/README.md
index 92dbe71..8de7b39 100644
--- a/system-maintenance/git-hooks/README.md
+++ b/system-maintenance/git-hooks/README.md
@@ -350,16 +350,17 @@ The push gate reads no binary, and a compiled release carries what it was built
 Measured 2026-10-20 with `artifact-scan.py` over quoth's last local release build: 1131 `machine.home-path` hits in the app binary and 22 in the `whisper-server` sidecar.
 
 `artifact-scan.py <file-or-directory>...` runs the same derived needles over every byte of each artifact, so there is one needle source and it cannot drift from the push gate.
-A directory is walked, which covers a `.app` bundle, and a symlink's target text is read too.
+A directory is walked, which covers a `.app` bundle; a symlink inside it is not followed, and its target text is read instead.
+An argument is followed, so a symlinked bundle is walked and a symlinked binary is read, not just the link's own text.
 Each needle is recompiled as a byte pattern, so a path followed by a stray high byte is still a hit.
 It prints `class  count  path` per hit and never the value; a path that itself holds a needle is redacted.
-It exits 0 when clean, 1 on any `machine.*` hit, and 2 for a missing, unreadable or absent argument, so an artifact that was not read never passes.
+It exits 0 when clean, 1 on any `machine.*` hit, and 2 for a missing, unreadable or absent argument, a directory it cannot list, or a FIFO, socket or device (which it never opens), so an artifact that was not read never passes.
 It is release tooling and not a hook: `install.sh` does not deploy it, and a release script calls it from a checkout of this repository before it signs or notarises.
 A compressed disk image is not meaningfully scanned, so scan the files that go into it.
 
 The fix belongs at the build, with `--remap-path-prefix` (rustc) or `-ffile-prefix-map` (clang); `trim-paths` in a Cargo profile is the better tool once it is stable.
 It was not on cargo 1.99.0, measured 2026-10-20: `feature trim-paths is required`.
-`test-artifact-scan.sh` plants this machine's values into random bytes at run time and drives every class, the directory walk and each exit code.
+`test-artifact-scan.sh` plants this machine's values into random bytes at run time and drives every class, the directory walk, a symlinked argument and each exit code.
 
 ## This is an accident-catcher, not a control
 
diff --git a/system-maintenance/git-hooks/artifact-scan.py b/system-maintenance/git-hooks/artifact-scan.py
index 1609612..7839bb5 100755
--- a/system-maintenance/git-hooks/artifact-scan.py
+++ b/system-maintenance/git-hooks/artifact-scan.py
@@ -4,20 +4,25 @@
     artifact-scan.py <file-or-directory>...
 
 Runs exposure-scan.py's derived needles over every byte of each artifact,
-binaries included, which the push gate cannot read. A directory (a .app
-bundle) is walked, and a symlink's target text is scanned as well. Prints
+binaries included, which the push gate cannot read. An argument is followed,
+so a symlinked argument is read or walked as what it points at. A directory
+(a .app bundle) is walked, and a symlink inside it has its target text
+scanned rather than followed. Prints
 `class  count  path` per hit and never the matched value; a path that itself
 holds a needle is redacted. system-maintenance/git-hooks/README.md, "Release
 artifacts", owns the design.
 
 Exit 0 clean, 1 on any machine.* hit or when the needles cannot be derived,
-2 for a bad argument or an unreadable path: an artifact that was not read
-never passes.
+2 for a bad argument, an unreadable file, a directory that cannot be listed
+or a path that is neither a regular file, a directory nor a symlink inside a
+walked directory (a FIFO, socket or device, which is never opened): an
+artifact that was not read never passes.
 """
 
 import importlib.util
 import os
 import re
+import stat
 import sys
 
 HERE = os.path.dirname(os.path.abspath(__file__))
@@ -67,23 +72,53 @@ def count_by_class(needles, data):
     return counts
 
 
+class Unreadable(Exception):
+    """A path the scan cannot read, so the artifact cannot pass."""
+
+    def __init__(self, path, reason):
+        super().__init__(reason)
+        self.path, self.reason = path, reason
+
+
+def _walk_error(exc):
+    # os.walk skips a directory it cannot list unless told otherwise, which
+    # would pass every file inside it unread.
+    raise Unreadable(exc.filename, "cannot list: %s" % exc.strerror)
+
+
 def files_under(path):
-    """Every file under path in a stable order, or the path itself."""
-    if os.path.isdir(path) and not os.path.islink(path):
-        for root, dirs, names in os.walk(path):
+    """(path, follow) for every file under an argument, in a stable order.
+
+    The argument itself is followed, so a symlinked bundle is walked and a
+    symlinked binary is read. Inside a walk a symlink is not followed: its
+    target text is read, and a linked directory is never descended.
+    """
+    if os.path.isdir(path):
+        for root, dirs, names in os.walk(path, onerror=_walk_error):
             dirs.sort()
             linked = [d for d in dirs if os.path.islink(os.path.join(root, d))]
             for name in sorted(names + linked):
-                yield os.path.join(root, name)
+                yield os.path.join(root, name), False
     else:
-        yield path
+        yield path, True
 
 
-def read_artifact(path):
-    if os.path.islink(path):
+def read_artifact(path, follow):
+    """The bytes to scan: a regular file's content, or a walked link's target text."""
+    try:
+        mode = (os.stat if follow else os.lstat)(path).st_mode
+    except OSError as exc:
+        raise Unreadable(path, "cannot read: %s" % exc.strerror) from exc
+    if stat.S_ISLNK(mode):
         return os.fsencode(os.readlink(path))
-    with open(path, "rb") as fh:
-        return fh.read()
+    if not stat.S_ISREG(mode):
+        # A FIFO would block the open and a device has no end; neither is a release file.
+        raise Unreadable(path, "not a regular file")
+    try:
+        with open(path, "rb") as fh:
+            return fh.read()
+    except OSError as exc:
+        raise Unreadable(path, "cannot read: %s" % exc.strerror) from exc
 
 
 def main(argv):
@@ -97,20 +132,21 @@ def main(argv):
     needles = byte_needles()
     text_needles = exposure.derive_needles()
     scanned = hits = 0
-    for arg in argv:
-        for path in files_under(arg):
-            try:
-                data = read_artifact(path)
-            except OSError as exc:
-                sys.stderr.write(
-                    "%s: cannot read %s: %s\n"
-                    % (TOOL, exposure.redact(text_needles, path), exc.strerror)
-                )
-                return 2
-            scanned += 1
-            for rule, n in sorted(count_by_class(needles, data).items()):
-                hits += n
-                print("%-22s %7d  %s" % (rule, n, exposure.redact(text_needles, path)))
+    try:
+        for arg in argv:
+            for path, follow in files_under(arg):
+                data = read_artifact(path, follow)
+                scanned += 1
+                for rule, n in sorted(count_by_class(needles, data).items()):
+                    hits += n
+                    print(
+                        "%-22s %7d  %s" % (rule, n, exposure.redact(text_needles, path))
+                    )
+    except Unreadable as exc:
+        sys.stderr.write(
+            "%s: %s: %s\n" % (TOOL, exposure.redact(text_needles, exc.path), exc.reason)
+        )
+        return 2
     if hits:
         sys.stderr.write(
             "%s: REFUSED, %d machine identifier(s) in %d file(s) scanned. "
diff --git a/system-maintenance/git-hooks/test-artifact-scan.sh b/system-maintenance/git-hooks/test-artifact-scan.sh
index 2043d59..154054a 100755
--- a/system-maintenance/git-hooks/test-artifact-scan.sh
+++ b/system-maintenance/git-hooks/test-artifact-scan.sh
@@ -135,7 +135,47 @@ if [ "$(id -u)" -ne 0 ]; then
     chmod 600 "$TMP/locked.bin"
 fi
 
-# 9. One clean artifact beside a bad one does not dilute the verdict.
+# A directory that cannot be listed would otherwise pass every file inside it unread.
+if [ "$(id -u)" -ne 0 ]; then
+    mkdir -p "$TMP/sealed/inner"
+    binary "$TMP/sealed/inner/main" "$HOME_PATH"
+    chmod 000 "$TMP/sealed/inner"
+    scan "$TMP/sealed"
+    expect_rc "a directory that cannot be listed is a usage error, not a pass" 2 $?
+    chmod 700 "$TMP/sealed/inner"
+fi
+# A FIFO is never opened: it would block the scan, and it is no release file.
+# A background writer stands by so a scanner that does open one reads an empty
+# file and fails the assertion, rather than hanging the suite.
+scan_fifo() { # fifo scan-arg
+    : > "$1" &
+    local writer=$! rc
+    scan "$2"
+    rc=$?
+    kill "$writer" 2>/dev/null
+    wait "$writer" 2>/dev/null
+    return "$rc"
+}
+mkfifo "$TMP/pipe"
+scan_fifo "$TMP/pipe" "$TMP/pipe"
+expect_rc "a FIFO argument is a usage error, not a pass" 2 $?
+mkdir -p "$TMP/piped.app"
+binary "$TMP/piped.app/main"
+mkfifo "$TMP/piped.app/pipe"
+scan_fifo "$TMP/piped.app/pipe" "$TMP/piped.app"
+expect_rc "a FIFO inside a bundle is a usage error, not a pass" 2 $?
+
+# 9. A symlinked argument is followed: a linked bundle is walked, a linked
+# binary is read, rather than only the link's own target text.
+ln -s "$TMP/app" "$TMP/app-link"
+scan "$TMP/app-link"
+expect_rc "a symlinked bundle argument is walked and refused" 1 $?
+said "the walked bundle's nested binary is reported" "machine\.home-path +1 .*app-link/Contents/MacOS/main"
+ln -s "$TMP/home.bin" "$TMP/home-link"
+scan "$TMP/home-link"
+expect_rc "a symlinked binary argument is read and refused" 1 $?
+
+# 10. One clean artifact beside a bad one does not dilute the verdict.
 scan "$TMP/clean.bin" "$TMP/home.bin"
 expect_rc "a clean artifact beside a bad one still refuses" 1 $?
 
```
