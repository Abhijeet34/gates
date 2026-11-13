#!/usr/bin/env python3
"""Regression tests for run/owner-verify.py.

    .ci/test-owner-verify.py

Each case serves a pull request, its files, the base's tier list and
CODEOWNERS, the owner's reviews, and the head's workflow runs, their jobs and
its check runs named `checks` through a stub `gh` on PATH, runs the judge
against a fixture git tree, and requires its exit status and a line of its output.
A case that names no CI is served one green pull_request run of the base's CI.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JUDGE = ROOT / "run/owner-verify.py"
REPO = "Abhijeet34/fixture"
OWNER = {"login": "Abhijeet34", "id": 101}
BUILDER = {"login": "hw-bodhi[bot]", "id": 202}
OLD = "a" * 40
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@users.noreply.github.com",
}

# Serves `gh api --paginate <path>` from <GH_STUB>/<path with / as __>, printed
# as written, so a case can hand it two pages back to back.
STUB = """#!/usr/bin/env python3
import os, sys
from pathlib import Path
page = Path(os.environ["GH_STUB"]) / sys.argv[-1].replace("/", "__")
if not page.is_file():
    sys.exit("gh: Not Found (HTTP 404)")
text = page.read_text()
if text.startswith("ERR:"):
    sys.exit(text[4:])
sys.stdout.write(text)
"""

TIERS = """\
owner = { login = "Abhijeet34", id = 101 }
maintainer = [".github/CODEOWNERS", ".github/owner-verify.toml", "home/"]
workflow = [".github/workflows/"]
verified = ["internal/domain/permit/"]
checked = [".github/gates.lock"]
"""
CODEOWNERS = """\
# The maintainer list of .github/owner-verify.toml, in GitHub's form.
/.github/CODEOWNERS         @Abhijeet34
/.github/owner-verify.toml  @Abhijeet34
/home/                      @Abhijeet34
"""
CI = ".github/workflows/ci.yml"
ACTIONS = {"id": 15368, "slug": "github-actions"}


def ci_run(number, event="pull_request", path=CI, attempt=1):
    return {
        "id": number,
        "event": event,
        "path": path,
        "head_sha": "HEAD",
        "run_attempt": attempt,
        "status": "completed",
    }


def job(number, conclusion="success", attempt=1, name="checks", status="completed"):
    return {
        "id": number,
        "name": name,
        "run_attempt": attempt,
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "check_run_url": f"https://api.github.com/repos/{REPO}/check-runs/{number}",
    }


def check_run(number, conclusion="success", app=ACTIONS):
    return {
        "id": number,
        "name": "checks",
        "status": "completed",
        "conclusion": conclusion,
        "app": app,
    }


# The pull_request run of the base's CI whose `checks` job is the one check run named checks.
GREEN = {
    "runs": [ci_run(11)],
    "jobs": {11: [job(1100, name="shell"), job(1101)]},
    "check_runs": [check_run(1101)],
}
# A check run named `checks`, success, from GitHub Actions, posted by a run of no workflow the base has.
FORGED = check_run(9001)


def git(tree, *args):
    return subprocess.run(
        ["git", "-C", str(tree), *args],
        env=GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write(tree, files):
    """Write each path's text, delete it for None, or link it for ("link", target)."""
    for path, text in files.items():
        target = tree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            target.unlink()
        if isinstance(text, tuple):
            target.symlink_to(text[1])
        elif text is not None:
            target.write_text(text)


def fixture_tree(tmp, name, head_files, base_files=None):
    """A git directory with a base commit holding base_files and a head commit that writes head_files."""
    tree = tmp / name
    tree.mkdir()
    git(tree, "init", "-q", "-b", "main")
    write(tree, {"README.md": "base\n", **(base_files or {})})
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", "base")
    base = git(tree, "rev-parse", "HEAD")
    write(tree, head_files)
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", "head")
    return tree, base, git(tree, "rev-parse", "HEAD")


def file_page(text, kind="file", encoding="base64"):
    raw = text.encode() if isinstance(text, str) else text
    return {
        "type": kind,
        "encoding": encoding,
        "content": base64.b64encode(raw).decode() if encoding == "base64" else "",
    }


def review(user, state, commit):
    return {"user": user, "state": state, "commit_id": commit}


TREES = {}


