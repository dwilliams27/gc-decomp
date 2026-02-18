"""Batch mode: run agent on candidates with parallelism and budget control."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import click
import structlog
from rich.console import Console
from rich.table import Table
from sqlalchemy import Engine
from sqlmodel import Session

from decomp_agent.config import Config
from decomp_agent.cost import calculate_cost, estimate_batch_cost
from decomp_agent.models.db import Function, get_candidate_batch
from decomp_agent.orchestrator.runner import run_function

log = structlog.get_logger()
console = Console()


@dataclass
class FunctionResult:
    name: str
    matched: bool = False
    best_match_pct: float = 0.0
    tokens: int = 0
    cost: float = 0.0
    elapsed: float = 0.0
    error: str | None = None
    termination_reason: str = ""


@dataclass
class BatchResult:
    attempted: int = 0
    matched: int = 0
    failed: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    elapsed: float = 0.0
    errors: list[str] = field(default_factory=list)
    results: list[FunctionResult] = field(default_factory=list)


def _run_one(
    function: Function,
    config: Config,
    engine: Engine,
    cost_lock: threading.Lock,
    batch: BatchResult,
    budget: float | None,
    index: int,
    total: int,
) -> FunctionResult:
    """Run the agent on one function, updating shared batch state."""
    func_name = function.name
    func_size = function.size

    # Check budget before starting
    if budget is not None:
        with cost_lock:
            if batch.total_cost >= budget:
                return FunctionResult(
                    name=func_name,
                    error="budget_exceeded",
                    termination_reason="budget_exceeded",
                )

    log.info("batch_function_start", index=index, function=func_name, size=func_size)
    console.print(
        f"\n[bold][{index}/{total}][/bold] {func_name} "
        f"(size={func_size}, current={function.current_match_pct:.1f}%)"
    )

    try:
        result = run_function(function, config, engine)
    except Exception as e:
        log.error("batch_function_error", function=func_name, error=str(e))
        fr = FunctionResult(name=func_name, error=str(e), termination_reason="exception")
        with cost_lock:
            batch.failed += 1
            batch.attempted += 1
            batch.errors.append(f"{func_name}: {e}")
            batch.results.append(fr)
        return fr

    cost = calculate_cost(result, config.pricing)
    fr = FunctionResult(
        name=func_name,
        matched=result.matched,
        best_match_pct=result.best_match_percent,
        tokens=result.total_tokens,
        cost=cost,
        elapsed=result.elapsed_seconds,
        error=result.error,
        termination_reason=result.termination_reason,
    )

    with cost_lock:
        batch.attempted += 1
        batch.total_tokens += result.total_tokens
        batch.total_cost += cost
        batch.results.append(fr)

        if result.matched:
            batch.matched += 1
            console.print(f"  [green]MATCHED[/green] in {result.elapsed_seconds:.1f}s (${cost:.4f})")
        elif result.error:
            batch.failed += 1
            console.print(f"  [red]ERROR[/red]: {result.error}")
        else:
            console.print(
                f"  [yellow]{result.termination_reason}[/yellow] "
                f"best={result.best_match_percent:.1f}% "
                f"iters={result.iterations} "
                f"(${cost:.4f}, {result.elapsed_seconds:.1f}s)"
            )

        console.print(
            f"  Totals: {batch.matched} matched, {batch.failed} failed, "
            f"{batch.attempted} attempted, ${batch.total_cost:.4f}"
        )

    return fr


def run_batch(
    config: Config,
    engine: Engine,
    *,
    limit: int = 50,
    max_size: int | None = None,
    workers: int = 1,
    budget: float | None = None,
    strategy: str = "smallest_first",
    library: str | None = None,
    min_match: float | None = None,
    max_match: float | None = None,
    auto_approve: bool = False,
) -> BatchResult:
    """Run the agent on candidates with parallelism and budget control.

    1. Fetch candidates via get_candidate_batch()
    2. Estimate costs and display preview table
    3. Prompt for confirmation (unless auto_approve)
    4. Execute with ThreadPoolExecutor
    5. Track budget, cancel remaining on overspend
    """
    start = time.monotonic()
    batch = BatchResult()
    max_attempts = config.orchestration.max_attempts_per_function

    log.info(
        "batch_start",
        limit=limit,
        max_size=max_size,
        workers=workers,
        budget=budget,
        strategy=strategy,
        library=library,
    )

    # 1. Fetch candidates
    with Session(engine) as session:
        candidates = get_candidate_batch(
            session,
            limit=limit,
            max_size=max_size,
            max_attempts=max_attempts,
            strategy=strategy,
            library=library,
            min_match=min_match,
            max_match=max_match,
        )

        if not candidates:
            console.print("[yellow]No candidates match the given filters.[/yellow]")
            batch.elapsed = time.monotonic() - start
            return batch

        # 2. Estimate costs
        estimated_cost = estimate_batch_cost(candidates, session, config.pricing)

    # 3. Display preview table
    table = Table(title="Batch Preview")
    table.add_column("Name", style="bold")
    table.add_column("Source File")
    table.add_column("Size", justify="right")
    table.add_column("Match %", justify="right")

    for c in candidates:
        table.add_row(
            c.name,
            c.source_file,
            str(c.size),
            f"{c.current_match_pct:.1f}%",
        )

    console.print(table)
    console.print(
        f"\n[bold]{len(candidates)}[/bold] functions, "
        f"estimated cost: [bold]${estimated_cost:.4f}[/bold]"
        + (f", budget: [bold]${budget:.4f}[/bold]" if budget is not None else "")
    )

    # 4. Confirm
    if not auto_approve:
        if not click.confirm("Proceed?"):
            console.print("[yellow]Aborted.[/yellow]")
            batch.elapsed = time.monotonic() - start
            return batch

    # 5. Execute
    cost_lock = threading.Lock()
    total = len(candidates)

    if workers <= 1:
        # Sequential execution
        for i, candidate in enumerate(candidates, 1):
            # Check budget
            if budget is not None and batch.total_cost >= budget:
                console.print(f"[red]Budget exceeded (${batch.total_cost:.4f} >= ${budget:.4f}). Stopping.[/red]")
                break
            _run_one(candidate, config, engine, cost_lock, batch, budget, i, total)
    else:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for i, candidate in enumerate(candidates, 1):
                future = executor.submit(
                    _run_one, candidate, config, engine, cost_lock, batch, budget, i, total
                )
                futures[future] = candidate.name

            for future in as_completed(futures):
                fr = future.result()
                # Check budget after each completion
                if budget is not None and batch.total_cost >= budget:
                    console.print(
                        f"[red]Budget exceeded (${batch.total_cost:.4f} >= ${budget:.4f}). "
                        f"Remaining futures will check budget before starting.[/red]"
                    )

    batch.elapsed = time.monotonic() - start
    log.info(
        "batch_complete",
        attempted=batch.attempted,
        matched=batch.matched,
        failed=batch.failed,
        tokens=batch.total_tokens,
        cost=round(batch.total_cost, 4),
        elapsed=round(batch.elapsed, 1),
    )
    return batch
