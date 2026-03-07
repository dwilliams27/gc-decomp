"""SQLModel tables and session helpers for tracking decompilation progress."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, event, text as sa_text
from sqlalchemy import func as sa_func
from sqlmodel import Field, Session, SQLModel, create_engine, select

if TYPE_CHECKING:
    from decomp_agent.agent.loop import AgentResult
    from decomp_agent.melee.functions import FunctionInfo

log = logging.getLogger(__name__)

# Libraries that are SDK/runtime code, not game code. The agent doesn't have
# the right tooling (no .ctx files, different build setup) to decomp these.
EXCLUDED_LIBRARIES = frozenset({
    "thp",
    "Gekko runtime",
    "<unknown>",
})


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


class Run(SQLModel, table=True):
    """One agent session — targets 1 function (function-mode) or N functions (file-mode)."""
    id: int | None = Field(default=None, primary_key=True)
    source_file: str
    function_name: str | None = None  # NULL for file-mode
    session_id: str = ""
    file_mode: bool = False
    warm_start: bool = False
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0
    elapsed_seconds: float = 0.0
    iterations: int = 0
    model: str = ""
    termination_reason: str = ""
    error: str | None = None


class Attempt(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    function_id: int = Field(foreign_key="function.id")
    run_id: int | None = Field(default=None, foreign_key="run.id")  # NULL for legacy
    started_at: datetime
    completed_at: datetime | None = None
    matched: bool = False
    best_match_pct: float = 0.0
    before_match_pct: float = 0.0
    iterations: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    elapsed_seconds: float = 0.0
    termination_reason: str = ""
    final_code: str | None = None
    error: str | None = None
    model: str = ""
    reasoning_effort: str = ""
    match_history: str | None = None  # JSON: [[iteration, match_pct], ...]
    tool_counts: str | None = None  # JSON: {"tool_name": count, ...}
    cost: float = 0.0  # Dollar cost (legacy only; new records use Run.cost)
    warm_start: bool = False
    session_id: str = ""  # Legacy; new records use Run.session_id


def get_engine(db_path: Path | str) -> Engine:
    """Create a SQLite engine and ensure all tables exist."""
    url = f"sqlite:///{db_path}" if str(db_path) != ":memory:" else "sqlite://"
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    _migrate(engine)
    return engine


def _migrate(engine: Engine) -> None:
    """Add columns that may be missing from older databases."""
    migrations = [
        ("attempt", "model", "TEXT NOT NULL DEFAULT ''"),
        ("attempt", "reasoning_effort", "TEXT NOT NULL DEFAULT ''"),
        ("attempt", "match_history", "TEXT"),
        ("attempt", "tool_counts", "TEXT"),
        ("attempt", "cost", "REAL NOT NULL DEFAULT 0.0"),
        ("attempt", "warm_start", "BOOLEAN NOT NULL DEFAULT 0"),
        ("attempt", "session_id", "TEXT NOT NULL DEFAULT ''"),
        ("attempt", "run_id", "INTEGER"),
        ("attempt", "before_match_pct", "REAL NOT NULL DEFAULT 0.0"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            try:
                conn.execute(
                    sa_text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                )
            except Exception:
                pass  # Column already exists
        conn.commit()


def get_next_candidate(
    session: Session,
    *,
    max_size: int | None = None,
    strategy: str = "smallest_first",
) -> Function | None:
    """Pick the next function to work on.

    Skips functions that are matched, failed, skipped, or in_progress.
    """
    stmt = select(Function).where(
        Function.status.in_(["pending"]),  # type: ignore[attr-defined]
        Function.current_match_pct < 100.0,  # skip already-matched functions
        Function.library.notin_(EXCLUDED_LIBRARIES),  # type: ignore[attr-defined]
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


def record_run(
    session: Session,
    result: AgentResult,
    cost: float,
    *,
    function: Function | None = None,
    functions_by_name: dict[str, Function] | None = None,
    source_file: str = "",
) -> Run:
    """Create a Run + Attempt(s) from an AgentResult.

    Function-mode: pass ``function`` — creates 1 Run + 1 Attempt.
    File-mode: pass ``functions_by_name`` — creates 1 Run + N Attempts
    from ``result.function_deltas``.

    Session-level data (tokens, cost) lives on Run.
    Per-function outcomes live on Attempt.
    Updates Function statuses.
    """
    import json

    now = datetime.now(timezone.utc)

    # Determine source_file
    if function is not None:
        source_file = function.source_file
    elif not source_file:
        raise ValueError("record_run requires either function or source_file")

    run = Run(
        source_file=source_file,
        function_name=function.name if function is not None else None,
        session_id=result.session_id,
        file_mode=result.file_mode,
        warm_start=result.warm_start,
        started_at=now,
        completed_at=now,
        total_tokens=result.total_tokens,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cached_tokens=result.cached_tokens,
        cost=cost,
        elapsed_seconds=result.elapsed_seconds,
        iterations=result.iterations,
        model=result.model,
        termination_reason=result.termination_reason,
        error=result.error,
    )
    session.add(run)
    session.flush()  # get run.id

    if not result.file_mode:
        # Function-mode: 1 Attempt
        assert function is not None, "function-mode record_run requires function"
        attempt = Attempt(
            function_id=function.id,  # type: ignore[arg-type]
            run_id=run.id,
            started_at=now,
            completed_at=now,
            matched=result.matched,
            best_match_pct=result.best_match_percent,
            before_match_pct=function.current_match_pct,
            iterations=result.iterations,
            termination_reason=result.termination_reason,
            final_code=result.final_code,
            error=result.error,
            model=result.model,
            reasoning_effort=result.reasoning_effort,
            match_history=json.dumps(result.match_history) if result.match_history else None,
            tool_counts=json.dumps(result.tool_counts) if result.tool_counts else None,
            warm_start=result.warm_start,
            session_id=result.session_id,
        )
        session.add(attempt)

        function.attempts += 1
        if result.best_match_percent > function.current_match_pct:
            function.current_match_pct = result.best_match_percent
        function.updated_at = now
        session.add(function)
    else:
        # File-mode: 1 Attempt per function in function_deltas
        assert functions_by_name is not None, "file-mode record_run requires functions_by_name"
        for func_name, (before, after) in result.function_deltas.items():
            func = functions_by_name.get(func_name)
            if func is None:
                continue
            matched = func_name in result.newly_matched
            attempt = Attempt(
                function_id=func.id,  # type: ignore[arg-type]
                run_id=run.id,
                started_at=now,
                completed_at=now,
                matched=matched,
                best_match_pct=after,
                before_match_pct=before,
                termination_reason=result.termination_reason,
                model=result.model,
                warm_start=result.warm_start,
                session_id=result.session_id,
            )
            session.add(attempt)

            func.attempts += 1
            if after > func.current_match_pct:
                func.current_match_pct = after
            if matched:
                func.status = "matched"
                func.matched_at = now
            func.updated_at = now
            session.add(func)

    session.commit()
    session.refresh(run)
    return run


def record_attempt(
    session: Session,
    function: Function,
    result: AgentResult,
    cost: float,
) -> Attempt:
    """Create an Attempt record and update the Function from an AgentResult.

    Legacy wrapper — new code should use record_run(). This delegates to
    record_run() internally so all new records get a Run.
    """
    run = record_run(session, result, cost, function=function)

    # Return the Attempt that was created (there's exactly one for function-mode)
    stmt = (
        select(Attempt)
        .where(Attempt.run_id == run.id)
        .limit(1)
    )
    attempt = session.exec(stmt).one()
    return attempt


def get_best_attempt(session: Session, function_id: int) -> Attempt | None:
    """Return the attempt with the highest match % that has final_code.

    Only considers attempts with best_match_pct > 0 and non-null final_code.
    Returns None if no qualifying attempt exists.
    """
    stmt = (
        select(Attempt)
        .where(
            Attempt.function_id == function_id,
            Attempt.best_match_pct > 0,
            Attempt.final_code.isnot(None),  # type: ignore[union-attr]
        )
        .order_by(Attempt.best_match_pct.desc())  # type: ignore[union-attr]
        .limit(1)
    )
    return session.exec(stmt).first()


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


def get_candidate_batch(
    session: Session,
    *,
    limit: int = 50,
    max_size: int | None = None,
    strategy: str = "smallest_first",
    library: str | None = None,
    min_match: float | None = None,
    max_match: float | None = None,
    unique_files: bool = False,
) -> list[Function]:
    """Fetch multiple candidate functions matching the given filters.

    Same filtering logic as get_next_candidate but returns N results
    and supports additional library and match-percentage filters.

    If unique_files is True, returns at most one function per source file
    to maximize parallelism (avoids per-file lock serialization).
    """
    stmt = select(Function).where(
        Function.status.in_(["pending"]),  # type: ignore[attr-defined]
        Function.current_match_pct < 100.0,  # skip already-matched functions
        Function.library.notin_(EXCLUDED_LIBRARIES),  # type: ignore[attr-defined]
    )
    if max_size is not None:
        stmt = stmt.where(Function.size <= max_size)
    if library is not None:
        stmt = stmt.where(Function.library == library)
    if min_match is not None:
        stmt = stmt.where(Function.current_match_pct >= min_match)
    if max_match is not None:
        stmt = stmt.where(Function.current_match_pct <= max_match)

    if strategy == "smallest_first":
        stmt = stmt.order_by(Function.size, Function.address)  # type: ignore[arg-type]
    elif strategy == "best_match_first":
        stmt = stmt.order_by(Function.current_match_pct.desc(), Function.size)  # type: ignore[arg-type, attr-defined]
    else:
        stmt = stmt.order_by(Function.size, Function.address)  # type: ignore[arg-type]

    if not unique_files:
        stmt = stmt.limit(limit)
        return list(session.exec(stmt).all())

    # Fetch more than needed, then dedup by source file
    all_candidates = list(session.exec(stmt).all())
    seen_files: set[str] = set()
    result: list[Function] = []
    for func in all_candidates:
        if func.source_file not in seen_files:
            seen_files.add(func.source_file)
            result.append(func)
            if len(result) >= limit:
                break
    return result


def get_historical_avg_tokens(
    session: Session,
    size_range: tuple[int, int],
) -> float | None:
    """Return average total_tokens for attempts on functions within a size range.

    Returns None if no historical data is available.
    """
    low, high = size_range
    stmt = (
        select(sa_func.avg(Attempt.total_tokens))
        .join(Function, Attempt.function_id == Function.id)  # type: ignore[arg-type]
        .where(Function.size >= low, Function.size <= high)
    )
    result = session.exec(stmt).first()  # type: ignore[call-overload]
    if result is None or result == 0:
        return None
    return float(result)


def get_total_cost(session: Session) -> float:
    """Total cost across all runs and legacy attempts.

    New records: cost on Run. Legacy records (run_id IS NULL): cost on Attempt.
    """
    run_cost = session.exec(
        select(sa_func.coalesce(sa_func.sum(Run.cost), 0.0))
    ).one()
    legacy_cost = session.exec(
        select(sa_func.coalesce(sa_func.sum(Attempt.cost), 0.0))
        .where(Attempt.run_id.is_(None))  # type: ignore[union-attr]
    ).one()
    return float(run_cost) + float(legacy_cost)


def get_total_tokens(session: Session) -> int:
    """Total tokens across all runs and legacy attempts."""
    run_tokens = session.exec(
        select(sa_func.coalesce(sa_func.sum(Run.total_tokens), 0))
    ).one()
    legacy_tokens = session.exec(
        select(sa_func.coalesce(sa_func.sum(Attempt.total_tokens), 0))
        .where(Attempt.run_id.is_(None))  # type: ignore[union-attr]
    ).one()
    return int(run_tokens) + int(legacy_tokens)


def get_candidate_files(
    session: Session,
    *,
    limit: int = 50,
    library: str | None = None,
) -> list[str]:
    """Return distinct source files that have pending unmatched functions.

    Ordered by number of pending functions (most first) to maximize
    per-session productivity.
    """
    stmt = (
        select(Function.source_file, sa_func.count(Function.id).label("cnt"))
        .where(
            Function.status.in_(["pending"]),  # type: ignore[attr-defined]
            Function.current_match_pct < 100.0,
            Function.library.notin_(EXCLUDED_LIBRARIES),  # type: ignore[attr-defined]
        )
        .group_by(Function.source_file)
        .order_by(sa_func.count(Function.id).desc())  # type: ignore[arg-type]
        .limit(limit)
    )
    if library is not None:
        stmt = stmt.where(Function.library == library)

    rows = session.exec(stmt).all()
    return [row[0] for row in rows]


def get_functions_for_file(
    session: Session,
    source_file: str,
) -> dict[str, Function]:
    """Return all Function records for a source file, keyed by name."""
    stmt = select(Function).where(Function.source_file == source_file)
    return {f.name: f for f in session.exec(stmt).all()}
