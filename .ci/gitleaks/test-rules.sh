#!/usr/bin/env bash
# Exercise every rule the fleet config claims to have.
#
#   .ci/gitleaks/test-rules.sh
#
# Each fixture line is scanned from a temp directory, not from the repository,
# so the allowlist that hides the fixture file from the real scan does not also
# hide it from its own test. The last case scans the fixture in place and
# asserts the opposite: that the allowlist works.
#
# Fails closed. A missing gitleaks, an unreadable config or a gitleaks crash all
# exit nonzero rather than reporting a pass.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 2

CONFIG=".gitleaks.toml"
FIXTURE=".ci/gitleaks/fixtures/known-secrets.tsv"

command -v gitleaks >/dev/null 2>&1 || {
    printf 'test-rules: gitleaks not installed - install with: brew install gitleaks\n' >&2
    exit 2
}
[ -r "$CONFIG" ] || { printf 'test-rules: cannot read %s\n' "$CONFIG" >&2; exit 2; }
[ -r "$FIXTURE" ] || { printf 'test-rules: cannot read %s\n' "$FIXTURE" >&2; exit 2; }

TMP=$(mktemp -d) || exit 2
trap 'rm -rf "$TMP"' EXIT

# A rule id that fires on nothing is as broken as one that fires on everything,
# and only the fixture can tell the difference - so a fixture that reaches zero
# cases is itself a failure.
cases=0
failed=0

fail() {
    printf '\033[31mFAIL\033[0m %s\n' "$1" >&2
    failed=$((failed + 1))
}

while IFS=$'\t' read -r rule label line; do
    case "$rule" in '#'* | '') continue ;; esac
    [ -n "${line:-}" ] || { fail "$rule/$label: malformed fixture row"; continue; }
    cases=$((cases + 1))

    printf '%s\n' "$line" > "$TMP/case.txt"
    gitleaks dir "$TMP/case.txt" --config "$CONFIG" --no-banner --redact \
        --log-level error --report-format json --report-path "$TMP/case.json" >/dev/null 2>&1
    rc=$?
    # 0 = clean, 1 = findings. Anything else is gitleaks itself failing, which
    # must never read as "this rule is fine".
    if [ "$rc" -ne 0 ] && [ "$rc" -ne 1 ]; then
        fail "$label: gitleaks exited $rc (scan did not complete)"
        continue
    fi
    if ! grep -q "\"RuleID\": *\"$rule\"" "$TMP/case.json" 2>/dev/null; then
        got=$(sed -n 's/.*"RuleID": *"\([^"]*\)".*/\1/p' "$TMP/case.json" 2>/dev/null | sort -u | paste -sd, -)
        fail "$label: expected rule '$rule', got '${got:-no findings}'"
    fi
done < "$FIXTURE"

[ "$cases" -gt 0 ] || { printf 'test-rules: fixture yielded zero cases\n' >&2; exit 2; }

# The fixture must not trip the scanner it exists to test.
gitleaks dir "$FIXTURE" --config "$CONFIG" --no-banner --redact --log-level error >/dev/null 2>&1
case $? in
    0) ;;
    1) fail "fixture is not allowlisted: the real scan flags .ci/gitleaks/fixtures/" ;;
    *) fail "fixture allowlist check: gitleaks did not complete" ;;
esac

# ...and nothing else in that directory is. Scanned from inside a copy of the
# layout, because an absolute path matches no `^`-anchored allowlist.
mkdir -p "$TMP/tree/.ci/gitleaks/fixtures"
grep -v '^#' "$FIXTURE" | head -n 1 | cut -f3 > "$TMP/tree/.ci/gitleaks/fixtures/pasted.txt"
(cd "$TMP/tree" && gitleaks dir . --config "$OLDPWD/$CONFIG" --no-banner --redact --log-level error >/dev/null 2>&1)
case $? in
    1) ;;
    0) fail "a new file under .ci/gitleaks/fixtures/ is allowlisted: the entry is wider than its two fixtures" ;;
    *) fail "fixture directory check: gitleaks did not complete" ;;
esac

if [ "$failed" -ne 0 ]; then
    printf '\033[31m%d of %d rule checks failed\033[0m\n' "$failed" "$cases" >&2
    exit 1
fi
printf '\033[32mok\033[0m %d rules fired, the two fixture files allowlisted and nothing else beside them\n' "$cases"
