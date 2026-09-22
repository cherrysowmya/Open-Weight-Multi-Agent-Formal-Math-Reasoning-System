#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
hf_cli="$repo_root/.venv/bin/hf"
if [[ ! -x "$hf_cli" ]]; then
  echo "Install the project's MLX dependencies first: .venv/bin/pip install -e '.[mlx]'" >&2
  exit 1
fi

# Download only: no inference or second resident model. Uses the same HF cache
# as managed mlx_lm.server. Pass an immutable revision for reproducible setup.
# macOS ships Bash 3.2, where an empty positional-argument expansion can trip
# nounset. Expand optional arguments only when at least one was supplied.
if [[ $# -gt 0 ]]; then
  "$hf_cli" download mlx-community/DeepSeek-Prover-V2-7B-4bit "$@"
else
  "$hf_cli" download mlx-community/DeepSeek-Prover-V2-7B-4bit
fi
echo "Specialist weights cached. Enable with: solve <file.lean> --v5 on"
