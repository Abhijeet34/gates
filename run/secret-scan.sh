#!/usr/bin/env bash
# The secret scan shared-secret-scan.yml runs from the gates checkout, in the
# caller's checkout: the canonical copies first, then the tree and the history.
set -euo pipefail
here="$(dirname "${BASH_SOURCE[0]}")/secret-scan"
bash -e "$here/verify.sh"
# Only gates carries the rule fixture, because only gates authors the rules;
# the copy check above ties every other repository to them.
if [ -x .ci/gitleaks/test-rules.sh ]; then
  ./.ci/gitleaks/test-rules.sh
else
  echo "no local rule fixture; the rules are gates' canonical copy, verified above"
fi
bash -e "$here/tree.sh"
bash -e "$here/history.sh"
