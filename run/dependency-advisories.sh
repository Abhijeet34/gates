#!/usr/bin/env bash
# The advisory gate shared-dependency-advisories.yml runs from the gates
# checkout: read the alerts, on a pull request read its dependency compare,
# then judge both against the caller's deferral list.
set -euo pipefail
here="$(dirname "${BASH_SOURCE[0]}")/dependency-advisories"
bash -e "$here/alerts.sh"
if [ "${EVENT_NAME:-}" = pull_request ]; then
  bash -e "$here/compare.sh"
fi
bash -e "$here/judge.sh"
