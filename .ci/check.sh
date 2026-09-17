#!/usr/bin/env bash
# Repo-wide checks. CI runs these exact commands, so there is exactly one
# definition of "clean".
#
#   .ci/check.sh [suite ...]    suites: shell python secrets all
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
    # against the extracted step body or a throwaway repository.
    bash .ci/test-checks-verdict.sh || rc=1
    bash .ci/test-guard-generated-files.sh || rc=1
    bash .ci/test-dependency-advisories.sh || rc=1
    bash .ci/gitleaks/test-sync.sh || rc=1
    bash .ci/test-no-mistakes-required.sh || rc=1
    bash .ci/test-watermark-scan-workflow.sh || rc=1
    bash .ci/test-upstream-owner-refs.sh || rc=1
    bash .ci/test-suite-coverage.sh || rc=1
    # The exposure gate, through real pushes into throwaway repositories.
    bash system-maintenance/git-hooks/test-exposure-scan.sh || rc=1
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
    gitleaks dir . --redact --no-banner --config .gitleaks.toml || rc=1
    gitleaks git . --redact --no-banner --config .gitleaks.toml || rc=1
    return $rc
}

# --- entry point -------------------------------------------------------------

run_suite() {
    case "$1" in
        shell | python | secrets) run "$1" "check_$1" ;;
        all)
            run shell check_shell
            run python check_python
            run secrets check_secrets
            ;;
        *)
            printf 'unknown suite: %s (shell|python|secrets|all)\n' "$1" >&2
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
