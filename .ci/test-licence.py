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
ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "PYTHONDONTWRITEBYTECODE": "1",
}
POLICY = """allowed = ["MIT", "Apache-2.0", "ISC"]
review = ["MPL-2.0"]
refused = ["GPL-3.0-only", "AGPL-3.0-only"]
vendored = ["vendor/**"]
"""

workdir = Path(tempfile.mkdtemp())


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


def run(script, files, policy=POLICY):
    """(exit status, output) of the row on a repository holding `files`, every one tracked."""
    repo = Path(tempfile.mkdtemp(dir=workdir))
    for rel, text in {".github/licence-policy.toml": policy, **files}.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    subprocess.run(["git", "init", "-q", str(repo)], env=ENV, check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], env=ENV, check=True)
    proc = subprocess.run(
        [sys.executable, str(script), str(repo)],
        env=ENV,
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
    reviewed = POLICY + '[reviewed]\n"npm:mpl" = "decided in review: used unmodified"\n'
    return why or expect(
        "a reviewed MPL-2.0",
        run(script, lockfile, reviewed),
        0,
        "(1 by a [reviewed] entry)",
    )


def reviewed_cannot_accept_refused(script):
    policy = POLICY + '[reviewed]\n"npm:gpl" = "decided in review"\n'
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
inputs = ["**/package-lock.json", "**/uv.lock", "vendor/**", ".github/licence-policy.toml"]
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
            [sys.executable, str(RUNNER), verb, "--base", "base", "--store", str(store)],
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
        [sys.executable, str(RUNNER), "select", "--base", "HEAD~1"],
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
    (review_needs_a_record, "review refusing without a record", 'if state == "review" and reviewed is None:', "if False:"),
    (reviewed_cannot_accept_refused, "no record accepting a refused licence", '    if state == "refused":\n', '    if state == "refused" and reviewed is None:\n'),
    (unlisted_licence_needs_review, "an unlisted id needing review", 'return "review", f"{tok} is in no list of the policy"', 'return "allowed", f"{tok} is in no list of the policy"'),
    (malformed_expression_needs_review, "a whole expression required", 'raise ValueError(f"{peek()} after a whole expression")', "pass"),
    (missing_licence_needs_declaring, "a dependency with no licence needing one", '    if expr is None:\n        state, why = "review", missing\n', '    if expr is None:\n        state, why = "allowed", missing\n'),
    (project_and_links_skipped, "the project and links skipped", 'if (\n                "node_modules/" not in where\n                or not isinstance(item, dict)\n                or item.get("link")\n            ):', "if not where or not isinstance(item, dict):"),
    (vendored_file_header, "a vendored file's own header read", "            return m.group(1).strip()\n", "            return None\n"),
    (notice_files_not_judged, "a vendored tree's notice files skipped", ' or NOTICES.match(\n            rel.rsplit("/", 1)[-1]\n        )', ""),
    (policy_contradiction_undecided, "a licence in two lists refused", 'problems.append(f"{lid!r} is in both {named[lid]} and {k}")', "pass"),
    (under_the_runner, "a refused licence refusing", '    if state == "refused":\n', "    if False:\n"),
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