def run(case):
    """case: files, and optionally author, reviews, pulls, tiers, owners, ..."""
    tree, base, head = TREES[case.get("tree", "clean")]
    head_in = case.get("head_in", head)
    base_in = case.get("base_in", base)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        def page(path, obj):
            text = obj if isinstance(obj, str) else json.dumps(obj)
            (tmp / path.replace("/", "__")).write_text(text)

        files = case["files"]
        page(f"repos/{REPO}", {"default_branch": "main"})
        pr = {
            "number": 7,
            "user": case.get("author", BUILDER),
            "head": {
                "sha": case.get("live_head", head),
                "repo": {"full_name": case.get("head_repo", REPO)},
            },
            "base": {
                "ref": case.get("base_ref", "main"),
                "sha": case.get("live_base", base),
            },
            "changed_files": case.get("changed", len(files)),
        }
        page(f"repos/{REPO}/pulls/7", pr)
        page(f"repos/{REPO}/pulls/7/files", files)
        tiers = case.get("tiers", file_page(TIERS))
        owners = case.get("owners", file_page(CODEOWNERS))
        if tiers is not None:
            page(f"repos/{REPO}/contents/.github/owner-verify.toml?ref={base}", tiers)
        page(f"repos/{REPO}/contents/.github/CODEOWNERS?ref={base}", owners)
        page(f"repos/{REPO}/commits/{head}/pulls", case.get("pulls", [pr]))
        for number, reviews in case.get("reviews", {7: []}).items():
            page(f"repos/{REPO}/pulls/{number}/reviews", reviews)
        ci = resolve({**GREEN, **{k: case[k] for k in GREEN if k in case}}, head)
        ci_file = case.get("ci_file", file_page("on: pull_request\n"))
        if ci_file is not None:
            page(f"repos/{REPO}/contents/{case.get('ci_path', CI)}?ref={base}", ci_file)
        page(
            f"repos/{REPO}/actions/runs?head_sha={head}&per_page=100",
            {
                "total_count": case.get("runs_total", len(ci["runs"])),
                "workflow_runs": ci["runs"],
            },
        )
        for number, jobs in ci["jobs"].items():
            page(
                f"repos/{REPO}/actions/runs/{number}/jobs?filter=all&per_page=100",
                {"total_count": len(jobs), "jobs": jobs},
            )
        page(
            f"repos/{REPO}/commits/{head}/check-runs?check_name=checks&filter=all&per_page=100",
            {"total_count": len(ci["check_runs"]), "check_runs": ci["check_runs"]},
        )
        argv = [
            sys.executable,
            str(JUDGE),
            *("--repo", REPO, "--pr", case.get("pr", "7")),
            *("--head", head_in, "--base", base_in, "--tree", str(tree)),
        ]
        if "review" in case:
            verdict = tmp / "review.json"
            verdict.write_text(json.dumps(case["review"]))
            argv += ["--review", str(verdict)]
        (tmp / "bin").mkdir()
        gh = tmp / "bin" / "gh"
        gh.write_text(STUB)
        gh.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
            "GH_STUB": str(tmp),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        return proc.returncode, proc.stdout + proc.stderr


def head_of(name="clean"):
    return TREES[name][2]


def pull(number, base_ref="main", name="clean"):
    return {"number": number, "head": {"sha": head_of(name)}, "base": {"ref": base_ref}}


def approved(name="clean", user=OWNER):
    return {7: [review(user, "APPROVED", head_of(name))]}


HOME = [{"filename": "home/queue.toml"}]
BUILD = ".github/workflows/build.yml"
PIN = "0123456789abcdef0123456789abcdef01234567"
PIN2 = "89abcdef0123456789abcdef0123456789abcdef"
# The base's workflow every workflow case edits.
WORKFLOW = f"""\
name: build

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

concurrency:
  group: build-${{{{ github.ref }}}}
  cancel-in-progress: true

jobs:
  build:
    name: build
    runs-on: ubuntu-22.04
    timeout-minutes: 10
    strategy:
      matrix:
        python: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@{PIN} # v4
        with:
          persist-credentials: false
      - name: Test
        env:
          LEVEL: unit
        run: |
          python3 -m unittest -q
"""


def edit(old, new, count=1):
    assert old in WORKFLOW, old
    return WORKFLOW.replace(old, new, count)


