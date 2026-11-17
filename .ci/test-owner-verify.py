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
import importlib.util
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
        if "train" in case:
            base_tree = git(tree, "rev-parse", f"{base}^{{tree}}")
            head_tree = git(tree, "rev-parse", f"{head}^{{tree}}")
            main_tree = "f" * 40 if case["train"] == "moved" else base_tree
            page(
                f"repos/{REPO}/branches/main",
                {"commit": {"sha": base, "commit": {"tree": {"sha": main_tree}}}},
            )
            # The train is this head alone, so its runs are the head's runs.
            for r in ci["runs"]:
                r.setdefault("head_commit", {"tree_id": head_tree})
                r.setdefault("conclusion", "success")
            page(
                f"repos/{REPO}/actions/runs?head_sha={head}&per_page=100",
                {"total_count": len(ci["runs"]), "workflow_runs": ci["runs"]},
            )
            members = [OLD] if case["train"] == "stranger" else [head]
            train = tmp / "train.json"
            train.write_text(
                json.dumps(
                    {"base": base, "members": members, "member": 1, "head": head}
                )
            )
            argv += ["--train", str(train)]
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


def judge_module():
    """run/owner-verify.py imported in this process, for its pure functions."""
    # A .pyc under run/ would read as an unregistered gate to gates-proof.
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("owner_verify", JUDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def train_fixture(tmp):
    """A train base and three members branched from it: two that compose and one that conflicts with the first."""
    tree = tmp / "train"
    tree.mkdir()
    git(tree, "init", "-q", "-b", "main")
    (tree / "a.txt").write_text("a\n")
    (tree / "b.txt").write_text("b\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-qm", "base")
    base = git(tree, "rev-parse", "HEAD")
    heads = {}
    for name, path, text in (
        ("m1", "a.txt", "a1\n"),
        ("m2", "b.txt", "b2\n"),
        ("clash", "a.txt", "a9\n"),
    ):
        git(tree, "checkout", "-q", "-b", name, base)
        (tree / path).write_text(text)
        git(tree, "commit", "-qam", name)
        heads[name] = git(tree, "rev-parse", "HEAD")
    git(tree, "checkout", "-q", "main")
    # The train head factory would push: m1 then m2 merged onto the base.
    git(tree, "checkout", "-q", "-b", "train", base)
    git(tree, "merge", "-q", "--no-ff", "-m", "m1", heads["m1"])
    after_m1 = git(tree, "rev-parse", "HEAD^{tree}")
    git(tree, "merge", "-q", "--no-ff", "-m", "m2", heads["m2"])
    git(tree, "checkout", "-q", "main")
    trees = {
        "base": git(tree, "rev-parse", f"{base}^{{tree}}"),
        "after_m1": after_m1,
        "train": git(tree, "rev-parse", "train^{tree}"),
        "m1": git(tree, "rev-parse", f"{heads['m1']}^{{tree}}"),
    }
    return tree, base, heads, trees, git(tree, "rev-parse", "train")


def tree_cases(mod, tmp):
    """(name, thunk, want_ok, want_line): the tree rule of README.md, "The tree rule"."""
    tree, base, heads, trees, train_head = train_fixture(tmp)
    two = [heads["m1"], heads["m2"]]

    def green(tree_id, number=1, conclusion="success", head=train_head):
        return {
            "id": number,
            "head_sha": head,
            "tree": tree_id,
            "conclusion": conclusion,
        }

    def rule(members, member, main_tree, runs):
        return lambda: mod.tree_rule(str(tree), base, members, member, main_tree, runs)

    other = "f" * 40
    return [
        (
            "tree rule: member 2 passes while main has the tree the train gives before it, on a green run of the train's tree",
            rule(two, 2, trees["after_m1"], [green(trees["train"])]),
            True,
            "pass: main has the tree of the train before member 2",
        ),
        (
            "tree rule: member 1 passes while main still has the base's tree",
            rule(two, 1, trees["base"], [green(trees["train"])]),
            True,
            "pass: main has the tree of the train before member 1",
        ),
        (
            "tree rule: member 2 fails once main moved to another tree",
            rule(two, 2, other, [green(trees["train"])]),
            False,
            f"main is at tree {other[:12]}, not {trees['after_m1'][:12]}",
        ),
        (
            "tree rule: member 2 fails while main is still at the base, before member 1 landed",
            rule(two, 2, trees["base"], [green(trees["train"])]),
            False,
            f"main is at tree {trees['base'][:12]}, not {trees['after_m1'][:12]}",
        ),
        (
            "tree rule: no run of the train's tree fails",
            rule(two, 2, trees["after_m1"], []),
            False,
            f"no run of the base's CI has the train's tree {trees['train'][:12]}",
        ),
        (
            "tree rule: a green run of another tree is no run of the train's",
            rule(two, 2, trees["after_m1"], [green(trees["after_m1"])]),
            False,
            f"no run of the base's CI has the train's tree {trees['train'][:12]}",
        ),
        (
            "tree rule: a red run of the train's tree fails",
            rule(
                two, 2, trees["after_m1"], [green(trees["train"], conclusion="failure")]
            ),
            False,
            "concluded failure",
        ),
        (
            "tree rule: the newest run of the train's tree decides, so a red re-run fails",
            rule(
                two,
                2,
                trees["after_m1"],
                [green(trees["train"], 1), green(trees["train"], 2, "failure")],
            ),
            False,
            "run 2",
        ),
        (
            "tree rule: a run not yet ended fails",
            rule(two, 2, trees["after_m1"], [green(trees["train"], conclusion=None)]),
            False,
            "concluded None",
        ),
        (
            "tree rule: a single-member train passes on its own green head while main has the tree before it",
            rule(
                [heads["m1"]],
                1,
                trees["base"],
                [green(trees["m1"], head=heads["m1"])],
            ),
            True,
            "pass: main has the tree of the train before member 1",
        ),
        (
            "tree rule: a single-member train fails once main moved",
            rule([heads["m1"]], 1, other, [green(trees["m1"], head=heads["m1"])]),
            False,
            f"main is at tree {other[:12]}",
        ),
        (
            "tree rule: members that conflict cannot be composed and fail",
            rule(
                [heads["m1"], heads["clash"]],
                2,
                trees["after_m1"],
                [green(trees["train"])],
            ),
            False,
            "does not compose",
        ),
        (
            "tree rule: a member index outside the train fails",
            rule(two, 3, trees["after_m1"], [green(trees["train"])]),
            False,
            "member 3 is not in a train of 2",
        ),
        (
            "tree rule: the pure judgement passes on the composition it is handed",
            lambda: mod.judge_tree(2, ["t0", "t1", "t2"], "t1", [green("t2")]),
            True,
            "pass: main has the tree of the train before member 2",
        ),
        (
            "tree rule: the pure judgement fails on a main that is the train's own final tree",
            lambda: mod.judge_tree(2, ["t0", "t1", "t2"], "t2", [green("t2")]),
            False,
            "main is at tree t2, not t1",
        ),
    ]


def shadow_cases():
    """The tree rule wired into the judge in shadow: its line is printed and the exit status is the head rule's."""
    return [
        (
            "tree rule in shadow: a passing member prints its line and the head rule decides",
            dict(files=[{"filename": "README.md"}], train="pass"),
            0,
            "owner-verify: tree rule (shadow): pass: main has the tree of the train before member 1",
        ),
        (
            "tree rule in shadow: a failing member prints its line and does not refuse",
            dict(files=[{"filename": "README.md"}], train="moved"),
            0,
            "owner-verify: tree rule (shadow): fail:",
        ),
        (
            "tree rule in shadow: a train that does not name the judged head is undecided in shadow only",
            dict(files=[{"filename": "README.md"}], train="stranger"),
            0,
            "owner-verify: tree rule (shadow): could not decide:",
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
        for name, thunk, want_ok, want_line in tree_cases(judge_module(), tmp):
            total += 1
            try:
                ok, lines = thunk()
            # A missing or broken rule is a failed case, not a crash.
            except Exception as e:
                ok, lines = None, [f"{type(e).__name__}: {e}"]
            if ok == want_ok and any(want_line in ln for ln in lines):
                print(f"ok   {name}")
            else:
                failed += 1
                print(f"FAIL {name}: {ok} (want {want_ok}), want {want_line!r} in:")
                print("     " + "\n     ".join(lines))
        for name, case, want_code, want_line in cases() + shadow_cases():
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
