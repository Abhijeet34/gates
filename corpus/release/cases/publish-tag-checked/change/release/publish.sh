#!/usr/bin/env bash
# Publish the client package. A failing dry run stops the release before anything ships.
set -euo pipefail

cd client
tag="$(git describe --tags --exact-match)"
[[ "$tag" == "v$(jq -r .version package.json)" ]] || { echo "publish: tag $tag is not the package version" >&2; exit 1; }
npm publish --dry-run
npm publish --provenance --access public
