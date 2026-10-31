#!/usr/bin/env python3
"""Regression tests for run/licence.py: the licence policy row.

    .ci/test-licence.py

Each case builds a git repository with a policy, lockfiles and vendored files,
runs the row on it and returns why it failed, or None. Every case must pass
against run/licence.py, then fail against a copy with the one control it exists
for removed, so a case that would pass without its control is refused. The last
case runs the row over the head tree of every case in corpus/licence, the seeded
corpus the licence review is measured on.
"""

import datetime
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENCE = ROOT / "run/licence.py"
RUNNER = ROOT / "run/gate.py"
CORPUS = ROOT / "corpus/licence"
# A sandbox can deny exec on the interpreter's symlinked path while allowing
# the one on PATH, so the children run whichever of the two may be executed.
PYTHON = (
    sys.executable
    if os.access(sys.executable, os.X_OK)
    else shutil.which("python3") or sys.executable
)
workdir = Path(tempfile.mkdtemp())


def real_git_path():
    """PATH with the real git first, where `git` is macOS's xcrun shim.

    The shim caches its lookup under TMPDIR, and every run of the row gets a fresh
    TMPDIR, so each `git ls-files` it made would spend seconds in xcodebuild.
    """
    path = os.environ.get("PATH", "")
    if not shutil.which("xcrun"):
        return path
    found = subprocess.run(["xcrun", "--find", "git"], capture_output=True, text=True)
    if found.returncode or not os.access(found.stdout.strip(), os.X_OK):
        return path
    bin_dir = workdir / "bin"
    bin_dir.mkdir()
    # A wrapper, not a symlink: git finds its templates relative to the path it ran as.
    (bin_dir / "git").write_text(
        f'#!/bin/sh\nexec {shlex.quote(found.stdout.strip())} "$@"\n'
    )
    (bin_dir / "git").chmod(0o755)
    return f"{bin_dir}{os.pathsep}{path}"


ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PATH": real_git_path(),
}
POLICY = """allowed = ["MIT", "Apache-2.0", "ISC"]
review = ["MPL-2.0"]
refused = ["GPL-3.0-only", "AGPL-3.0-only"]
vendored = ["vendor/**"]
"""


def lock(**licences):
    """A lockfileVersion 3 package-lock.json, one dependency per keyword."""
    packages = {"": {"name": "app", "version": "1.0.0", "license": "UNLICENSED"}}
    for name, lic in licences.items():
        packages[f"node_modules/{name}"] = {"version": "1.0.0"}
        if lic is not None:
            packages[f"node_modules/{name}"]["license"] = lic
    return json.dumps({"name": "app", "lockfileVersion": 3, "packages": packages})


def uv_lock(*names):
    rows = [
        'version = 1\n[[package]]\nname = "app"\nversion = "0.1.0"\nsource = { editable = "." }'
    ]
    rows += [
        f'[[package]]\nname = "{n}"\nversion = "1.0"\nsource = {{ registry = "https://pypi.org/simple" }}'
        for n in names
    ]
    return "\n".join(rows) + "\n"


def crates(*rows, optional=()):
    """A Cargo project whose crates.io dependencies are vendored, as `cargo vendor` leaves them.

    Each row is (name, version, licence); `optional` names rows behind a feature.
    A directory source keeps `cargo metadata --offline` off the network and the registry cache.
    """
    deps = "".join(
        f'{n} = {{ version = "{v}", optional = true }}\n'
        if n in optional
        else f'{n} = "{v}"\n'
        for n, v, _ in rows
    )
    features = (
        f"\n[features]\nextra = {[f'dep:{n}' for n in optional]}\n".replace("'", '"')
        if optional
        else ""
    )
    files = {
        "app/Cargo.toml": f'[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n{deps}{features}',
        "app/src/main.rs": "fn main() {}\n",
        "app/.cargo/config.toml": '[source.crates-io]\nreplace-with = "v"\n[source.v]\ndirectory = "vendor"\n',
    }
    lock = [
        'version = 4\n\n[[package]]\nname = "app"\nversion = "0.1.0"\ndependencies = [\n'
    ]
    lock += [f' "{n}",\n' for n, _, _ in sorted(rows)] + ["]\n"]
    for n, v, lic in sorted(rows):
        lock.append(
            f'\n[[package]]\nname = "{n}"\nversion = "{v}"\nsource = "registry+https://github.com/rust-lang/crates.io-index"\n'
        )
        files[f"app/vendor/{n}/Cargo.toml"] = (
            f'[package]\nname = "{n}"\nversion = "{v}"\nedition = "2021"\nlicense = "{lic}"\n'
        )
        files[f"app/vendor/{n}/src/lib.rs"] = ""
        files[f"app/vendor/{n}/.cargo-checksum.json"] = '{"files": {}, "package": null}'
    files["app/Cargo.lock"] = "".join(lock)
    return files


