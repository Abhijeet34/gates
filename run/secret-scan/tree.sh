#!/usr/bin/env bash
# Scan the caller's working tree by its rules, which verify.sh proved canonical.
# `gitleaks dir` fails closed on its own: it exits 1 with an FTL on a path it
# cannot walk, and verify.sh has already proved the checkout is populated. The
# history scan does not, hence its guard.
gitleaks dir . --config .gitleaks.toml --no-banner --redact --log-level warn
