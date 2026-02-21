"""Batch start/cancel/status endpoints with background execution."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func as sa_func
from sqlmodel import Session, select

from decomp_agent.models.db import Attempt, Function
from decomp_agent.web.deps import get_state

router = APIRouter(prefix="/api/batch", tags=["batch"])


class BatchStartRequest(BaseModel):
    limit: int = 50
    max_size: int | None = None
    budget: float | None = None
    workers: int = 1
    strategy: str = "smallest_first"
    library: str | None = None
    min_match: float | None = None
    max_match: float | None = None
    max_tokens: int | None = None  # Override config.agent.max_tokens_per_attempt


class BatchStatus:
    """Tracks whether a batch is running and its parameters.

    Live progress comes from the DB, not from in-memory counters —
    the orchestrator updates Function.status and records Attempts
    as it works, so the DB is always the source of truth.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running: bool = False
        self.cancel_event: threading.Event = threading.Event()
        self.started_at_monotonic: float = 0.0
        self.started_at_utc: datetime | None = None
        self.params: dict[str, Any] = {}
        self._thread: threading.Thread | None = None

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def reset(self, params: dict) -> None:
        with self._lock:
            self.running = True
            self.cancel_event = threading.Event()
            self.started_at_monotonic = time.monotonic()
            self.started_at_utc = datetime.now(timezone.utc)
            self.params = params

    def finish(self) -> None:
        with self._lock:
            self.running = False


# Module-level singleton
_batch_status = BatchStatus()


def get_batch_status() -> BatchStatus:
    return _batch_status


@router.post("/start")
def start_batch(req: BatchStartRequest):
    """Start a batch run in the background."""
    status = get_batch_status()
    if status.running:
        raise HTTPException(status_code=409, detail="A batch is already running")

    state = get_state()
    params = req.model_dump()
    status.reset(params)

    def _run():
        import copy

        import structlog

        from decomp_agent.orchestrator.batch import run_batch

        log = structlog.get_logger()
        try:
            # Apply per-batch token budget override without mutating shared config
            config = state.config
            if req.max_tokens is not None:
                config = copy.deepcopy(state.config)
                config.agent.max_tokens_per_attempt = req.max_tokens

            run_batch(
                config,
                state.engine,
                limit=req.limit,
                max_size=req.max_size,
                workers=req.workers,
                budget=req.budget,
                strategy=req.strategy,
                library=req.library,
                min_match=req.min_match,
                max_match=req.max_match,
                auto_approve=True,  # No interactive prompts in web mode
                cancel_flag=status.cancel_event,
            )
        except Exception as e:
            log.error("batch_thread_error", error=str(e))
        finally:
            status.finish()

    thread = threading.Thread(target=_run, daemon=True, name="batch-runner")
    status._thread = thread
    thread.start()

    return {"status": "started", "params": params}


@router.get("/current")
def current_batch():
    """Get current batch status by querying the DB for live progress."""
    status = get_batch_status()

    with status._lock:
        running = status.running
        cancelled = status.cancelled
        started_at_utc = status.started_at_utc
        started_at_mono = status.started_at_monotonic
        params = dict(status.params)

    if not running and started_at_utc is None:
        return {"running": False}

    # Query DB for live progress since this batch started
    state = get_state()
    with Session(state.engine) as session:
        # Functions currently being worked on
        in_progress = session.exec(
            select(Function.name).where(Function.status == "in_progress")
        ).all()

        if started_at_utc is not None:
            # Attempts created since batch started
            batch_attempts = session.exec(
                select(Attempt).where(Attempt.started_at >= started_at_utc)
            ).all()

            attempted = len(batch_attempts)
            matched = sum(1 for a in batch_attempts if a.matched)
            failed = sum(1 for a in batch_attempts if not a.matched and a.completed_at is not None)
            total_cost = sum(a.cost for a in batch_attempts)
            total_tokens = sum(a.total_tokens for a in batch_attempts)

            # Build recent list with function names
            func_names: dict[int, str] = {}
            recent_attempts = sorted(batch_attempts, key=lambda a: a.id or 0)[-10:]
            if recent_attempts:
                fids = [a.function_id for a in recent_attempts]
                funcs = session.exec(
                    select(Function).where(Function.id.in_(fids))  # type: ignore[union-attr]
                ).all()
                func_names = {f.id: f.name for f in funcs}

            recent = [
                {
                    "function_name": func_names.get(a.function_id, f"#{a.function_id}"),
                    "matched": a.matched,
                    "best_match_pct": a.best_match_pct,
                    "termination_reason": a.termination_reason,
                    "cost": round(a.cost, 4),
                    "elapsed": round(a.elapsed_seconds, 1),
                }
                for a in recent_attempts
            ]
        else:
            attempted = matched = failed = total_tokens = 0
            total_cost = 0.0
            recent = []

    elapsed = time.monotonic() - started_at_mono if running else 0

    return {
        "running": running,
        "cancelled": cancelled,
        "elapsed": round(elapsed, 1),
        "params": params,
        "attempted": attempted,
        "matched": matched,
        "failed": failed,
        "total_cost": round(total_cost, 4),
        "total_tokens": total_tokens,
        "current_functions": list(in_progress),
        "recent_completed": recent,
    }


@router.post("/cancel")
def cancel_batch():
    """Cancel the running batch.

    Note: This sets a flag — the batch runner checks it between functions.
    Currently uses a simple cancellation signal; individual agent runs
    finish their current iteration before stopping.
    """
    status = get_batch_status()
    if not status.running:
        raise HTTPException(status_code=409, detail="No batch is running")
    status.cancel_event.set()
    return {"status": "cancelling"}
