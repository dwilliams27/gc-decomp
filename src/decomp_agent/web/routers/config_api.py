"""Configuration endpoint — exposes safe config values to the UI."""

from __future__ import annotations

from fastapi import APIRouter

from decomp_agent.web.deps import get_state

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
def get_config():
    """Return non-sensitive configuration values."""
    config = get_state().config
    return {
        "agent": {
            "model": config.agent.model,
            "max_iterations": config.agent.max_iterations,
            "max_tokens_per_attempt": config.agent.max_tokens_per_attempt,
        },
        "orchestration": {
            "db_path": str(config.orchestration.db_path),
            "batch_size": config.orchestration.batch_size,
            "default_workers": config.orchestration.default_workers,
            "default_budget": config.orchestration.default_budget,
            "max_function_size": config.orchestration.max_function_size,
        },
        "docker": {
            "enabled": config.docker.enabled,
        },
        "ghidra": {
            "enabled": config.ghidra.enabled,
        },
    }
