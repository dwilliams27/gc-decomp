#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

exec decomp-agent \
  --config config/default.toml \
  campaign launch melee/mn/mnsnap.c \
  --orchestrator-provider claude \
  --worker-provider-policy claude \
  --max-active-workers 2 \
  --timeout-hours 8
