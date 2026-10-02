#!/usr/bin/env bash
# Run in the Pod's Jupyter terminal. Clones this (public) repo and checks out ONE verified commit.
#   bash pod-bootstrap.sh <40-hex commit>
# If this script is not on the Pod yet, run the separate steps listed in docs/runpod-runbook.ja.md one at a time.
set -euo pipefail
sha="${1:-}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "usage: $0 <full 40-hex commit sha>" >&2; exit 2; }
dest="${QMC_OPS_DIR:-/workspace/qwen/ops}"
mkdir -p "$(dirname "$dest")"
[ -d "$dest/.git" ] || git clone "${QMC_OPS_REPO_URL:-https://github.com/moruku36/qwen-runpod-operations}" "$dest"
git -C "$dest" fetch -q origin
git -C "$dest" checkout -q --detach "$sha"
[ "$(git -C "$dest" rev-parse HEAD)" = "$sha" ] || { echo "HEAD does not match $sha" >&2; exit 1; }
[ -z "$(git -C "$dest" status --porcelain)" ] || { echo "working tree is not clean" >&2; exit 1; }
echo "OK: $dest at $sha. Open $dest/notebooks/Qwen-Q8-Chat-RunPod.ipynb"
