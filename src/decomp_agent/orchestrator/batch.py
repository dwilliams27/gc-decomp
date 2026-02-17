"""Batch mode: run agent on multiple candidates sequentially."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog
from rich.console import Console
from sqlalchemy import Engine
from sqlmodel import Session

from decomp_agent.config import Config
from decomp_agent.models.db import get_next_candidate
from decomp_agent.orchestrator.runner import run_function

log = structlog.get_logger()
console = Console()


@dataclass
class BatchResult:
    attempted: int = 0
    matched: int = 0
    failed: int = 0
    total_tokens: int = 0
    elapsed: float = 0.0
    errors: list[str] = field(default_factory=list)


def run_batch(
    config: Config,
    engine: Engine,
    *,
    limit: int = 50,
    max_size: int | None = None,
) -> BatchResult:
    """Run the agent on candidates until limit is reached or queue is empty."""
    start = time.monotonic()
    batch = BatchResult()
    max_attempts = config.orchestration.max_attempts_per_function

    log.info("batch_start", limit=limit, max_size=max_size)

    for i in range(limit):
        with Session(engine) as session:
            candidate = get_next_candidate(
                session,
                max_size=max_size,
                max_attempts=max_attempts,
            )
            if candidate is None:
                console.print("[yellow]No more candidates available.[/yellow]")
                break

            func_name = candidate.name
            func_size = candidate.size
            log.info("batch_function_start", index=i + 1, function=func_name, size=func_size)
            console.print(
                f"\n[bold][{i + 1}/{limit}][/bold] {func_name} "
                f"(size={func_size}, current={candidate.current_match_pct:.1f}%)"
            )

        # run_function manages its own sessions
        try:
            result = run_function(candidate, config, engine)
        except Exception as e:
            log.error("batch_function_error", function=func_name, error=str(e))
            batch.errors.append(f"{func_name}: {e}")
            batch.failed += 1
            batch.attempted += 1
            continue

        batch.attempted += 1
        batch.total_tokens += result.total_tokens

        if result.matched:
            batch.matched += 1
            console.print(f"  [green]MATCHED[/green] in {result.elapsed_seconds:.1f}s")
        elif result.error:
            batch.failed += 1
            console.print(f"  [red]ERROR[/red]: {result.error}")
        else:
            console.print(
                f"  [yellow]{result.termination_reason}[/yellow] "
                f"best={result.best_match_percent:.1f}% "
                f"iters={result.iterations} "
                f"({result.elapsed_seconds:.1f}s)"
            )

        # Running totals
        console.print(
            f"  Totals: {batch.matched} matched, {batch.failed} failed, "
            f"{batch.attempted} attempted, {batch.total_tokens:,} tokens"
        )

    batch.elapsed = time.monotonic() - start
    log.info(
        "batch_complete",
        attempted=batch.attempted,
        matched=batch.matched,
        failed=batch.failed,
        tokens=batch.total_tokens,
        elapsed=round(batch.elapsed, 1),
    )
    return batch