# Twelve workflow edits that change no trigger and no permission, each held on
# main before the workflow route was built.
CLEAN_EDITS = {
    "a step added": edit(
        "      - name: Test\n",
        "      - name: Lint\n        run: python3 -m pyflakes .\n      - name: Test\n",
    ),
    "an action pin bumped": edit(PIN, PIN2),
    "a run script changed": edit("python3 -m unittest -q", "python3 -m unittest -v"),
    "a job's display name changed": edit(
        "    name: build\n", "    name: build and test\n"
    ),
    "a timeout changed": edit("timeout-minutes: 10", "timeout-minutes: 20"),
    "a step env var added": edit(
        "LEVEL: unit\n", "LEVEL: unit\n          COLUMNS: '120'\n"
    ),
    "a runner image moved": edit("ubuntu-22.04", "ubuntu-24.04"),
    "a job added": WORKFLOW
    + "  docs:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: echo docs\n",
    "a concurrency group changed": edit("build-${{", "build-pr-${{"),
    "a comment edited": edit("# v4", "# v4.2.2"),
    "a matrix entry added": edit('"3.12"]', '"3.12", "3.13"]'),
    "a step input changed": edit(
        "persist-credentials: false\n",
        "persist-credentials: false\n          fetch-depth: 0\n",
    ),
}
# Edits that change a trigger, a permission or a credential, or that the reader
# cannot read, each held: (head text, the line the hold names).
HELD_EDITS = {
    "a trigger added": (
        edit("on:\n", "on:\n  workflow_dispatch:\n"),
        "the triggers (on:) changed",
    ),
    "a trigger's branches widened": (
        edit("branches: [main]", "branches: ['**']"),
        "the triggers (on:) changed",
    ),
    "a workflow token's write scope": (
        edit("contents: read", "contents: write"),
        "a permissions: block changed",
    ),
    "a job's write scope": (
        edit(
            "    timeout-minutes: 10\n",
            "    timeout-minutes: 10\n    permissions:\n      pull-requests: write\n",
        ),
        "a permissions: block changed",
    ),
    "a secret read": (
        edit("LEVEL: unit\n", "LEVEL: unit\n          TOKEN: ${{ secrets.DEPLOY }}\n"),
        "names secrets",
    ),
    "an environment added": (
        edit(
            "    timeout-minutes: 10\n",
            "    timeout-minutes: 10\n    environment: release\n",
        ),
        "an environment: block changed",
    ),
    "a self-hosted runner": (
        edit("ubuntu-22.04", "[self-hosted, linux]"),
        "names a self-hosted runner",
    ),
    "a quoted trigger key": (
        edit("on:\n  pull_request:\n", "'on':\n  pull_request:\n"),
        "the top-level key 'on' changed",
    ),
    "a YAML 1.1 true key": (
        edit("on:\n  pull_request:\n", "true:\n  pull_request:\n"),
        "the top-level key true changed",
    ),
    "an anchor beside an edit": (
        edit("    timeout-minutes: 10\n", "    timeout-minutes: &t 10\n"),
        "uses a YAML anchor or alias",
    ),
    "a flow mapping": (
        edit(
            "        with:\n          persist-credentials: false\n",
            "        with: {persist-credentials: false}\n",
        ),
        "opens a flow mapping",
    ),
    "a runner label no hosted image has": (
        edit("ubuntu-22.04", "build-box"),
        "runs on 'build-box', not a GitHub-hosted image",
    ),
    "a runner from an expression": (
        edit("ubuntu-22.04", "${{ matrix.runner }}"),
        "runs on '${{ matrix.runner }}', not a GitHub-hosted image",
    ),
    "a matrix edit beside a runner the base already reads from an expression": (
        edit('"3.12"]', '"3.12", "3.13"]'),
        "runs on an expression, so an edit elsewhere may move it",
    ),
    "a tab": (
        edit("LEVEL: unit\n", "LEVEL: unit\n          COLUMNS:\t'120'\n"),
        "holds a tab or a space YAML does not indent with",
    ),
    "permissions in another form": (
        edit(
            "LEVEL: unit\n",
            "LEVEL: unit\n          X: ${{ toJSON(github.permissions) }}\n",
        ),
        "holds `permissions` outside a plain permissions: key",
    ),
}
PERMIT = [{"filename": "internal/domain/permit/permit.go"}]


