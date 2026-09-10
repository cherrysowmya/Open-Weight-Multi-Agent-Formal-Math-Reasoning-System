#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_path="$repo_root/services/lean-lsp-mcp/.venv"

python_bin="${PYTHON313:-}"
if [[ -z "$python_bin" ]]; then
  python_bin="$(command -v python3.13 || true)"
fi
if [[ -z "$python_bin" ]]; then
  echo "Python 3.13 is required. Install it or set PYTHON313." >&2
  exit 1
fi

mkdir -p "$(dirname "$venv_path")"
"$python_bin" -m venv "$venv_path"
"$venv_path/bin/python" -m pip install "lean-lsp-mcp==0.14.1"
echo "Installed Lean-LSP-MCP 0.14.1 at $venv_path"
