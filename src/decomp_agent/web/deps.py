"""Dependency injection for FastAPI — engine, config, broadcaster."""

from __future__ import annotations

from pathlib import Path
from typing import Generator

from sqlalchemy import Engine
from sqlmodel import Session

from decomp_agent.config import Config, load_config
from decomp_agent.models.db import get_engine


class AppState:
    """Shared application state — created once at startup."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config: Config = load_config(config_path)
        self.engine: Engine = get_engine(self.config.orchestration.db_path)
        # Lazy import — set after app creation in app.py
        self.broadcaster: object | None = None


# Module-level singleton set by create_app()
_state: AppState | None = None


def set_state(state: AppState) -> None:
    global _state
    _state = state


def get_state() -> AppState:
    if _state is None:
        raise RuntimeError("AppState not initialized — call create_app() first")
    return _state


def get_config() -> Config:
    return get_state().config


def get_engine_dep() -> Engine:
    return get_state().engine


def get_session() -> Generator[Session, None, None]:
    engine = get_state().engine
    with Session(engine) as session:
        yield session
