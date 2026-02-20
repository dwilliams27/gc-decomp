"""Run the agent on a single function with DB lifecycle management."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import structlog
from sqlalchemy import Engine
from sqlmodel import Session

from decomp_agent.agent.loop import AgentResult, run_agent
from decomp_agent.config import Config
from decomp_agent.cost import calculate_cost
from decomp_agent.models.db import Function, record_attempt

log = structlog.get_logger()

# Per-source-file locks. When multiple workers target functions in the same
# file, they must serialize: concurrent write_function calls would corrupt
# each other (non-atomic read-modify-write), and the save/restore rollback
# would clobber a parallel worker's changes.
_file_locks: dict[str, threading.Lock] = {}
_file_locks_guard = threading.Lock()


def _get_file_lock(source_file: str) -> threading.Lock:
    """Get or create a lock for a source file path."""
    with _file_locks_guard:
        if source_file not in _file_locks:
            _file_locks[source_file] = threading.Lock()
        return _file_locks[source_file]


def run_function(
    function: Function,
    config: Config,
    engine: Engine,
    *,
    worker_label: str = "",
) -> AgentResult:
    """Run the agent on a single function, managing DB state throughout.

    1. Sets status to in_progress
    2. Acquires per-file lock (serializes concurrent work on same source file)
    3. Saves source file for rollback
    4. Calls run_agent
    5. Records the attempt
    6. Updates status based on outcome
    7. Reverts source file if function didn't match
    8. Returns AgentResult

    DB is always updated even if the agent crashes.
    """
    # Mark in_progress
    with Session(engine) as session:
        session.add(function)
        function.status = "in_progress"
        function.updated_at = datetime.now(timezone.utc)
        session.commit()
        # Capture values we need outside the session
        func_name = function.name
        source_file = function.source_file

    bound_log = log.bind(worker=worker_label) if worker_label else log
    bound_log.info(
        "function_start",
        function=func_name,
        attempt=function.attempts + 1,
    )

    # Serialize all work on the same source file to prevent concurrent
    # write_function corruption and save/restore race conditions.
    result: AgentResult | None = None
    saved_source: bytes | None = None
    file_lock = _get_file_lock(source_file)
    try:
        with file_lock:
            # Save source file before agent runs for rollback on failure
            src_path = config.melee.resolve_source_path(source_file)
            saved_source = src_path.read_bytes() if src_path.exists() else None

            # Run the agent
            try:
                result = run_agent(func_name, source_file, config, worker_label=worker_label)
            except Exception as e:
                log.error("agent_crash", function=func_name, error=str(e))
                result = AgentResult(
                    error=str(e),
                    termination_reason="agent_crash",
                )

            # Revert source file if function didn't match
            if not result.matched and saved_source is not None:
                src_path.write_bytes(saved_source)
                log.info(
                    "source_reverted",
                    function=func_name,
                    source_file=source_file,
                    reason=result.termination_reason,
                )

        # Record attempt and update status
        cost = calculate_cost(result, config.pricing)
        with Session(engine) as session:
            session.add(function)
            session.refresh(function)
            record_attempt(session, function, result, cost)

            if result.matched:
                function.status = "matched"
                function.matched_at = datetime.now(timezone.utc)
            else:
                function.status = "pending"

            function.updated_at = datetime.now(timezone.utc)
            session.commit()

            log.info(
                "function_complete",
                function=func_name,
                matched=result.matched,
                status=function.status,
                best_match=result.best_match_percent,
            )

        return result

    except (KeyboardInterrupt, SystemExit):
        # Reset status so the function isn't stuck as in_progress
        log.warning("interrupted", function=func_name)
        with Session(engine) as session:
            session.add(function)
            session.refresh(function)
            function.status = "pending"
            function.updated_at = datetime.now(timezone.utc)
            session.commit()

        # Revert source file if we had a backup
        if result is None or not result.matched:
            try:
                src_path = config.melee.resolve_source_path(source_file)
                if saved_source is not None:
                    src_path.write_bytes(saved_source)
            except Exception:
                pass

        raise
