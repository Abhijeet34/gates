#!/usr/bin/env python3
"""Regression tests for run/owner-verify.py.

    .ci/test-owner-verify.py

Each case serves a pull request, its files, the base's tier list and
CODEOWNERS, and the owner's reviews through a stub `gh` on PATH, runs the judge
against a fixture git tree, and requires its exit status and a line of its output.
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
sys.stdout.write(page.read_text())
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


def git(tree, *args):
    return subprocess.run(
        ["git", "-C", str(tree), *args],
        env=GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def fixture_tree(tmp, name, head_files):
    """A git directory with a base commit and a head commit that writes head_files."""
    tree = tmp / name
    tree.mkdir()
    git(tree, "init", "-q", "-b", "main")
    (tree / "README.md").write_text("base\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", "base")
    base = git(tree, "rev-parse", "HEAD")
    for path, text in head_files.items():
        (tree / path).parent.mkdir(parents=True, exist_ok=True)
        (tree / path).write_text(text)
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
        (
            "a workflow path is held while its route is not built",
            dict(files=[{"filename": ".github/workflows/ci.yml"}]),
            1,
            "workflow .github/workflows/ci.yml: its reader, canary and review are not built",
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
            "a base with no tier list cannot decide",
            dict(files=[], tiers=None),
            2,
            "gh api repos/Abhijeet34/fixture/contents/.github/owner-verify.toml",
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
        for name, case, want_code, want_line in cases():
            total += 1
            head = head_of(case.get("tree", "clean"))
            case = resolve(case, head)
            code, out = run(case)
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
