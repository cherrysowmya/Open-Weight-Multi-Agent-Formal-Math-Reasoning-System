#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
runner="$repo_root/.venv/bin/local-lean-agent"
if [[ ! -x "$runner" ]]; then
  echo "Install the project in .venv before running the benchmark." >&2
  exit 1
fi
"$runner" --config config/local.toml intro-logic-prepare
if [[ $# -gt 0 ]]; then
  exec "$runner" --config config/local.toml intro-logic-run "$@"
else
  exec "$runner" --config config/local.toml intro-logic-run
fi