def cases():
    return [
        (
            "a change to no listed path passes",
            dict(files=[{"filename": "README.md"}]),
            0,
            "owner-verify: pass:",
        ),
        (
            "a maintainer path opened under the owner's login refuses and says why",
            dict(files=HOME, author=OWNER),
            1,
            "opened under the owner's login, whose own pull request Abhijeet34 cannot approve",
        ),
        (
            "a maintainer path with no approval refuses",
            dict(files=HOME),
            1,
            "owner-verify: maintainer home/queue.toml: needs the owner's approval at the head",
        ),
        (
            "a maintainer path the owner approved at the head passes",
            dict(files=HOME, reviews=approved()),
            0,
            "owner-verify: pass: Abhijeet34 approved",
        ),
        (
            "an approval at an older commit refuses",
            dict(files=HOME, reviews={7: [review(OWNER, "APPROVED", OLD)]}),
            1,
            "owner-verify: refused: 1 item(s) above need Abhijeet34's approval",
        ),
        (
            "an approval on a second pull request at the same head passes",
            dict(
                files=HOME,
                pulls=[pull(7), pull(9)],
                reviews={7: [], 9: [review(OWNER, "APPROVED", "HEAD")]},
            ),
            0,
            "approved",
        ),
        (
            "the owner's own pull request passes on an approval at its head elsewhere",
            dict(
                files=HOME,
                author=OWNER,
                pulls=[pull(7), pull(9)],
                reviews={7: [], 9: [review(OWNER, "APPROVED", "HEAD")]},
            ),
            0,
            "on pull request 9",
        ),
        (
            "an approval on a pull request into another base does not count",
            dict(
                files=HOME,
                pulls=[pull(9, base_ref="staging")],
                reviews={9: [review(OWNER, "APPROVED", "HEAD")]},
            ),
            1,
            "refused:",
        ),
        (
            "an approval by a login with another id does not count",
            dict(
                files=HOME,
                reviews=approved(user={"login": "Abhijeet34", "id": 303}),
            ),
            1,
            "refused:",
        ),
        (
            "changes requested after the approval refuses",
            dict(
                files=HOME,
                reviews={
                    7: [
                        review(OWNER, "APPROVED", "HEAD"),
                        review(OWNER, "CHANGES_REQUESTED", "HEAD"),
                    ]
                },
            ),
            1,
            "refused:",
        ),
        (
            "a comment after the approval leaves it standing",
            dict(
                files=HOME,
                reviews={
                    7: [
                        review(OWNER, "APPROVED", "HEAD"),
                        review(OWNER, "COMMENTED", "HEAD"),
                    ]
                },
            ),
            0,
            "approved",
        ),
        (
            "renaming a maintainer path away keeps its route",
            dict(
                files=[
                    {"filename": "notes.toml", "previous_filename": "home/queue.toml"}
                ]
            ),
            1,
            "owner-verify: maintainer notes.toml <- home/queue.toml",
        ),
        *(
            (
                f"a workflow edit with {what} passes",
                dict(tree=f"clean-wf {what}", files=[{"filename": BUILD}]),
                0,
                f"owner-verify: workflow {BUILD}: no trigger, permission or credential changed",
            )
            for what in CLEAN_EDITS
        ),
        *(
            (
                f"a workflow edit with {what} is held",
                dict(tree=f"held-wf {what}", files=[{"filename": BUILD}]),
                1,
                f"owner-verify: workflow {BUILD}: {line}",
            )
            for what, (_, line) in HELD_EDITS.items()
        ),
        (
            "a held workflow edit the owner approved passes",
            dict(
                tree="held-wf a trigger added",
                files=[{"filename": BUILD}],
                reviews=approved("held-wf a trigger added"),
            ),
            0,
            "owner-verify: pass: Abhijeet34 approved",
        ),
        (
            "a trigger change in a root mapping indented throughout is held",
            dict(tree="indented-wf", files=[{"filename": BUILD}]),
            1,
            f"owner-verify: workflow {BUILD}: the triggers (on:) changed",
        ),
        (
            "a new workflow is held",
            dict(tree="new-wf", files=[{"filename": BUILD}]),
            1,
            f"owner-verify: workflow {BUILD}: a new workflow adds its triggers",
        ),
        (
            "a deleted workflow is held",
            dict(tree="gone-wf", files=[{"filename": BUILD}]),
            1,
            f"owner-verify: workflow {BUILD}: deleting a workflow removes its triggers",
        ),
        (
            "a renamed workflow is held",
            dict(
                tree="moved-wf",
                files=[
                    {
                        "filename": ".github/workflows/make.yml",
                        "previous_filename": BUILD,
                    }
                ],
            ),
            1,
            "owner-verify: workflow .github/workflows/make.yml <- "
            f"{BUILD}: a renamed workflow is a new workflow",
        ),
        (
            "a file under the workflow path that is not YAML is held",
            dict(tree="notes-wf", files=[{"filename": ".github/workflows/NOTES.md"}]),
            1,
            "owner-verify: workflow .github/workflows/NOTES.md: not a .yml or .yaml workflow",
        ),
        (
            "a workflow made a link is held",
            dict(tree="link-wf", files=[{"filename": BUILD}]),
            1,
            f"owner-verify: workflow {BUILD}: is not a regular file at the head",
        ),
        (
            "a workflow the tree does not hold cannot decide",
            dict(files=[{"filename": ".github/workflows/ci.yml"}]),
            2,
            "could not decide: .github/workflows/ci.yml is at neither the base nor the head",
        ),
        (
            "a checked path passes",
            dict(files=[{"filename": ".github/gates.lock"}]),
            0,
            "owner-verify: checked .github/gates.lock: passes on this check",
        ),
        (
            "a verified path with no review verdict refuses",
            dict(files=PERMIT),
            1,
            "no review verdict was given, so it needs the owner's approval",
        ),
        (
            "a verified path whose review holds nothing passes",
            dict(files=PERMIT, review={"head": "HEAD", "blocking": 0}),
            0,
            "the review holds no blocking finding",
        ),
        (
            "a verified path whose review holds a finding refuses",
            dict(files=PERMIT, review={"head": "HEAD", "blocking": 2}),
            1,
            "the review holds 2 blocking finding(s)",
        ),
        (
            "a review verdict for another head cannot decide",
            dict(files=PERMIT, review={"head": OLD, "blocking": 0}),
            2,
            "is for aaaaaaaaaaaa, not the head",
        ),
        (
            "a review verdict with another shape cannot decide",
            dict(files=PERMIT, review={"head": "HEAD", "blocking": 0, "pass": True}),
            2,
            "not {head, blocking}",
        ),
        (
            "a head from a fork gets the deterministic verdict alone",
            dict(
                files=PERMIT,
                head_repo="someone/fixture",
                review={"head": "HEAD", "blocking": 0},
            ),
            1,
            "a head outside the repository gets the deterministic verdict alone",
        ),
        (
            "an unmatched weakening refuses",
            dict(tree="weak", files=[{"filename": "README.md"}]),
            1,
            "owner-verify: weakening: an unmatched weakening above needs the owner's approval",
        ),
        (
            "an unmatched weakening the owner approved passes",
            dict(
                tree="weak", files=[{"filename": "README.md"}], reviews=approved("weak")
            ),
            0,
            "approved",
        ),
        (
            "a pull request into another base refuses",
            dict(files=[{"filename": "README.md"}], base_ref="staging"),
            1,
            "the verdict is only ever against the default branch",
        ),
        (
            "a run for a head that moved refuses",
            dict(files=[{"filename": "README.md"}], live_head=OLD),
            1,
            "the run for those decides",
        ),
        (
            "a run for a base that moved refuses",
            dict(files=[{"filename": "README.md"}], live_base=OLD),
            1,
            "the run for those decides",
        ),
        (
            "a files list shorter than the change cannot decide",
            dict(files=[{"filename": "README.md"}], changed=3001),
            2,
            "listed 1 of 3001 changed files",
        ),
        (
            "a head that is not 40 hex cannot decide",
            dict(files=[], head_in="main"),
            2,
            "--head and --base 40 hex",
        ),
        (
            "a pull request that is not digits cannot decide",
            dict(files=[], pr="7;id"),
            2,
            "--pr must be digits",
        ),
        (
            "a base with no tier list is not enrolled",
            dict(files=[], tiers=None),
            3,
            "owner-verify: not enrolled: no .github/owner-verify.toml at BASE",
        ),
        (
            "a malformed tier list still cannot decide",
            dict(files=[], tiers=file_page("owner = [\n")),
            2,
            "could not decide",
        ),
        (
            "a 5xx reading the tier list cannot decide",
            dict(files=[], tiers="ERR:gh: Bad Gateway (HTTP 502)"),
            2,
            "could not decide: gh api repos/Abhijeet34/fixture/contents/.github/owner-verify.toml",
        ),
        (
            "a timeout reading the tier list cannot decide",
            dict(
                files=[], tiers="ERR:Post: context deadline exceeded (Client.Timeout)"
            ),
            2,
            "could not decide",
        ),
        (
            "a tier list with an unknown key cannot decide",
            dict(files=[], tiers=file_page(TIERS + 'skip = ["home/"]\n')),
            2,
            "unknown key skip",
        ),
        (
            "a tier list naming a list twice cannot decide",
            dict(files=[], tiers=file_page(TIERS + 'verified = ["home/"]\n')),
            2,
            "owner-verify.toml at the base:",
        ),
        (
            "a path in two lists cannot decide",
            dict(
                files=[],
                tiers=file_page(TIERS.replace('"internal/domain/permit/"', '"home/"')),
            ),
            2,
            "home/ is listed twice",
        ),
        (
            "a tier list that frees itself cannot decide",
            dict(
                files=[],
                tiers=file_page(TIERS.replace('".github/owner-verify.toml", ', "")),
            ),
            2,
            "maintainer must list .github/owner-verify.toml",
        ),
        (
            "a tier list path with a glob cannot decide",
            dict(files=[], tiers=file_page(TIERS.replace('"home/"', '"home/*.toml"'))),
            2,
            "is not a literal repository path",
        ),
        (
            "a tier list path climbing out cannot decide",
            dict(files=[], tiers=file_page(TIERS.replace('"home/"', '"home/../cmd/"'))),
            2,
            "is not a literal repository path",
        ),
        (
            "an owner id that is not a number cannot decide",
            dict(files=[], tiers=file_page(TIERS.replace("id = 101", 'id = "101"'))),
            2,
            "owner must be { login, id }",
        ),
        (
            "a CODEOWNERS that differs from the maintainer list cannot decide",
            dict(files=[], owners=file_page(CODEOWNERS.replace("/home/", "/cmd/"))),
            2,
            "differ at the base",
        ),
        (
            "a later CODEOWNERS line handing a path to another owner cannot decide",
            dict(
                files=[], owners=file_page(CODEOWNERS + "/home/queue.toml @hw-bodhi\n")
            ),
            2,
            "is not `/<path> @Abhijeet34`",
        ),
        (
            "a CODEOWNERS line with no owner cannot decide",
            dict(files=[], owners=file_page(CODEOWNERS + "/home/queue.toml\n")),
            2,
            "is not `/<path> @Abhijeet34`",
        ),
        (
            "a CODEOWNERS too large to serve cannot decide",
            dict(files=[], owners=file_page("", encoding="none")),
            2,
            "is not served whole",
        ),
        (
            "a CODEOWNERS that is a link cannot decide",
            dict(files=[], owners=file_page("home/x", kind="symlink")),
            2,
            "is not a regular file",
        ),
        (
            "a CODEOWNERS narrowed by a glob cannot decide",
            dict(
                files=[], owners=file_page(CODEOWNERS.replace("/home/ ", "/home/*.go"))
            ),
            2,
            "is not a literal repository path",
        ),
        (
            "a CODEOWNERS owner spelled with a look-alike letter cannot decide",
            dict(
                files=[],
                owners=file_page(
                    CODEOWNERS.replace("@Abhijeet34\n", "@\u0410bhijeet34\n", 1)
                ),
            ),
            2,
            "holds a non-ASCII byte",
        ),
        (
            "a forged success check run named checks with no workflow run refuses",
            dict(
                files=[{"filename": "README.md"}], runs=[], jobs={}, check_runs=[FORGED]
            ),
            1,
            "owner-verify: ci: no pull_request run of .github/workflows/ci.yml at the head",
        ),
        (
            "the owner's approval does not pass a head CI did not run for",
            dict(files=HOME, reviews=approved(), runs=[], jobs={}, check_runs=[FORGED]),
            1,
            "owner-verify: refused: the head's tests are not shown to have run",
        ),
        (
            "a forged check run beside a green run refuses",
            dict(
                files=[{"filename": "README.md"}],
                check_runs=[check_run(1101), FORGED],
            ),
            1,
            "owner-verify: ci: check run 9001 named checks from github-actions (app 15368) is not",
        ),
        (
            "a checks run from another app beside a green run refuses",
            dict(
                files=[{"filename": "README.md"}],
                check_runs=[
                    check_run(1101),
                    check_run(9002, app={"id": 77, "slug": "x"}),
                ],
            ),
            1,
            "check run 9002 named checks from x (app 77) is not",
        ),
        (
            "a run of a workflow the base does not name does not count",
            dict(
                files=[{"filename": "README.md"}],
                runs=[ci_run(12, path=".github/workflows/forge.yml")],
                jobs={12: [job(1201)]},
                check_runs=[check_run(1201)],
            ),
            1,
            "ci: no pull_request run of .github/workflows/ci.yml at the head",
        ),
        (
            "a push run of the base's workflow does not count",
            dict(
                files=[{"filename": "README.md"}],
                runs=[ci_run(12, event="push")],
                jobs={12: [job(1201)]},
                check_runs=[check_run(1201)],
            ),
            1,
            "ci: no pull_request run of .github/workflows/ci.yml at the head",
        ),
        (
            "a run for another head does not count",
            dict(
                files=[{"filename": "README.md"}],
                runs=[{**ci_run(11), "head_sha": OLD}],
            ),
            1,
            "ci: no pull_request run of .github/workflows/ci.yml at the head",
        ),
        (
            "a checks job that failed refuses",
            dict(
                files=[{"filename": "README.md"}],
                jobs={11: [job(1101, conclusion="failure")]},
                check_runs=[check_run(1101, conclusion="failure")],
            ),
            1,
            "ci: the checks job of run 11 (attempt 1) concluded failure",
        ),
        (
            "a checks job still running refuses",
            dict(
                files=[{"filename": "README.md"}],
                jobs={11: [job(1101, status="in_progress")]},
                check_runs=[{**check_run(1101), "status": "in_progress"}],
            ),
            1,
            "ci: the checks job of run 11 (attempt 1) is in_progress",
        ),
        (
            "a run with no checks job refuses",
            dict(
                files=[{"filename": "README.md"}],
                jobs={11: [job(1100, name="shell")]},
                check_runs=[],
            ),
            1,
            "ci: run 11 (attempt 1) has no job named checks",
        ),
        (
            "a re-run's latest attempt decides, and its earlier check run is its own",
            dict(
                files=[{"filename": "README.md"}],
                runs=[ci_run(11, attempt=2)],
                jobs={11: [job(1101, "failure"), job(1102, attempt=2)]},
                check_runs=[check_run(1101, "failure"), check_run(1102)],
            ),
            0,
            "owner-verify: ci: run 11 (attempt 2) of .github/workflows/ci.yml: checks concluded success",
        ),
        (
            "a re-run that failed refuses although an earlier attempt passed",
            dict(
                files=[{"filename": "README.md"}],
                runs=[ci_run(11, attempt=2)],
                jobs={11: [job(1101), job(1102, "failure", attempt=2)]},
                check_runs=[check_run(1101), check_run(1102, "failure")],
            ),
            1,
            "ci: the checks job of run 11 (attempt 2) concluded failure",
        ),
        (
            "the newest pull_request run decides",
            dict(
                files=[{"filename": "README.md"}],
                runs=[ci_run(12), ci_run(11)],
                jobs={11: [job(1101)], 12: [job(1201, "failure")]},
                check_runs=[check_run(1101), check_run(1201, "failure")],
            ),
            1,
            "ci: the checks job of run 12 (attempt 1) concluded failure",
        ),
        (
            "a review run of the base's workflow is not another check",
            dict(
                files=[{"filename": "README.md"}],
                runs=[ci_run(12, event="pull_request_review"), ci_run(11)],
                jobs={11: [job(1101)], 12: [job(1201)]},
                check_runs=[check_run(1201), check_run(1101)],
            ),
            0,
            "owner-verify: pass:",
        ),
        (
            "a tier list naming another CI workflow reads that one",
            dict(
                files=[{"filename": "README.md"}],
                tiers=file_page(TIERS + 'ci = ".github/workflows/test.yml"\n'),
                ci_path=".github/workflows/test.yml",
                runs=[ci_run(11, path=".github/workflows/test.yml")],
            ),
            0,
            "run 11 (attempt 1) of .github/workflows/test.yml: checks concluded success",
        ),
        (
            "a tier list CI path with a glob cannot decide",
            dict(
                files=[],
                tiers=file_page(TIERS + 'ci = ".github/workflows/*.yml"\n'),
            ),
            2,
            "is not a literal repository path",
        ),
        (
            "a base without the CI workflow cannot decide",
            dict(files=[{"filename": "README.md"}], ci_file=None),
            2,
            "gh api repos/Abhijeet34/fixture/contents/.github/workflows/ci.yml",
        ),
        (
            "a run list shorter than its total cannot decide",
            dict(files=[{"filename": "README.md"}], runs_total=1001),
            2,
            "listed 1 of 1001 workflow_runs",
        ),
    ]


