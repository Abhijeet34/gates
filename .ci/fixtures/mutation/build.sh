#!/usr/bin/env bash
# Build a fixture repository for run/mutation.py in the current directory.
#
#   build.sh [--stub] rust|typescript STEP...
#
# Commits the language's base/ tree, then each STEP directory over it in turn, and
# tags the commit before the last step `base`, so `--base base` judges the last step.
# --stub installs stub-tool.py as the tool, untracked: `.stub/cargo` for Rust, to put
# on PATH, and the package's node_modules/.bin/stryker for TypeScript.
# The manifests are written here rather than kept beside the sources, so this
# repository holds no package manifest for the dependency graph to read.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
stub=
[[ "$1" == --stub ]] && stub=1 && shift
lang="$1"
shift

git init -q --template= -b main
case "$lang" in
    rust)
        printf '[package]\nname = "mutfix"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n' > Cargo.toml
        printf 'target\nmutants.out*\n' > .gitignore
        ;;
    typescript)
        cat > package.json <<'EOF'
{
  "name": "mutfix",
  "private": true,
  "type": "module",
  "scripts": { "test": "vitest run" },
  "devDependencies": {
    "@stryker-mutator/core": "10.0.0",
    "@stryker-mutator/vitest-runner": "10.0.0",
    "vitest": "3.2.7"
  }
}
EOF
        printf 'node_modules\nreports\n.stryker-tmp\n' > .gitignore
        ;;
    *)
        echo "build.sh: unknown language $lang" >&2
        exit 2
        ;;
esac
cp -R "$here/$lang/base/." .
git add -A
git commit -qm base
while (($#)); do
    (($# == 1)) && git tag base
    cp -R "$here/$lang/$1/." .
    git add -A
    git commit -qm "$1"
    shift
done

[[ -n "$stub" ]] || exit 0
mkdir -p .git/info
echo .stub >> .git/info/exclude
if [[ "$lang" == rust ]]; then
    mkdir .stub
    cp "$here/stub-tool.py" .stub/cargo
else
    mkdir -p node_modules/.bin node_modules/@stryker-mutator/core node_modules/@stryker-mutator/vitest-runner
    cp "$here/stub-tool.py" node_modules/.bin/stryker
    echo '{"version": "10.0.0"}' > node_modules/@stryker-mutator/core/package.json
    echo '{"version": "10.0.0"}' > node_modules/@stryker-mutator/vitest-runner/package.json
fi
