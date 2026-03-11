#!/bin/sh
set -eu

CODEX_HOME_DIR="${HOME}/.codex"
CODEX_CONFIG_PATH="${CODEX_HOME_DIR}/config.toml"
CODEX_AUTH_DEST="${CODEX_HOME_DIR}/auth.json"
CODEX_AUTH_SEED="${CODEX_AUTH_SEED:-}"
CODEX_MODEL="${CODEX_MODEL:-}"
DECOMP_CONFIG_PATH="${DECOMP_CONFIG:-/app/config/container.toml}"

mkdir -p "${CODEX_HOME_DIR}"

if [ -n "${CODEX_AUTH_SEED}" ] && [ -f "${CODEX_AUTH_SEED}" ]; then
    cp "${CODEX_AUTH_SEED}" "${CODEX_AUTH_DEST}"
    chmod 600 "${CODEX_AUTH_DEST}"
fi

python3 - <<'PY' > "${CODEX_CONFIG_PATH}"
import os

from decomp_agent.orchestrator.codex_bootstrap import render_codex_config

print(
    render_codex_config(
        decomp_config_path=os.environ.get("DECOMP_CONFIG", "/app/config/container.toml"),
        model=os.environ.get("CODEX_MODEL") or None,
    ),
    end="",
)
PY

exec "$@"