def resolve(value, head):
    """Replace the placeholder HEAD with the case's head commit."""
    if isinstance(value, dict):
        return {k: resolve(v, head) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, head) for v in value]
    return head if value == "HEAD" else value


def main():
    failed = total = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        TREES["clean"] = fixture_tree(tmp, "clean", {"README.md": "base\nhead\n"})
        TREES["weak"] = fixture_tree(
            tmp,
            "weak",
            {
                ".github/workflows/ci.yml": "on: pull_request\njobs:\n  a:\n    runs-on: ubuntu-24.04\n"
                "    steps:\n      - uses: actions/checkout@v4\n"
            },
        )
        for what, text in CLEAN_EDITS.items():
            TREES[f"clean-wf {what}"] = fixture_tree(
                tmp, f"clean-{len(TREES)}", {BUILD: text}, {BUILD: WORKFLOW}
            )
        for what, (text, _) in HELD_EDITS.items():
            base_wf = WORKFLOW
            if "runner the base already" in what:
                base_wf, text = (
                    x.replace("ubuntu-22.04", "${{ matrix.runner }}")
                    for x in (WORKFLOW, text)
                )
            TREES[f"held-wf {what}"] = fixture_tree(
                tmp, f"held-{len(TREES)}", {BUILD: text}, {BUILD: base_wf}
            )
        indented = "".join(f"  {x}" if x else x for x in WORKFLOW.splitlines(True))
        TREES["indented-wf"] = fixture_tree(
            tmp,
            "indented-wf",
            {
                BUILD: indented.replace(
                    "    pull_request:\n", "    pull_request_target:\n"
                )
            },
            {BUILD: indented},
        )
        TREES["new-wf"] = fixture_tree(tmp, "new-wf", {BUILD: WORKFLOW})
        TREES["gone-wf"] = fixture_tree(
            tmp, "gone-wf", {BUILD: None}, {BUILD: WORKFLOW}
        )
        TREES["moved-wf"] = fixture_tree(
            tmp,
            "moved-wf",
            {BUILD: None, ".github/workflows/make.yml": WORKFLOW},
            {BUILD: WORKFLOW},
        )
        TREES["notes-wf"] = fixture_tree(
            tmp, "notes-wf", {".github/workflows/NOTES.md": "notes\n"}
        )
        TREES["link-wf"] = fixture_tree(
            tmp, "link-wf", {BUILD: ("link", "make.yml")}, {BUILD: WORKFLOW}
        )
        for name, case, want_code, want_line in cases():
            total += 1
            head = head_of(case.get("tree", "clean"))
            case = resolve(case, head)
            code, out = run(case)
            want_line = want_line.replace("BASE", TREES[case.get("tree", "clean")][1])
            if code == want_code and want_line in out:
                print(f"ok   {name}")
            else:
                failed += 1
                print(
                    f"FAIL {name}: exit {code} (want {want_code}), want {want_line!r} in:"
                )
                print("     " + out.replace("\n", "\n     "))
    if failed:
        print(f"owner-verify: {failed} of {total} case(s) failed", file=sys.stderr)
        return 1
    print(f"owner-verify: all {total} cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
