#!/usr/bin/env bash
# Claude Codeクラウド環境に runpodctl と GPUなしチェック用の依存を用意する。
# 有料リソース・APIキー・SSH設定は扱わない。
set -euo pipefail

RUNPODCTL_VERSION="${RUNPODCTL_VERSION:-2.14.0}"
UPSTREAM_REPO="https://github.com/moruku36/qwen-multimodal-colab"
UPSTREAM_SHA="9e5ff82cf604dd3b0377de81b89e1f4aaa422947"
UPSTREAM_DIR="${UPSTREAM_DIR:-$HOME/qmc-upstream}"
VENV="${VENV:-$HOME/venv-qmc}"
base="https://github.com/runpod/runpodctl/releases/download/v${RUNPODCTL_VERSION}"
arc="runpodctl-linux-amd64.tar.gz"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

curl -fsSL -o "$tmp/$arc" "$base/$arc"
curl -fsSL -o "$tmp/sums" "$base/checksums_${RUNPODCTL_VERSION}_sha256.txt"
want="$(awk -v a="$arc" '$2==a{print $1}' "$tmp/sums")"
have="$(sha256sum "$tmp/$arc" | cut -d' ' -f1)"
[ -n "$want" ] && [ "$want" = "$have" ] || { echo "checksum mismatch" >&2; exit 1; }
tar xzf "$tmp/$arc" -C "$tmp" runpodctl
install -m 0755 "$tmp/runpodctl" /usr/local/bin/runpodctl
runpodctl version

[ -d "$UPSTREAM_DIR/.git" ] || GIT_LFS_SKIP_SMUDGE=1 git clone "$UPSTREAM_REPO" "$UPSTREAM_DIR"
git -C "$UPSTREAM_DIR" checkout -q "$UPSTREAM_SHA"
[ "$(git -C "$UPSTREAM_DIR" rev-parse HEAD)" = "$UPSTREAM_SHA" ]

uv venv -q "$VENV" --python 3.11
# shellcheck disable=SC1091
. "$VENV/bin/activate"
uv pip install -q -r "$UPSTREAM_DIR/requirements-chat-colab.txt" -r "$UPSTREAM_DIR/requirements-dev.txt"
uv pip check
(cd "$UPSTREAM_DIR" && python -m pytest -q)