def pnpm_tree(lockfile, **installed):
    """A pnpm project: its lockfile, and one installed package.json per keyword (`_` for `-`, `__` for a scope)."""
    files = {"web/pnpm-lock.yaml": lockfile}
    for key, (version, lic) in installed.items():
        name = ("@" if "__" in key else "") + key.replace("__", "/").replace("_", "-")
        files[
            f"web/node_modules/.pnpm/{name.replace('/', '+')}@{version}/node_modules/{name}/package.json"
        ] = json.dumps({"name": name, "version": version, "license": lic})
    return files


PNPM9 = """lockfileVersion: '9.0'

importers:

  .:
    dependencies:
      gpl-lib:
        specifier: ^1.0.0
        version: 1.0.0

packages:

  '@scope/fmt@2.0.0':
    resolution: {integrity: sha512-AAAA}

  gpl-lib@1.0.0:
    resolution: {integrity: sha512-BBBB}

snapshots:

  gpl-lib@1.0.0: {}
"""


def runner_tmp(repo):
    """A TMPDIR whose cargo configuration names the repository's vendored directory source.

    The row reads no cargo configuration from the tree, so a `cargo vendor` tree's
    own `.cargo/config.toml` is not what lets it resolve offline. Cargo reads the
    configuration of its working directory's parents, and the row works under TMPDIR.
    """
    tmp = Path(tempfile.mkdtemp(dir=workdir))
    for d in sorted(repo.rglob("vendor")):
        if any(d.glob("*/.cargo-checksum.json")):
            (tmp / ".cargo").mkdir()
            (tmp / ".cargo/config.toml").write_text(
                f'[source.crates-io]\nreplace-with = "v"\n[source.v]\ndirectory = "{d}"\n'
            )
            break
    return tmp


