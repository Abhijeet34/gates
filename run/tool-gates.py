#!/usr/bin/env python3
"""Run a pinned external tool as a gate: a refusal is typed, never a quiet pass.

Usage: tool-gates.py zizmor|actionlint|osv|osv-stamp [root]    (default root: the current directory)

zizmor      zizmor, offline, over .github/workflows (default persona), with no
            configuration file and no inline ignore read.
actionlint  actionlint over the same files, with shellcheck and pyflakes switched
            off so the verdict does not depend on what else the machine has.
osv         OSV-Scanner over the tree against a local database, no network.
            An npm lockfile entry under node_modules/ with a version, no resolved
            URL and no link is refused as undecided: 2.7.0 skips it as local.

None of the three reads configuration from the tree under review, which a change
could otherwise add to silence the finding that gates it. osv and actionlint are
handed a file under policy/ of this repository, and zizmor is handed none. Any
ignore belongs in that file, which security design 7.2's surface protects.
osv-stamp   Print today's UTC date and the size and mtime of each database file, as
            the runner's cache-key probe: a verdict is reused only within a day
            and only against the database it was judged on, so a pass cannot
            outlive the database going stale.

Exit status: 0 pass, 1 the tool found something, 2 could not decide. Every exit 2
prints `tool-gates: refused (TYPE): ...`, and TYPE is one of:

missing-tool      the tool is not on PATH.
wrong-version     the tool's version is not the one pinned in PINS.
missing-database  osv: the database directory is unset, holds no all.zip, or lacks
                  the ecosystem of a lockfile in the tree.
stale-database    osv: the oldest all.zip is older than MAX_DB_AGE_DAYS.
undecided         osv: a lockfile the tool would read as clean without judging all
                  of it (see above), or one that cannot be parsed.
tool-error        the tool crashed, timed out or exited with a status that means
                  neither pass nor finding.

The pins live here and nowhere else; README.md, "The tool rows", owns the install
and database-refresh steps. Fetching the database is not this script's job: it
runs with no network, so a database it cannot find is a refusal.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PINS = {"zizmor": "1.29.0", "actionlint": "1.7.12", "osv-scanner": "2.7.0"}
DB_ENV = "OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY"
MAX_DB_AGE_DAYS = 7
TIMEOUT = 120
WORKFLOWS = ".github/workflows"
POLICY = Path(__file__).resolve().parent.parent / "policy"
# zizmor exits 10 to 14 by the highest severity it found; 1 and 2 are its own errors.
ZIZMOR_FINDINGS = range(10, 15)
NPM_LOCKS = ("package-lock.json", "npm-shrinkwrap.json")
OSV_FINDINGS, OSV_NO_PACKAGES, OSV_ERROR = 1, 128, 127


class Refused(Exception):
    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind


def tool(name):
    """Path of `name` once its version is the pinned one."""
    path = shutil.which(name)
    if path is None:
        raise Refused(
            "missing-tool",
            f"{name} is not on PATH (pinned {PINS[name]}; install steps in README.md)",
        )
    rc, out = run([path, "--version"], None)
    found = re.search(r"\d+\.\d+\.\d+", out.splitlines()[0] if out else "")
    if rc != 0 or not found or found.group() != PINS[name]:
        raise Refused(
            "wrong-version",
            f"{name} reports {found.group() if found else 'no version'},"
            f" the pin is {PINS[name]}",
        )
    return path


def run(argv, root):
    try:
        proc = subprocess.run(
            argv,
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise Refused("tool-error", f"{argv[0]} timed out after {TIMEOUT}s") from e
    except OSError as e:
        raise Refused("tool-error", f"{argv[0]} could not run: {e}") from e
    return proc.returncode, proc.stdout + proc.stderr


def broke(name, rc, out):
    last = out.strip().splitlines()[-1] if out.strip() else "no output"
    return Refused("tool-error", f"{name} exited {rc}: {last}")


def workflow_files(root):
    folder = Path(root) / WORKFLOWS
    return sorted(
        str(p.relative_to(root))
        for p in folder.glob("*")
        if p.suffix in (".yml", ".yaml") and p.is_file()
    )


def lint_workflows(name, argv, found_rc, root):
    exe = tool(name)
    files = workflow_files(root)
    if not files:
        print(f"tool-gates: no workflow files under {WORKFLOWS}, nothing to check")
        return 0
    rc, out = run([exe, *argv(files)], root)
    if rc == 0:
        print(
            f"tool-gates: {name} {PINS[name]}: {len(files)} workflow file(s), no findings"
        )
        return 0
    if rc in found_rc:
        print(out.rstrip())
        print(f"tool-gates: {name} refused the workflows (exit {rc})")
        return 1
    raise broke(name, rc, out)


def zizmor(root):
    return lint_workflows(
        "zizmor",
        lambda files: [
            "--offline",
            "--no-progress",
            "--no-config",
            "--no-ignores",
            WORKFLOWS,
        ],
        ZIZMOR_FINDINGS,
        root,
    )


def actionlint(root):
    return lint_workflows(
        "actionlint",
        lambda files: [
            "-shellcheck=",
            "-pyflakes=",
            "-no-color",
            "-config-file",
            str(POLICY / "actionlint.yaml"),
            *files,
        ],
        (1,),
        root,
    )


def database_files():
    folder = os.environ.get(DB_ENV)
    if not folder:
        raise Refused(
            "missing-database",
            f"{DB_ENV} is not set (refresh steps in README.md)",
        )
    zips = sorted(Path(folder).rglob("all.zip"))
    if not zips:
        raise Refused("missing-database", f"{folder} holds no all.zip")
    return zips


def database():
    """The number of database files, once the oldest is fresh enough."""
    zips = database_files()
    oldest = min(zips, key=lambda p: p.stat().st_mtime)
    age = (time.time() - oldest.stat().st_mtime) / 86400
    if age > MAX_DB_AGE_DAYS:
        raise Refused(
            "stale-database",
            f"{oldest} is {age:.1f} days old, the limit is {MAX_DB_AGE_DAYS}",
        )
    return len(zips)


def undecided_npm_entries(root):
    """Each `lockfile: entry` osv-scanner 2.7.0 would skip as local, or could not be read."""
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in NPM_LOCKS:
            if name not in files:
                continue
            path = Path(folder) / name
            rel = path.relative_to(root)
            try:
                entries = list(json.loads(path.read_text()).get("packages", {}).items())
            except (OSError, ValueError, AttributeError) as e:
                # A lockfile this cannot read is one the tool may skip, so it is not a pass.
                found.append(f"{rel}: not readable as an npm lockfile ({e})")
                continue
            found += [
                f"{rel}: {key} has a version and no resolved URL"
                for key, entry in entries
                if "node_modules/" in key
                and isinstance(entry, dict)
                and "version" in entry
                and not entry.get("resolved")
                and not entry.get("link")
            ]
    return found


def osv(root):
    exe = tool("osv-scanner")
    count = database()
    undecided = undecided_npm_entries(root)
    if undecided:
        raise Refused(
            "undecided",
            f"osv-scanner {PINS['osv-scanner']} would not judge these, so the tree"
            f" would read clean unjudged: {'; '.join(undecided)}",
        )
    # --offline forbids the network; the second flag names the source of the verdict;
    # --config stops the tree under review from supplying the tool's configuration.
    rc, out = run(
        [
            exe,
            "scan",
            "source",
            "--config",
            str(POLICY / "osv-scanner.toml"),
            "--offline",
            "--offline-vulnerabilities",
            "-r",
            ".",
        ],
        root,
    )
    if rc == 0:
        print(
            f"tool-gates: osv-scanner {PINS['osv-scanner']}: no known vulnerabilities ({count} database file(s))"
        )
        return 0
    if rc == OSV_NO_PACKAGES:
        print("tool-gates: no package source in the tree, nothing to check")
        return 0
    if rc == OSV_FINDINGS:
        print(out.rstrip())
        print("tool-gates: osv-scanner refused the tree: known vulnerabilities")
        return 1
    if rc == OSV_ERROR and re.search(r"no offline version|could not load db", out):
        raise Refused(
            "missing-database", "the database lacks an ecosystem this tree uses"
        )
    raise broke("osv-scanner", rc, out)


def osv_stamp(root):
    paths = database_files()
    print(time.strftime("%Y-%m-%d", time.gmtime()))
    for path in paths:
        info = path.stat()
        print(path, info.st_size, int(info.st_mtime))
    return 0


CHECKS = {
    "zizmor": zizmor,
    "actionlint": actionlint,
    "osv": osv,
    "osv-stamp": osv_stamp,
}


def main(argv):
    if not 2 <= len(argv) <= 3 or argv[1] not in CHECKS:
        print(f"Usage: tool-gates.py {'|'.join(CHECKS)} [root]", file=sys.stderr)
        return 2
    try:
        return CHECKS[argv[1]](argv[2] if len(argv) == 3 else ".")
    except Refused as e:
        print(f"tool-gates: refused ({e.kind}): {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
