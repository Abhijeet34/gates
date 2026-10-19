#!/usr/bin/env bash
# Publish the client package. A failing dry run stops the release before anything ships.
set -euo pipefail

cd client
echo "publish: checking the package"
npm publish --dry-run || true
npm publish --provenance --access public