def run(
    script,
    files,
    policy=POLICY,
    *,
    repo=None,
    executable=(),
    links=None,
    env=None,
    track_modules=False,
):
    """(exit status, output) of the row on a repository holding `files`, every one tracked.

    `links` maps a path in the repository to a directory it is a symlink to. A file
    under node_modules is left untracked, as `pnpm install` leaves it, unless `track_modules`.
    """
    repo = repo or Path(tempfile.mkdtemp(dir=workdir))
    for rel, text in {".github/licence-policy.toml": policy, **files}.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
        if rel in executable:
            (repo / rel).chmod(0o755)
    for rel, target in (links or {}).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).symlink_to(target)
    subprocess.run(["git", "init", "-q", str(repo)], env=ENV, check=True)
    keep = [] if track_modules else [":(exclude,glob)**/node_modules/**"]
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A", "--", ".", *keep], env=ENV, check=True
    )
    proc = subprocess.run(
        [PYTHON, str(script), str(repo)],
        env={**ENV, **(env or {}), "TMPDIR": str(runner_tmp(repo))},
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def expect(what, got, status, *texts):
    rc, out = got
    if rc != status:
        return f"{what}: exit {rc}, not {status}: {out.strip()}"
    missing = [t for t in texts if t not in out]
    return f"{what}: no {missing[0]!r} in: {out.strip()}" if missing else None


def refused_dependency(script):
    got = run(script, {"package-lock.json": lock(a="MIT", gpl="GPL-3.0-only")})
    return expect(
        "a GPL-3.0-only dependency", got, 1,
        "licence: refused: package-lock.json: npm:gpl 1.0.0: GPL-3.0-only is refused",
    )  # fmt: skip


def or_takes_best_alternative(script):
    got = run(script, {"package-lock.json": lock(dual="(MIT OR GPL-3.0-only)")})
    return expect(
        "MIT OR GPL-3.0-only", got, 0, "1 dependencies and vendored files allowed"
    )


def and_takes_worst_part(script):
    got = run(script, {"package-lock.json": lock(both="MIT AND AGPL-3.0-only")})
    return expect(
        "MIT AND AGPL-3.0-only", got, 1, "npm:both 1.0.0: AGPL-3.0-only is refused"
    )


def review_needs_a_record(script):
    lockfile = {"package-lock.json": lock(mpl="MPL-2.0")}
    why = expect(
        "an unreviewed MPL-2.0", run(script, lockfile), 1,
        "licence: review: package-lock.json: npm:mpl 1.0.0: MPL-2.0 needs review;"
        " record why it is accepted under [reviewed]",
    )  # fmt: skip
    reviewed = (
        POLICY
        + '[reviewed]\n"npm:mpl" = { accepts = "MPL-2.0", reason = "decided in review: used unmodified" }\n'
    )
    return why or expect(
        "a reviewed MPL-2.0",
        run(script, lockfile, reviewed),
        0,
        "(1 by a [reviewed] entry)",
    )


def reviewed_cannot_accept_refused(script):
    policy = (
        POLICY
        + '[reviewed]\n"npm:gpl" = { accepts = "GPL-3.0-only", reason = "decided in review" }\n'
    )
    got = run(script, {"package-lock.json": lock(gpl="GPL-3.0-only")}, policy)
    return expect(
        "a reviewed GPL-3.0-only", got, 1, "npm:gpl 1.0.0: GPL-3.0-only is refused"
    )


def unlisted_licence_needs_review(script):
    got = run(script, {"package-lock.json": lock(odd="WTFPL")})
    return expect(
        "an unlisted WTFPL", got, 1, "npm:odd 1.0.0: WTFPL is in no list of the policy"
    )


def malformed_expression_needs_review(script):
    got = run(script, {"package-lock.json": lock(two="MIT GPL-3.0-only")})
    return expect(
        "two ids with no operator", got, 1,
        "'MIT GPL-3.0-only' is not an SPDX expression (GPL-3.0-only after a whole expression)",
    )  # fmt: skip


def missing_licence_needs_declaring(script):
    files = {"uv.lock": uv_lock("requests"), "package-lock.json": lock(bare=None)}
    why = expect(
        "dependencies with no licence", run(script, files), 1,
        "licence: review: uv.lock: uv:requests 1.0: no licence recorded; name it under [declared]",
        "licence: review: package-lock.json: npm:bare 1.0.0: no licence recorded",
    )  # fmt: skip
    declared = (
        POLICY + '[declared]\n"uv:requests" = "Apache-2.0"\n"npm:bare@1.0.0" = "ISC"\n'
    )
    return why or expect(
        "declared licences", run(script, files, declared), 0, "2 dependencies"
    )


def refused_crate(script):
    files = crates(
        ("gpl-crate", "1.0.0", "GPL-3.0-only"), ("legacy", "2.0.0", "MIT/Apache-2.0")
    )
    return expect(
        "a GPL-3.0-only crate", run(script, files), 1,
        "licence: refused: app/Cargo.lock: cargo:gpl-crate 1.0.0: GPL-3.0-only is refused",
        "licence: 1 refused, 0 need review",
    )  # fmt: skip


def legacy_slash_is_or(script):
    files = crates(
        ("legacy", "2.0.0", "MIT/Apache-2.0"), ("spaced", "1.0.0", "MIT / ISC")
    )
    # The project's own crate has no source, so it is skipped like an npm workspace link.
    return expect("MIT/Apache-2.0", run(script, files), 0, "2 dependencies")


def optional_crate_judged(script):
    files = crates(("gpl-crate", "1.0.0", "GPL-3.0-only"), optional=("gpl-crate",))
    return expect(
        "a GPL-3.0-only crate behind a feature", run(script, files), 1,
        "cargo:gpl-crate 1.0.0: GPL-3.0-only is refused",
    )  # fmt: skip


def stale_cargo_lock_undecided(script):
    files = crates(("mit-crate", "1.0.0", "MIT"))
    files["app/Cargo.lock"] = files["app/Cargo.lock"].replace(' "mit-crate",\n', "")
    files["app/Cargo.lock"] = files["app/Cargo.lock"].split(
        '\n\n[[package]]\nname = "mit-crate"'
    )[0]
    # The row must never rewrite a caller's lockfile, so a stale one is undecided, not resolved.
    return expect(
        "a Cargo.lock behind its Cargo.toml", run(script, files), 2,
        "licence: undecided (tool-error): app/Cargo.lock: cargo metadata exited 101",
    )  # fmt: skip


def pnpm_package_refused(script):
    files = pnpm_tree(
        PNPM9, gpl_lib=("1.0.0", "GPL-3.0-or-later"), scope__fmt=("2.0.0", "MIT")
    )
    policy = POLICY.replace('"AGPL-3.0-only"]', '"AGPL-3.0-only", "GPL-3.0-or-later"]')
    return expect(
        "a GPL-3.0-or-later pnpm package", run(script, files, policy), 1,
        "licence: refused: web/pnpm-lock.yaml: npm:gpl-lib 1.0.0: GPL-3.0-or-later is refused",
        "licence: 1 refused, 0 need review",
    )  # fmt: skip


def pnpm_uninstalled_needs_review(script):
    files = pnpm_tree(PNPM9, scope__fmt=("2.0.0", "MIT"))
    return expect(
        "a pnpm package not installed", run(script, files), 1,
        "licence: review: web/pnpm-lock.yaml: npm:gpl-lib 1.0.0: not installed, so no package.json to read",
    )  # fmt: skip


def pnpm_v6_keys_read(script):
    v6 = "lockfileVersion: '6.0'\n\npackages:\n\n  /gpl-lib@1.0.0(react@18.2.0):\n    resolution: {integrity: sha512-BBBB}\n"
    files = pnpm_tree(v6, gpl_lib=("1.0.0", "GPL-3.0-only"))
    return expect(
        "a lockfileVersion 6 key with a peer suffix", run(script, files), 1,
        "npm:gpl-lib 1.0.0: GPL-3.0-only is refused",
    )  # fmt: skip


def project_and_links_skipped(script):
    data = json.loads(lock(a="MIT"))
    data["packages"]["node_modules/local"] = {
        "link": True,
        "resolved": "packages/local",
    }
    data["packages"]["packages/local"] = {"name": "local", "version": "1.0.0"}
    files = {"package-lock.json": json.dumps(data), "uv.lock": uv_lock()}
    # The project's own UNLICENSED and the workspace link are its code, not dependencies.
    return expect(
        "the project and a workspace link", run(script, files), 0, "1 dependencies"
    )


def vendored_file_header(script):
    files = {
        "vendor/gpl.c": "/* SPDX-License-Identifier: GPL-3.0-only */\nint x;\n",
        "vendor/mit.js": "// SPDX-License-Identifier: MIT\nmodule.exports = 1;\n",
        "vendor/bare.js": "module.exports = 2;\n",
        "vendor/pad/index.js": "module.exports = 3;\n",
        "vendor/pad/LICENSE": "MIT License\n",
    }
    policy = POLICY + '[declared]\n"path:vendor/pad/**" = "MIT"\n'
    return expect(
        "vendored files", run(script, files, policy), 1,
        "licence: refused: vendor/gpl.c: vendored: GPL-3.0-only is refused",
        "licence: review: vendor/bare.js: vendored: no SPDX-License-Identifier in its first 30 lines",
        "licence: 1 refused, 1 need review",
    )  # fmt: skip


def notice_files_not_judged(script):
    files = {"vendor/pad/LICENSE": "MIT License\n", "vendor/pad/NOTICE.txt": "notice\n"}
    return expect("a vendored tree's notices", run(script, files), 0, "0 dependencies")


def policy_contradiction_undecided(script):
    policy = POLICY.replace('allowed = ["MIT"', 'allowed = ["GPL-3.0-only", "MIT"')
    got = run(script, {"package-lock.json": lock(a="MIT")}, policy)
    return expect(
        "a licence both allowed and refused", got, 2,
        "licence: undecided (bad-policy): licence-policy.toml: 'GPL-3.0-only' is in both allowed and refused",
    )  # fmt: skip


def cargo_wrapper_not_run(script):
    repo = Path(tempfile.mkdtemp(dir=workdir))
    files = crates(("mit-crate", "1.0.0", "MIT"))
    files["app/.cargo/config.toml"] += '[build]\nrustc-wrapper = "./wrapper.sh"\n'
    files["app/wrapper.sh"] = (
        '#!/bin/sh\necho "$@" >> "$(dirname "$0")/ran"\nexec "$@"\n'
    )
    got = run(script, files, repo=repo, executable=("app/wrapper.sh",))
    ran = repo / "app/ran"
    if ran.exists():
        return f"the tree's rustc wrapper ran: {ran.read_text().strip()}"
    return expect("a crate beside a rustc wrapper", got, 0, "1 dependencies")


def cargo_compiler_from_config_not_run(script):
    repo = Path(tempfile.mkdtemp(dir=workdir))
    files = crates(("mit-crate", "1.0.0", "MIT"))
    files["app/.cargo/config.toml"] += '[build]\nrustc = "./fake-rustc.sh"\n'
    files["app/fake-rustc.sh"] = (
        '#!/bin/sh\necho "$@" >> "$(dirname "$0")/ran"\nexec rustc "$@"\n'
    )
    got = run(script, files, repo=repo, executable=("app/fake-rustc.sh",))
    ran = repo / "app/ran"
    if ran.exists():
        return f"the tree's rustc ran: {ran.read_text().strip()}"
    return expect("a crate beside a configured rustc", got, 0, "1 dependencies")


def cargo_wrapper_from_environment_not_run(script):
    repo = Path(tempfile.mkdtemp(dir=workdir))
    wrapper = repo / "wrapper.sh"
    ran = repo / "ran"
    files = crates(("mit-crate", "1.0.0", "MIT"))
    files["wrapper.sh"] = f'#!/bin/sh\necho "$@" >> "{ran}"\nexec "$@"\n'
    got = run(
        script, files, repo=repo, executable=("wrapper.sh",),
        env={"RUSTC_WRAPPER": str(wrapper)},
    )  # fmt: skip
    if ran.exists():
        return (
            f"a rustc wrapper named by the environment ran: {ran.read_text().strip()}"
        )
    return expect("a crate with RUSTC_WRAPPER set", got, 0, "1 dependencies")


def pnpm_decoy_manifest_not_read(script):
    files = pnpm_tree(
        PNPM9, gpl_lib=("1.0.0", "GPL-3.0-or-later"), scope__fmt=("2.0.0", "MIT")
    )
    files["web/node_modules/decoy/package.json"] = json.dumps(
        {"name": "gpl-lib", "version": "1.0.0", "license": "MIT"}
    )
    policy = POLICY.replace('"AGPL-3.0-only"]', '"AGPL-3.0-only", "GPL-3.0-or-later"]')
    return expect(
        "a decoy package.json claiming an installed package", run(script, files, policy), 1,
        "npm:gpl-lib 1.0.0: GPL-3.0-or-later is refused",
    )  # fmt: skip


def pnpm_decoy_is_not_installed(script):
    files = pnpm_tree(PNPM9, scope__fmt=("2.0.0", "MIT"))
    files["web/node_modules/decoy/package.json"] = json.dumps(
        {"name": "gpl-lib", "version": "1.0.0", "license": "MIT"}
    )
    return expect(
        "a decoy package.json for a package not installed", run(script, files), 1,
        "npm:gpl-lib 1.0.0: not installed, so no package.json to read",
    )  # fmt: skip


def pnpm_mismatched_manifest_not_read(script):
    files = pnpm_tree(PNPM9, scope__fmt=("2.0.0", "MIT"))
    store = "web/node_modules/.pnpm/gpl-lib@1.0.0/node_modules/gpl-lib/package.json"
    files[store] = json.dumps({"name": "gpl-lib", "version": "9.9.9", "license": "MIT"})
    return expect(
        "a manifest at the derived path naming another version", run(script, files), 1,
        "npm:gpl-lib 1.0.0: not installed, so no package.json to read",
    )  # fmt: skip


def pnpm_manifest_outside_tree_not_read(script):
    outside = Path(tempfile.mkdtemp(dir=workdir))
    (outside / "node_modules/gpl-lib").mkdir(parents=True)
    (outside / "node_modules/gpl-lib/package.json").write_text(
        json.dumps({"name": "gpl-lib", "version": "1.0.0", "license": "MIT"})
    )
    files = pnpm_tree(PNPM9, scope__fmt=("2.0.0", "MIT"))
    got = run(
        script, files,
        links={"web/node_modules/.pnpm/gpl-lib@1.0.0": outside},
    )  # fmt: skip
    return expect(
        "a store entry that links out of the tree", got, 1,
        "npm:gpl-lib 1.0.0: not installed, so no package.json to read",
    )  # fmt: skip


def pnpm_peer_suffixed_directory_read(script):
    files = pnpm_tree(PNPM9, scope__fmt=("2.0.0", "MIT"))
    files["web/node_modules/.pnpm/gpl-lib@1.0.0_react@18.2.0/node_modules/gpl-lib/package.json"] = (
        json.dumps({"name": "gpl-lib", "version": "1.0.0", "license": "GPL-3.0-only"})
    )  # fmt: skip
    return expect(
        "a package installed under a peer-suffixed directory", run(script, files), 1,
        "npm:gpl-lib 1.0.0: GPL-3.0-only is refused",
    )  # fmt: skip


def tracked_installed_manifest_is_refused(script):
    files = pnpm_tree(PNPM9, gpl_lib=("1.0.0", "MIT"), scope__fmt=("2.0.0", "MIT"))
    return expect(
        "a change tracking the installed package.json it is judged by",
        run(script, files, track_modules=True), 1,
        "licence: refused: web/pnpm-lock.yaml: 2 tracked files under web/node_modules/",
    )  # fmt: skip


def lower_case_id_refused(script):
    got = run(script, {"package-lock.json": lock(gpl="gpl-3.0-only", ok="mit")})
    return expect(
        "a lower-case gpl-3.0-only beside a lower-case mit", got, 1,
        "licence: refused: package-lock.json: npm:gpl 1.0.0: gpl-3.0-only is refused",
        "licence: 1 refused, 0 need review",
    )  # fmt: skip


def lower_case_policy_id_matches(script):
    policy = POLICY.replace('"GPL-3.0-only"', '"gpl-3.0-only"')
    got = run(script, {"package-lock.json": lock(gpl="GPL-3.0-only")}, policy)
    return expect("a policy listing gpl-3.0-only", got, 1, "GPL-3.0-only is refused")


def lists_differing_in_case_contradict(script):
    policy = POLICY.replace('allowed = ["MIT"', 'allowed = ["gpl-3.0-only", "MIT"')
    got = run(script, {"package-lock.json": lock(a="MIT")}, policy)
    return expect(
        "an id allowed and refused in different case", got, 2,
        "licence: undecided (bad-policy): licence-policy.toml: 'GPL-3.0-only' is in both allowed and refused",
    )  # fmt: skip


MPL_REVIEWED = (
    POLICY.replace('review = ["MPL-2.0"]', 'review = ["MPL-2.0", "LGPL-2.1-or-later"]')
    + '[reviewed]\n"npm:mpl" = { accepts = "MPL-2.0", reason = "used unmodified" }\n'
)


def reviewed_accepts_only_its_expression(script):
    got = run(
        script, {"package-lock.json": lock(mpl="LGPL-2.1-or-later")}, MPL_REVIEWED
    )
    return expect(
        "a [reviewed] MPL-2.0 entry over a dependency now under LGPL-2.1-or-later", got, 1,
        "licence: review: package-lock.json: npm:mpl 1.0.0: LGPL-2.1-or-later needs review;"
        " [reviewed] accepts MPL-2.0, not LGPL-2.1-or-later",
    )  # fmt: skip


def reviewed_expression_is_case_folded(script):
    policy = MPL_REVIEWED.replace('accepts = "MPL-2.0"', 'accepts = "mpl-2.0"')
    got = run(script, {"package-lock.json": lock(mpl="MPL-2.0")}, policy)
    return expect(
        "a [reviewed] entry in lower case", got, 0, "(1 by a [reviewed] entry)"
    )


def reviewed_needs_a_licence_to_match(script):
    policy = POLICY + '[reviewed]\n"uv:requests" = { accepts = "MIT", reason = "r" }\n'
    got = run(script, {"uv.lock": uv_lock("requests")}, policy)
    return expect(
        "a [reviewed] entry for a dependency with no licence", got, 1,
        "uv:requests 1.0: no licence recorded; name it under [declared]",
    )  # fmt: skip


def reviewed_shape_checked(script):
    files = {"package-lock.json": lock(mpl="MPL-2.0")}
    for what, value in (
        ("a reason alone", '"decided in review"'),
        ("a table with no expression", '{ reason = "decided in review" }'),
    ):
        got = run(script, files, POLICY + f'[reviewed]\n"npm:mpl" = {value}\n')
        why = expect(
            f"a [reviewed] entry of {what}", got, 2,
            "licence: undecided (bad-policy): licence-policy.toml: [reviewed] 'npm:mpl' must be a table of accepts and reason",
        )  # fmt: skip
        if why:
            return why
    return None


def corpus_cases_agree(script):
    """The row over corpus/licence: each seeded case refused, each clean one passed."""
    wrong = []
    for case in sorted(p for p in (CORPUS / "cases").iterdir() if p.is_dir()):
        tree = Path(tempfile.mkdtemp(dir=workdir), "tree")
        shutil.copytree(CORPUS / "base", tree)
        shutil.copytree(case / "change", tree, dirs_exist_ok=True)
        files = {
            str(p.relative_to(tree)): p.read_text()
            for p in tree.rglob("*")
            if p.is_file() and p.name != "licence-policy.toml"
        }
        policy = (tree / ".github/licence-policy.toml").read_text()
        rc, out = run(script, files, policy)
        seeded = "defect" in tomllib.loads((case / "case.toml").read_text())
        if rc != (1 if seeded else 0):
            wrong.append(f"{case.name}: exit {rc}: {out.strip().splitlines()[0]}")
    return "; ".join(wrong) or None


ROW = """[[gate]]
id = "licence"
inputs = ["**/package-lock.json", "**/uv.lock", "**/pnpm-lock.yaml", "**/Cargo.lock", "vendor/**", ".github/licence-policy.toml"]
scoped = true
run = 'python3 "$GATES_DIR/run/licence.py"'
shadow_since = {since}
"""


def under_the_runner(script):
    """The README's row: a red lockfile is shadow, not a block, and an unrelated change leaves it out."""
    gates = Path(tempfile.mkdtemp(dir=workdir))
    (gates / "run").mkdir()
    shutil.copy(script, gates / "run/licence.py")
    shutil.copy(RUNNER, gates / "run/gate.py")
    since = datetime.datetime.now(datetime.UTC).date().isoformat()
    repo = Path(tempfile.mkdtemp(dir=workdir))
    git = ["git", "-C", str(repo)]
    files = {
        ".github/gates.toml": ROW.format(since=since),
        ".github/licence-policy.toml": POLICY,
        "notes.txt": "a\n",
    }
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    ident = ["-c", "user.name=fixture", "-c", "user.email=fixture@invalid"]
    subprocess.run(["git", "init", "-q", str(repo)], env=ENV, check=True)
    subprocess.run([*git, "add", "-A"], env=ENV, check=True)
    subprocess.run([*git, *ident, "commit", "-qm", "base"], env=ENV, check=True)
    subprocess.run([*git, "tag", "base"], env=ENV, check=True)
    (repo / "package-lock.json").write_text(lock(gpl="GPL-3.0-only"))
    subprocess.run([*git, "add", "-A"], env=ENV, check=True)
    subprocess.run([*git, *ident, "commit", "-qm", "gpl"], env=ENV, check=True)
    env = {**ENV, "GATES_DIR": str(gates)}
    store = Path(tempfile.mkdtemp(dir=workdir)) / "store"
    out = ""
    for verb in ("run", "fold"):
        proc = subprocess.run(
            [PYTHON, str(RUNNER), verb, "--base", "base", "--store", str(store)],
            cwd=repo, env=env, capture_output=True, text=True,
        )  # fmt: skip
        out += proc.stdout + proc.stderr
        if proc.returncode:
            return f"gate.py {verb} exited {proc.returncode} on a row in shadow:\n{out}"
    if "shadow licence: fail: exit 1 (in shadow since" not in out:
        return f"the red row was not folded to shadow:\n{out}"
    (repo / "notes.txt").write_text("b\n")
    subprocess.run([*git, *ident, "commit", "-qam", "notes"], env=ENV, check=True)
    chosen = subprocess.run(
        [PYTHON, str(RUNNER), "select", "--base", "HEAD~1"],
        cwd=repo, env=env, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return (
        f"a change to notes.txt selected the scoped row: {chosen}"
        if "licence" in chosen
        else None
    )


# (case, control it proves, text in run/licence.py, the text with the control removed)
CASES = [
    (refused_dependency, "a refused licence refusing", '    if state == "refused":\n', "    if False:\n"),
    (or_takes_best_alternative, "OR taking its best alternative", "return min(got, key=lambda v: RANK[v[0]])", "return max(got, key=lambda v: RANK[v[0]])"),
    (and_takes_worst_part, "AND taking its worst part", "return max(got, key=lambda v: RANK[v[0]])", "return min(got, key=lambda v: RANK[v[0]])"),
    (review_needs_a_record, "review refusing without a record", "if recorded is not None and fold(recorded) == fold(expr):", "if True:"),
    (reviewed_cannot_accept_refused, "no record accepting a refused licence", '    if state == "refused":\n', '    if state == "refused" and reviewed is None:\n'),
    (unlisted_licence_needs_review, "an unlisted id needing review", 'return "review", f"{tok} is in no list of the policy"', 'return "allowed", f"{tok} is in no list of the policy"'),
    (malformed_expression_needs_review, "a whole expression required", 'raise ValueError(f"{peek()} after a whole expression")', "pass"),
    (missing_licence_needs_declaring, "a dependency with no licence needing one", '    if expr is None:\n        state, why = "review", missing\n', '    if expr is None:\n        state, why = "allowed", missing\n'),
    (refused_crate, "a crate's licence read from cargo metadata", "            expr = re.sub(", "            expr = None and re.sub("),
    (legacy_slash_is_or, "the legacy slash read as OR", 'expr = re.sub(r"\\s*/\\s*", " OR ", crate.get("license") or "") or None', 'expr = crate.get("license") or None'),
    (optional_crate_judged, "every feature's crates resolved", '        "--all-features",\n', ""),
    (stale_cargo_lock_undecided, "cargo kept from rewriting the lockfile", '        "--locked",\n', ""),
    (pnpm_package_refused, "a pnpm package's licence read from its package.json", "licence_field(data.get(\"license\")),", "None and licence_field(data.get(\"license\")),"),
    (pnpm_uninstalled_needs_review, "an uninstalled pnpm package needing review", '            expr, why = None, "not installed, so no package.json to read"', '            expr, why = "MIT", "not installed, so no package.json to read"'),
    (pnpm_v6_keys_read, "a lockfileVersion 6 key read", """PNPM_KEY = re.compile(r"  ['\\"]?/?(""", """PNPM_KEY = re.compile(r"  ['\\"]?("""),
    (project_and_links_skipped, "the project and links skipped", 'if (\n                "node_modules/" not in where\n                or not isinstance(item, dict)\n                or item.get("link")\n            ):', "if not where or not isinstance(item, dict):"),
    (vendored_file_header, "a vendored file's own header read", "            return m.group(1).strip()\n", "            return None\n"),
    (notice_files_not_judged, "a vendored tree's notice files skipped", ' or NOTICES.match(\n            rel.rsplit("/", 1)[-1]\n        )', ""),
    (policy_contradiction_undecided, "a licence in two lists refused", 'problems.append(f"{lid!r} is in both {named[fold(lid)]} and {k}")', "pass"),
    (under_the_runner, "a refused licence refusing", '    if state == "refused":\n', "    if False:\n"),
    (cargo_wrapper_not_run, "cargo run from outside the tree, with no wrapper", "cwd=outside, env=env,", "cwd=(root / path).parent, env=None,"),
    (cargo_compiler_from_config_not_run, "cargo run from outside the tree", "cwd=outside,", "cwd=(root / path).parent,"),
    (cargo_wrapper_from_environment_not_run, "an empty wrapper overriding the environment's", "env.update({k: \"\" for k in CARGO_WRAPPERS})", "pass"),
    (pnpm_decoy_manifest_not_read, "a manifest taken only where the lock entry derives it", "    for manifest in derived:", "    for manifest in [*modules.glob(\"*/package.json\"), *derived]:"),
    (pnpm_decoy_is_not_installed, "a manifest taken only where the lock entry derives it", "    for manifest in derived:", "    for manifest in [*modules.glob(\"*/package.json\"), *derived]:"),
    (pnpm_mismatched_manifest_not_read, "a manifest's own name and version matching the entry", "    if found == (name, ver):", "    if isinstance(data, dict):"),
    (pnpm_manifest_outside_tree_not_read, "a symlink in the manifest's path refused", "manifest.resolve() != manifest", "False"),
    (pnpm_peer_suffixed_directory_read, "a peer-suffixed store directory read", "or d.name.startswith(f\"{stem}_\")", "or False"),
    (tracked_installed_manifest_is_refused, "a tracked installed tree refused", "            if supplied:\n", "            if False:\n"),
    (lower_case_id_refused, "a policy id stored folded", "named[fold(lid)] = k", "named[lid] = k"),
    (lower_case_policy_id_matches, "an id looked up by its folded form", "state = lists.get(fold(tok))", "state = lists.get(tok)"),
    (lists_differing_in_case_contradict, "a contradiction found across case", "if fold(lid) in named and named[fold(lid)] != k:", "if lid in named and named[lid] != k:"),
    (reviewed_accepts_only_its_expression, "a [reviewed] entry accepting only its expression", " and fold(recorded) == fold(expr)", ""),
    (reviewed_expression_is_case_folded, "the recorded expression compared folded", "fold(recorded) == fold(expr)", "recorded == expr"),
    (reviewed_needs_a_licence_to_match, "a [reviewed] entry needing a licence to match", "if reviewed and expr is not None else None", "if reviewed else None"),
    (reviewed_shape_checked, "a [reviewed] entry's shape checked", '                    and set(val) == {"accepts", "reason"}\n', ""),
    (corpus_cases_agree, "a vendored file's own header read", "            return m.group(1).strip()\n", "            return None\n"),
]  # fmt: skip


def main():
    failed = 0
    source = LICENCE.read_text()
    for case, control, old, new in CASES:
        why = case(LICENCE)
        if why:
            failed = 1
            print(f"FAIL {case.__name__}: {why}")
            continue
        print(f"ok   {case.__name__}")
        if source.count(old) != 1:
            failed = 1
            print(
                f"FAIL without {control}: the mutation matches {source.count(old)} places in run/licence.py, not 1"
            )
            continue
        # licence.py imports gate.py from its own directory, so the copy takes it along.
        mutant_dir = Path(tempfile.mkdtemp(dir=workdir))
        shutil.copy(RUNNER, mutant_dir / "gate.py")
        mutant = mutant_dir / "licence.py"
        mutant.write_text(source.replace(old, new))
        why = case(mutant)
        if why:
            quoted = " | ".join(why.splitlines()[:3])
            print(f"ok   without {control}, {case.__name__} goes red: {quoted}")
        else:
            failed = 1
            print(
                f"FAIL without {control}, {case.__name__} still passes, so the case does not prove it"
            )
    print("\nlicence tests " + ("FAILED" if failed else "passed"))
    return failed


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Best effort: the fixtures are throwaway and a leftover only costs disk.
        shutil.rmtree(workdir, ignore_errors=True)
