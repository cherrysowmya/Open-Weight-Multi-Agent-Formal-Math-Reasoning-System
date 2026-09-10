#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
KIMINA_ROOT="${PROJECT_ROOT}/services/kimina-lean-server"
ELAN_ENV_FILE="${HOME}/.elan/env"

if [[ ! -f "${ELAN_ENV_FILE}" ]]; then
  echo "Elan environment not found at ${ELAN_ENV_FILE}. Run Kimina's setup.sh first." >&2
  exit 1
fi

if [[ ! -x "${KIMINA_ROOT}/.venv/bin/python" ]]; then
  echo "Kimina virtual environment not found at ${KIMINA_ROOT}/.venv." >&2
  exit 1
fi

if [[ ! -x "${KIMINA_ROOT}/repl/.lake/build/bin/repl" ]]; then
  echo "Kimina REPL is not built. Run services/kimina-lean-server/setup.sh first." >&2
  exit 1
fi

source "${ELAN_ENV_FILE}"
source "${KIMINA_ROOT}/.venv/bin/activate"
cd "${KIMINA_ROOT}"

# Kimina defaults to 0.0.0.0. Keep the verifier private to this machine unless
# the operator deliberately overrides the setting.
export LEAN_SERVER_HOST="${LEAN_SERVER_HOST:-127.0.0.1}"
exec python -m server
