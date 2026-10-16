#!/usr/bin/env bash
# Install the gitleaks every scan here runs, checksum-verified. It is gates
# content rather than workflow text, so a rules change that needs a newer
# gitleaks moves with the lock that carries it.
set -euo pipefail
GITLEAKS_VERSION="8.30.1"
GITLEAKS_SHA256="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
cd "$(mktemp -d)"
url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
curl -fsSL "$url" -o gitleaks.tar.gz
echo "${GITLEAKS_SHA256}  gitleaks.tar.gz" | sha256sum --check --strict
tar -xzf gitleaks.tar.gz gitleaks
sudo install -m 0755 gitleaks /usr/local/bin/gitleaks
rm -f gitleaks gitleaks.tar.gz
