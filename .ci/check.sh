#!/usr/bin/env bash
# Repo-wide checks. CI runs these exact commands, so there is exactly one
# definition of "clean".
#
#   .ci/check.sh [suite ...]    suites: shell python secrets gates-proof all
#
# Every requested suite runs even when an earlier one fails, so one pass
# reports every problem; the exit code is nonzero if any failed.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

# Pinned so a local run and a CI run apply the same rules.
RUFF_VERSION="0.16.0"

failed=()
run() {
    local name="$1"
    shift
    printf '\n\033[1m==> %s\033[0m\n' "$name"
    if "$@"; then
        printf '\033[32mok\033[0m %s\n' "$name"
    else
        printf '\033[31mFAIL\033[0m %s\n' "$name"
        failed+=("$name")
    fi
}

need() {
    if ! command -v "$1" >/dev/null 2>&1; then
        printf '\033[31mmissing tool:\033[0m %s - install with: %s\n' "$1" "$2" >&2
        return 1
    fi
}

# Same contract as `need`, for a tool whose binary is named differently across
# versions: satisfied by any one of the names, and refuses naming all of them.
need_any() {
    local remedy="$1" name
    shift
    for name in "$@"; do
        command -v "$name" >/dev/null 2>&1 && return 0
    done
    printf '\033[31mmissing tool:\033[0m %s - install with: %s\n' "$*" "$remedy" >&2
    return 1
}

# --- shell -------------------------------------------------------------------

# Git hooks carry no extension, so the two hook directories are listed whole and
# a hook added later is linted by default. *.md, *.py and *.conf are skipped:
# ruff covers the Python, test-watermark-scan.py parses the rule files.
shell_files() {
    local f
    while IFS= read -r -d '' f; do
        case "$f" in *.md | *.py | *.conf) continue ;; esac
        printf '%s\0' "$f"
    done < <(git ls-files -z '*.sh' '.githooks/*' 'system-maintenance/git-hooks/*')
}

check_shell() {
    need shellcheck 'brew install shellcheck' || return 1
    need yq 'brew install yq' || return 1
    need exiftool 'brew install exiftool' || return 1
    local rc=0
    # -x follows `source`d files so shared helpers are analysed in context.
    shell_files | xargs -0 shellcheck -x --severity=warning || rc=1
    # Forbidden triggers, self-hosted runners and any secret but GITHUB_TOKEN,
    # refused in every workflow file rather than remembered at review.
    bash .ci/test-workflow-policy.sh || rc=1
    # Each suite below is a network-free exercise of one gate's rule, run
    # against its run/ script or a throwaway repository.
    bash .ci/test-checks-verdict.sh || rc=1
    bash .ci/test-guard-generated-files.sh || rc=1
    bash .ci/test-dependency-advisories.sh || rc=1
    bash .ci/gitleaks/test-sync.sh || rc=1
    bash .ci/test-no-mistakes-required.sh || rc=1
    bash .ci/test-instruction-path-truth.sh || rc=1
    python3 run/instruction-truth/path-truth.py . || rc=1
    bash .ci/test-instruction-size-limit.sh || rc=1
    python3 run/instruction-truth/size-limit.py . || rc=1
    bash .ci/test-watermark-scan-workflow.sh || rc=1
    # The bootstrap's lock controls, against fixture gates and caller repositories.
    bash .ci/test-lock.sh || rc=1
    bash .ci/test-upstream-owner-refs.sh || rc=1
    bash .ci/test-suite-coverage.sh || rc=1
    # The gate runner's selection, cache and fold, each case also run without its control.
    python3 .ci/test-gate-runner.py || rc=1
    # The shadow job's script: a failing row is recorded and never turns the job red.
    python3 .ci/test-gate-shadow.py || rc=1
    # The budget monitor's alarm and fix task, replayed over pointback's check step.
    python3 .ci/test-budget-monitor.py || rc=1
    # The protected surface of security design 7.2, and its row in shadow under the runner.
    python3 .ci/test-permissions-diff.py || rc=1
    # The gate-weakening guard and the record it reads from policy/relaxations.toml.
    python3 .ci/test-weakening.py || rc=1
    # The document rules, their shadow rows under the runner, and all but the budget on this tree.
    # The budget is not run here yet, though this tree's AGENTS.md now fits it.
    python3 .ci/test-docs-truth.py || rc=1
    python3 run/docs-truth.py owner . || rc=1
    python3 run/docs-truth.py commands . || rc=1
    python3 run/docs-truth.py emdash . || rc=1
    # The pull request text scan, and the guard that a run leaves no scratch behind.
    python3 .ci/test-pr-text.py || rc=1
    python3 .ci/test-scratch-guard.py || rc=1
    # The pinned-tool gates against stub tools, and their rows in shadow under the runner.
    # `--real`, with the pinned binaries, is not run here: README.md, "The tool rows".
    python3 .ci/test-tool-gates.py || rc=1
    # The UI rows (axe, Linux pixel baselines) against a stub browser, and their rows in shadow under the runner.
    # `--real`, with the pinned browser, is not run here: README.md, "The UI rows".
    python3 .ci/test-ui-gates.py || rc=1
    # The contract, migration and fact rows over a change, and their scoped shadow rows under the runner.
    python3 .ci/test-change-gates.py || rc=1
    # The slop rows (source-text assertions, symbol truth, the UI rules of ~/STANDARDS.md), and their scoped shadow rows under the runner.
    python3 .ci/test-slop-rules.py || rc=1
    # The fake rows (undeclared doubles, counterparts, freshness), and their scoped shadow rows under the runner.
    python3 .ci/test-fakes.py || rc=1
    # The mutation rows' decisions against a stub tool, and their scoped shadow row under the runner.
    python3 .ci/test-mutation.py || rc=1
    # The same fixtures put to cargo-mutants and Stryker, which ci.yml installs. Only CI runs it:
    # a bare checkout lacks the tools, and a skip on a missing tool would pass CI the same way.
    if [ -n "${CI:-}" ]; then
        python3 .ci/test-mutation.py --real || rc=1
    fi
    # The tool rows put to the pinned zizmor, actionlint and osv-scanner and an npm database,
    # which ci.yml installs. Only CI runs it, for the reason given above.
    if [ -n "${CI:-}" ]; then
        python3 .ci/test-tool-gates.py --real || rc=1
    fi
    # The review row's seats, budgets, verifier pass, router and veto against recorded model replies, and its rows folded in shadow.
    python3 .ci/test-review.py || rc=1
    # The seeded corpus runner against recorded replies of its live run, and the licence policy row folded in shadow.
    python3 .ci/test-corpus.py || rc=1
    python3 .ci/test-licence.py || rc=1
    # The role lenses' ablation eval against recorded replies of its second live run.
    python3 .ci/test-lens-eval.py || rc=1
    # The critic lens's seat and its eval against recorded replies of the eval's live run.
    python3 .ci/test-critic.py || rc=1
    python3 .ci/test-critic-eval.py || rc=1
    # The exposure and signature gates, through real pushes into throwaway repositories.
    bash system-maintenance/git-hooks/test-exposure-scan.sh || rc=1
    # The release-artifact scan, over fixture binaries planted at run time.
    bash system-maintenance/git-hooks/test-artifact-scan.sh || rc=1
    bash system-maintenance/git-hooks/test-signature-check.sh || rc=1
    bash system-maintenance/git-hooks/test-commit-msg.sh || rc=1
    return $rc
}

