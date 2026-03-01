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
from decomp_agent.models.db import Function, get_best_attempt, record_attempt
from decomp_agent.tools.build import check_match

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


def _auto_commit_match(
    func_name: str,
    source_file: str,
    config: Config,
    bound_log,
) -> None:
    """Git-commit the matched function in the melee repo.

    Stages only the changed source file(s) and commits with a standard
    message. Failures are logged but never raised — a commit failure
    should not break the batch run.
    """
    import subprocess

    repo_path = str(config.melee.repo_path)
    src_path = config.melee.resolve_source_path(source_file)

    # Also check for a corresponding header change
    header_path = src_path.with_suffix(".h")
    files_to_add = [str(src_path)]
    if header_path.exists():
        # Only add if it has unstaged changes
        try:
            ret = subprocess.run(
                ["git", "diff", "--name-only", "--", str(header_path)],
                capture_output=True, text=True, cwd=repo_path,
            )
            if header_path.name in (ret.stdout or ""):
                files_to_add.append(str(header_path))
        except Exception:
            pass

    try:
        subprocess.run(
            ["git", "add"] + files_to_add,
            check=True, capture_output=True, text=True, cwd=repo_path,
        )
        subprocess.run(
            ["git", "commit", "-m", f"Match {func_name} in {source_file}"],
            check=True, capture_output=True, text=True, cwd=repo_path,
        )
        bound_log.info(
            "auto_commit",
            function=func_name,
            source_file=source_file,
        )
    except subprocess.CalledProcessError as e:
        bound_log.warning(
            "auto_commit_failed",
            function=func_name,
            error=e.stderr.strip() if e.stderr else str(e),
        )
    except Exception as e:
        bound_log.warning(
            "auto_commit_failed",
            function=func_name,
            error=str(e),
        )


def run_function(
    function: Function,
    config: Config,
    engine: Engine,
    *,
    worker_label: str = "",
    warm_start: bool = False,
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

            # Capture baseline match percentages for collateral damage guard
            baseline: dict[str, float] | None = None
            try:
                baseline_result = check_match(source_file, config)
                if baseline_result.success:
                    baseline = {
                        f.name: f.fuzzy_match_percent
                        for f in baseline_result.functions
                    }
                    bound_log.info(
                        "baseline_captured",
                        function=func_name,
                        num_functions=len(baseline),
                    )
                else:
                    bound_log.warning(
                        "baseline_compile_failed",
                        function=func_name,
                        error=baseline_result.error,
                    )
            except Exception as e:
                bound_log.warning(
                    "baseline_capture_error",
                    function=func_name,
                    error=str(e),
                )

            # Look up best prior attempt for warm start
            prior_best_code: str | None = None
            prior_match_pct: float = 0
            if warm_start and function.attempts > 0:
                with Session(engine) as session:
                    best = get_best_attempt(session, function.id)  # type: ignore[arg-type]
                    if best is not None:
                        prior_best_code = best.final_code
                        prior_match_pct = best.best_match_pct
                        bound_log.info(
                            "warm_start",
                            prior_match=prior_match_pct,
                        )

            # Run the agent
            try:
                if config.claude_code.enabled:
                    from decomp_agent.orchestrator.headless import run_headless

                    result = run_headless(
                        func_name,
                        source_file,
                        config,
                        worker_label=worker_label,
                        prior_best_code=prior_best_code,
                        prior_match_pct=prior_match_pct,
                    )
                else:
                    result = run_agent(
                        func_name,
                        source_file,
                        config,
                        worker_label=worker_label,
                        prior_best_code=prior_best_code,
                        prior_match_pct=prior_match_pct,
                    )
            except Exception as e:
                log.error("agent_crash", function=func_name, error=str(e))
                result = AgentResult(
                    error=str(e),
                    termination_reason="agent_crash",
                )

            # Collateral damage check: reject match if other functions got worse
            if result.matched and baseline is not None:
                try:
                    final_result = check_match(source_file, config)
                    if final_result.success:
                        damaged: list[tuple[str, float, float]] = []
                        for fn, before_pct in baseline.items():
                            if fn == func_name:
                                continue
                            final_fn = final_result.get_function(fn)
                            if final_fn is None:
                                continue
                            after_pct = final_fn.fuzzy_match_percent
                            if after_pct < before_pct:
                                damaged.append((fn, before_pct, after_pct))
                        if damaged:
                            for fn, before_pct, after_pct in damaged:
                                bound_log.warning(
                                    "collateral_damage",
                                    damaged_function=fn,
                                    before=before_pct,
                                    after=after_pct,
                                    delta=round(after_pct - before_pct, 2),
                                )
                            bound_log.warning(
                                "match_rejected_collateral_damage",
                                function=func_name,
                                num_damaged=len(damaged),
                            )
                            result.matched = False
                            result.termination_reason = "collateral_damage"
                except Exception as e:
                    bound_log.warning(
                        "collateral_check_error",
                        function=func_name,
                        error=str(e),
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

            # Auto-commit matched functions to the melee repo so victories
            # are locked in and can be built on by subsequent attempts.
            if result.matched:
                _auto_commit_match(func_name, source_file, config, bound_log)

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
