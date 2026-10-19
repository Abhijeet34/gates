#!/usr/bin/env bash
# Publish the client package. A failing dry run stops the release before anything ships.
set -euo pipefail

cd client
npm publish --dry-run
npm publish --provenance --access public