# --- python ------------------------------------------------------------------

check_python() {
    need uvx 'brew install uv' || return 1
    local rc=0
    uvx "ruff@${RUFF_VERSION}" check . || rc=1
    uvx "ruff@${RUFF_VERSION}" format --check . || rc=1
    return $rc
}

# --- secrets -----------------------------------------------------------------

# Tree and full history: a credential that was committed and later deleted is
# still published, so the tip alone is not enough.
#
# .gitleaks.toml here is the canonical copy the whole fleet runs; test-rules.sh
# is what stops a rule from silently stopping working, so it runs first.
check_secrets() {
    need gitleaks 'brew install gitleaks' || return 1
    # The watermark gate's inspector and its three writers. `need`ed rather than
    # skipped: the deployed hook refuses without exiftool, and a suite that
    # skipped a writer would certify what it never measured.
    need exiftool 'brew install exiftool' || return 1
    need qpdf 'brew install qpdf' || return 1
    need ffmpeg 'brew install ffmpeg' || return 1
    # ImageMagick 7 calls it `magick`, the 6 that ubuntu-24.04 installs `convert`.
    need_any 'brew install imagemagick' magick convert || return 1
    local rc=0
    # The fixture is generator output, and the generator is what shows every
    # value is computed and fails its provider's own format check.
    ./.ci/gitleaks/generate-fixtures.py --check || rc=1
    ./.ci/gitleaks/test-rules.sh || rc=1
    ./.ci/gitleaks/test-allowlists.py || rc=1
    ./.ci/gitleaks/test-pre-push.sh || rc=1
    # The machine-wide half: drives real pushes through a deployed ~/.git-hooks
    # in a throwaway HOME.
    ./system-maintenance/git-hooks/test-global-hooks.sh || rc=1
    python3 ./system-maintenance/git-hooks/test-watermark-scan.py || rc=1
    ./.ci/test-secret-scan.sh || rc=1
    # The lock bump and its rollback, judged by the real lock.sh and secret scan.
    python3 ./.ci/test-bump.py || rc=1
    gitleaks dir . --redact --no-banner --config .gitleaks.toml || rc=1
    gitleaks git . --redact --no-banner --config .gitleaks.toml || rc=1
    return $rc
}

# --- gates-proof ---------------------------------------------------------------

# Every gate is run on a red input it must refuse and a clean one it must pass,
# then neutered to prove the red verdict came from it. The cases run in
# parallel, so the whole suite costs seconds, but it needs every gate's tools.
check_gates-proof() {
    need gitleaks 'brew install gitleaks' || return 1
    need exiftool 'brew install exiftool' || return 1
    need yq 'brew install yq' || return 1
    need jq 'brew install jq' || return 1
    need git-lfs 'brew install git-lfs' || return 1
    local rc=0
    python3 .ci/gates-proof.py || rc=1
    bash .ci/test-gates-proof.sh || rc=1
    return $rc
}

# --- entry point -------------------------------------------------------------

run_suite() {
    case "$1" in
        shell | python | secrets | gates-proof) run "$1" "check_$1" ;;
        all)
            run shell check_shell
            run python check_python
            run secrets check_secrets
            run gates-proof check_gates-proof
            ;;
        *)
            printf 'unknown suite: %s (shell|python|secrets|gates-proof|all)\n' "$1" >&2
            exit 2
            ;;
    esac
}

if (($#)); then
    for suite in "$@"; do run_suite "$suite"; done
else
    run_suite all
fi

if ((${#failed[@]})); then
    printf '\n\033[31mfailed:\033[0m %s\n' "${failed[*]}"
    exit 1
fi
printf '\n\033[32mall checks passed\033[0m\n'
