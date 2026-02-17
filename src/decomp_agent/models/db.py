"""SQLModel tables and session helpers for tracking decompilation progress."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine
from sqlmodel import Field, Session, SQLModel, create_engine, select

if TYPE_CHECKING:
    from decomp_agent.agent.loop import AgentResult
    from decomp_agent.melee.functions import FunctionInfo

log = logging.getLogger(__name__)


class Function(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    address: int
    size: int
    source_file: str
    library: str
    initial_match_pct: float
    current_match_pct: float
    status: str = "pending"  # pending | in_progress | matched | failed | skipped
    attempts: int = 0
    matched_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Attempt(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    function_id: int = Field(foreign_key="function.id")
    started_at: datetime
    completed_at: datetime | None = None
    matched: bool = False
    best_match_pct: float = 0.0
    iterations: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    termination_reason: str = ""
    final_code: str | None = None
    error: str | None = None


def get_engine(db_path: Path | str) -> Engine:
    """Create a SQLite engine and ensure all tables exist."""
    url = f"sqlite:///{db_path}" if str(db_path) != ":memory:" else "sqlite://"
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    return engine


def get_next_candidate(
    session: Session,
    *,
    max_size: int | None = None,
    max_attempts: int = 3,
    strategy: str = "smallest_first",
) -> Function | None:
    """Pick the next function to work on.

    Skips functions that are matched, failed, skipped, or in_progress.
    Also skips functions that have reached max_attempts.
    """
    stmt = select(Function).where(
        Function.status.in_(["pending"]),  # type: ignore[attr-defined]
        Function.attempts < max_attempts,
    )
    if max_size is not None:
        stmt = stmt.where(Function.size <= max_size)

    if strategy == "smallest_first":
        stmt = stmt.order_by(Function.size, Function.address)  # type: ignore[arg-type]
    elif strategy == "best_match_first":
        stmt = stmt.order_by(Function.current_match_pct.desc(), Function.size)  # type: ignore[arg-type, attr-defined]
    else:
        stmt = stmt.order_by(Function.size, Function.address)  # type: ignore[arg-type]

    return session.exec(stmt).first()


def record_attempt(
    session: Session,
    function: Function,
    result: AgentResult,
) -> Attempt:
    """Create an Attempt record and update the Function from an AgentResult."""
    now = datetime.now(timezone.utc)
    attempt = Attempt(
        function_id=function.id,  # type: ignore[arg-type]
        started_at=now,
        completed_at=now,
        matched=result.matched,
        best_match_pct=result.best_match_percent,
        iterations=result.iterations,
        total_tokens=result.total_tokens,
        elapsed_seconds=result.elapsed_seconds,
        termination_reason=result.termination_reason,
        final_code=result.final_code,
        error=result.error,
    )
    session.add(attempt)

    function.attempts += 1
    if result.best_match_percent > function.current_match_pct:
        function.current_match_pct = result.best_match_percent
    function.updated_at = now

    session.add(function)
    session.commit()
    session.refresh(attempt)
    return attempt


def sync_from_report(session: Session, functions: list[FunctionInfo]) -> int:
    """Upsert functions from the melee report into the database.

    Returns the number of new functions inserted.
    """
    inserted = 0
    for fi in functions:
        existing = session.exec(
            select(Function).where(Function.name == fi.name)
        ).first()
        if existing is None:
            func = Function(
                name=fi.name,
                address=fi.address,
                size=fi.size,
                source_file=fi.source_file,
                library=fi.library,
                initial_match_pct=fi.fuzzy_match_percent,
                current_match_pct=fi.fuzzy_match_percent,
            )
            session.add(func)
            inserted += 1
        else:
            # Update match percentage from latest report
            if fi.fuzzy_match_percent != existing.initial_match_pct:
                existing.initial_match_pct = fi.fuzzy_match_percent
            if fi.fuzzy_match_percent > existing.current_match_pct:
                existing.current_match_pct = fi.fuzzy_match_percent
            if fi.fuzzy_match_percent == 100.0 and existing.status == "pending":
                existing.status = "matched"
                existing.matched_at = datetime.now(timezone.utc)
            existing.updated_at = datetime.now(timezone.utc)
            session.add(existing)
    session.commit()
    return inserted
