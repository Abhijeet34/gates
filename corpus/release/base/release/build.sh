#!/usr/bin/env bash
# Build the app archive and its bill of materials for one version.
set -euo pipefail

version="$(jq -r .version client/package.json)"
mkdir -p build
xcodebuild -scheme Notes -configuration Release archive -archivePath "build/Notes-$version.xcarchive"
syft dir:. -o spdx-json > "build/Notes-$version.spdx.json"
