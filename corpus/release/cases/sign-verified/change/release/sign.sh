#!/usr/bin/env bash
# Sign one release artefact. Only an artefact an approved release task built is signed:
# its digest must be listed in release/approved.json.
set -euo pipefail

artefact="$1"
manifest="release/approved.json"

digest="$(shasum -a 256 "$artefact" | cut -d' ' -f1)"
if ! jq -e --arg d "$digest" '.approved[] | select(.sha256 == $d)' "$manifest" >/dev/null; then
    echo "sign: $artefact ($digest) is not from an approved release" >&2
    exit 1
fi

codesign --sign "Developer ID Application" --options runtime \
    --entitlements release/entitlements.plist --timestamp "$artefact"

codesign --verify --strict --verbose=2 "$artefact"
