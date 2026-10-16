#!/usr/bin/env bash
# Regression tests for run/instruction-truth/path-truth.py, the check
# .github/workflows/shared-instruction-truth.yml runs.
#
# Each case writes one instruction file into an empty tree and states whether the
# check must refuse it. The two lines the check was written against, a path that
# a repository's own tree no longer holds and a synced file credited to
# automation after gates took it over, are the first two red cases.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

CHECK=$PWD/run/instruction-truth/path-truth.py

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

failed=0

# expect <name> <0|1> <instruction text> [finding]: build a tree holding
# src/real.sh, .gitleaks.toml and the text as AGENTS.md, then judge the verdict and the finding.
expect() {
    local name=$1 want=$2 text=$3 finding=${4:-} dir out rc
    dir=$(mktemp -d "$workdir/case.XXXXXX")
    mkdir -p "$dir/src/deep"
    : >"$dir/src/real.sh"
    : >"$dir/src/deep/tools.tsv"
    : >"$dir/.gitleaks.toml"
    printf '%s\n' "$text" >"$dir/AGENTS.md"
    out=$(python3 "$CHECK" "$dir" 2>&1)
    rc=$?
    if [[ $rc -ne $want ]]; then
        printf 'FAIL %s: exit %s, wanted %s\n%s\n' "$name" "$rc" "$want" "$out"
        failed=1
    elif [[ -n $finding && $out != *"$finding"* ]]; then
        printf 'FAIL %s: output does not name %s\n%s\n' "$name" "$finding" "$out"
        failed=1
    else
        printf 'ok   %s\n' "$name"
    fi
}

expect 'a path the tree lacks is refused' 1 'Run `.ci/gitleaks/sync.sh` after a change.' 'AGENTS.md:1: `.ci/gitleaks/sync.sh` does not exist'
expect 'a synced file credited to automation is refused' 1 '`.gitleaks.toml` is synced from automation.' 'is synced from gates'
expect 'a synced file credited to gates is accepted' 0 'The copy is gates-owned; edit `.gitleaks.toml` there.'
expect 'a real path is accepted' 0 'See `src/real.sh`.'
expect 'a path with a line suffix is judged without it' 0 'See `src/real.sh:12-20`.'
expect 'a path cited by its trailing segments is accepted' 0 'Read `deep/tools.tsv` and `tools.tsv`.'
expect 'a marked absent path is accepted' 0 'Callers carry `.github/gates.lock` (absent here).'
expect 'a marker on another span does not excuse this one' 1 'Callers carry `a/b.lock` (absent) and `c/d.lock`.' '`c/d.lock`'
expect 'a fenced block is not judged' 0 $'```\nsh missing/thing.sh\n```'
expect 'a URL, a home path and a glob are not judged' 0 'See `https://x.test/a/b.md`, `~/notes/a.md` and `src/*.sh`.'
expect 'an owner/repo, a branch and a bare word are not judged' 0 'Push `feat/branch` to `owner/repo`, then `main`.'
expect 'an extension alone is not a path' 0 'Files ending `.mjs` count.'
expect 'build output is not judged' 0 'The bundle lands in `target/debug/app` and `node_modules/x/y.js`.'

dir=$(mktemp -d "$workdir/nested.XXXXXX")
mkdir -p "$dir/apps/desktop"
: >"$dir/apps/desktop/local.ts"
printf 'See `local.ts` and `gone.ts`.\n' >"$dir/apps/desktop/CLAUDE.md"
out=$(python3 "$CHECK" "$dir" 2>&1)
if [[ $? -eq 1 && $out == *'apps/desktop/CLAUDE.md:1: `gone.ts`'* && $out != *'`local.ts` does'* ]]; then
    printf 'ok   %s\n' 'a nested CLAUDE.md is judged and named by its path'
else
    printf 'FAIL a nested CLAUDE.md is judged and named by its path\n%s\n' "$out"
    failed=1
fi

dir=$(mktemp -d "$workdir/empty.XXXXXX")
if python3 "$CHECK" "$dir" >/dev/null 2>&1; then
    printf 'ok   %s\n' 'a tree with no instruction file passes'
else
    printf 'FAIL a tree with no instruction file passes\n'
    failed=1
fi

exit "$failed"
