#!/usr/bin/env bash
# Tarball of the files a Pod needs (no secrets, no models). Upload it in Jupyter if the repo is not cloneable there.
set -euo pipefail
cd "$(dirname "$0")/.."
out="${1:-pod-bundle.tar.gz}"
tar czf "$out" --exclude='__pycache__' qmc_runpod notebooks requirements-runpod.in requirements-runpod.lock.txt baseline.json
sha256sum "$out"
