"""Run the agent on a single function with DB lifecycle management."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import Engine
from sqlmodel import Session

from decomp_agent.agent.loop import AgentResult, run_agent
from decomp_agent.config import Config
from decomp_agent.models.db import Function, record_attempt

log = logging.getLogger(__name__)


def run_function(function: Function, config: Config, engine: Engine) -> AgentResult:
    """Run the agent on a single function, managing DB state throughout.

    1. Sets status to in_progress
    2. Calls run_agent
    3. Records the attempt
    4. Updates status based on outcome
    5. Returns AgentResult

    DB is always updated even if the agent crashes.
    """
    max_attempts = config.orchestration.max_attempts_per_function

    # Mark in_progress
    with Session(engine) as session:
        session.add(function)
        function.status = "in_progress"
        function.updated_at = datetime.now(timezone.utc)
        session.commit()
        # Capture values we need outside the session
        func_name = function.name
        source_file = function.source_file

    # Run the agent
    try:
        result = run_agent(func_name, source_file, config)
    except Exception as e:
        log.error("Agent crashed on %s: %s", func_name, e)
        result = AgentResult(
            error=str(e),
            termination_reason="agent_crash",
        )

    # Record attempt and update status
    with Session(engine) as session:
        session.add(function)
        session.refresh(function)
        record_attempt(session, function, result)

        if result.matched:
            function.status = "matched"
            function.matched_at = datetime.now(timezone.utc)
        elif result.error and function.attempts >= max_attempts:
            function.status = "failed"
        elif function.attempts >= max_attempts:
            function.status = "failed"
        else:
            function.status = "pending"

        function.updated_at = datetime.now(timezone.utc)
        session.commit()

    return result
