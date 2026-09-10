#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service_root="$repo_root/services/lean-explore"
venv_path="$service_root/.venv"
cache_path="$service_root/cache"
hf_cache_path="$service_root/huggingface"
lean_explore_version="1.3.0"
lean_explore_data_version="20260714_172516"

python_bin="${PYTHON313:-}"
if [[ -z "$python_bin" ]]; then
  python_bin="$(command -v python3.13 || true)"
fi
if [[ -z "$python_bin" ]]; then
  echo "Python 3.13 is required. Install it or set PYTHON313." >&2
  exit 1
fi

mkdir -p "$service_root" "$cache_path" "$hf_cache_path"
"$python_bin" -m venv "$venv_path"
"$venv_path/bin/python" -m pip install "lean-explore[local]==$lean_explore_version"

export LEAN_EXPLORE_CACHE_DIR="$cache_path"
export HF_HOME="$hf_cache_path"
export LEAN_EXPLORE_EMBEDDING_BATCH_SIZE=1
export LEAN_EXPLORE_RERANKER_BATCH_SIZE=1
"$venv_path/bin/lean-explore" data fetch --version "$lean_explore_data_version"

# Fetch only the embedding model needed by the rerank_top=0 baseline. Runtime
# MCP processes use offline mode; no model download or API lookup during search.
"$venv_path/bin/python" -c 'from huggingface_hub import snapshot_download; snapshot_download("Qwen/Qwen3-Embedding-0.6B", ignore_patterns=["onnx/*", "openvino/*"])'

echo "Installed LeanExplore $lean_explore_version with data $lean_explore_data_version at $service_root"
