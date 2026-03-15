"""Run the agent on a single function or file with DB lifecycle management."""

from __future__ import annotations

from contextlib import nullcontext
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

import structlog
from sqlalchemy import Engine
from sqlmodel import Session

from decomp_agent.agent.loop import AgentResult, run_agent
from decomp_agent.config import Config
from decomp_agent.cost import calculate_cost
from decomp_agent.models.db import (
    Function,
    get_best_attempt,
    get_functions_for_file,
    record_attempt,
    record_run,
)
from decomp_agent.tools.build import check_match
from decomp_agent.tools.source import get_function_source, read_source_file

log = structlog.get_logger()

# Per-source-file locks. When multiple workers target functions in the same
# file, they must serialize: concurrent write_function calls would corrupt
# each other (non-atomic read-modify-write), and the save/restore rollback
# would clobber a parallel worker's changes.
_file_locks: dict[str, threading.Lock] = {}
_file_locks_guard = threading.Lock()


def _provider_enabled(section) -> bool:
    enabled = getattr(section, "enabled", False)
    return enabled is True


def _get_file_lock(source_file: str) -> threading.Lock:
    """Get or create a lock for a source file path."""
    with _file_locks_guard:
        if source_file not in _file_locks:
            _file_locks[source_file] = threading.Lock()
        return _file_locks[source_file]


def _uses_isolated_worker(config: Config) -> bool:
    """Return whether the active provider edits an isolated checkout instead of the main repo."""
    return (
        (
            _provider_enabled(config.codex_code)
            and getattr(config.codex_code, "isolated_worker_enabled", False) is True
        )
        or (
            _provider_enabled(config.claude_code)
            and getattr(config.claude_code, "isolated_worker_enabled", False) is True
        )
    )


def _capture_baseline_with_retry(
    source_file: str,
    config: Config,
    bound_log,
    *,
    function_name: str,
) -> dict[str, float] | None:
    """Capture per-function baseline match data with a small retry budget.

    The build pipeline can occasionally fail transiently under concurrent load.
    Retry baseline capture a small number of times before giving up.
    """
    attempts = max(config.campaign.baseline_compile_retries + 1, 1)
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            baseline_result = check_match(source_file, config)
        except Exception as exc:
            last_error = str(exc)
            bound_log.warning(
                "baseline_capture_error",
                function=function_name,
                attempt=attempt,
                max_attempts=attempts,
                error=last_error,
            )
            continue

        if baseline_result.success:
            baseline = {
                f.name: f.fuzzy_match_percent
                for f in baseline_result.functions
            }
            bound_log.info(
                "baseline_captured",
                function=function_name,
                attempt=attempt,
                num_functions=len(baseline),
            )
            return baseline

        last_error = baseline_result.error
        log_method = bound_log.warning if attempt == attempts else bound_log.info
        log_method(
            "baseline_compile_failed",
            function=function_name,
            attempt=attempt,
            max_attempts=attempts,
            error=baseline_result.error,
        )

    if last_error:
        bound_log.warning(
            "baseline_unavailable",
            function=function_name,
            error=last_error,
        )
    return None


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


def _promote_isolated_patch(
    result: AgentResult,
    func_name: str,
    source_file: str,
    config: Config,
    baseline: dict[str, float] | None,
    bound_log,
) -> AgentResult:
    """Apply and validate an isolated worker patch against the main repo."""
    if result.termination_reason != "isolated_patch_ready" or not result.patch_path:
        return result

    patch_path = Path(result.patch_path)
    if not patch_path.is_file():
        result.termination_reason = "patch_missing"
        result.error = f"Isolated worker patch not found: {patch_path}"
        return result

    repo_path = str(config.melee.repo_path)
    check_apply = subprocess.run(
        ["git", "apply", "--check", str(patch_path)],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if check_apply.returncode != 0:
        result.termination_reason = "patch_apply_failed"
        result.error = check_apply.stderr.strip() or check_apply.stdout.strip() or "git apply --check failed"
        return result

    applied = False
    try:
        subprocess.run(
            ["git", "apply", str(patch_path)],
            check=True,
            capture_output=True,
            text=True,
            cwd=repo_path,
        )
        applied = True

        final_result = check_match(source_file, config)
        if not final_result.success:
            result.termination_reason = "patch_compile_failed"
            result.error = final_result.error or "compile failed after applying isolated patch"
            return result

        func_result = final_result.get_function(func_name)
        if func_result is None:
            result.termination_reason = "patch_validation_failed"
            result.error = f"Function {func_name} missing after applying isolated patch"
            return result

        result.best_match_percent = max(
            result.best_match_percent,
            func_result.fuzzy_match_percent,
        )
        if not func_result.is_matched:
            result.termination_reason = "patch_validation_failed"
            result.error = (
                f"Applied isolated patch but {func_name} only reached "
                f"{func_result.fuzzy_match_percent:.1f}%"
            )
            return result

        if baseline is not None:
            damaged: list[tuple[str, float, float]] = []
            for fn, before_pct in baseline.items():
                if fn == func_name:
                    continue
                other = final_result.get_function(fn)
                if other is None:
                    continue
                after_pct = other.fuzzy_match_percent
                if after_pct < before_pct:
                    damaged.append((fn, before_pct, after_pct))
            if damaged:
                for fn, before_pct, after_pct in damaged:
                    bound_log.warning(
                        "isolated_patch_collateral_damage",
                        damaged_function=fn,
                        before=before_pct,
                        after=after_pct,
                        delta=round(after_pct - before_pct, 2),
                    )
                result.termination_reason = "collateral_damage"
                result.error = (
                    f"Isolated patch regressed {len(damaged)} other function(s)"
                )
                return result

        result.matched = True
        result.termination_reason = "matched"
        result.best_match_percent = 100.0
        result.error = None
        return result
    finally:
        if applied and not result.matched:
            revert = subprocess.run(
                ["git", "apply", "-R", str(patch_path)],
                capture_output=True,
                text=True,
                cwd=repo_path,
            )
            if revert.returncode != 0:
                bound_log.warning(
                    "isolated_patch_revert_failed",
                    function=func_name,
                    patch_path=str(patch_path),
                    stderr=(revert.stderr or "").strip()[:200],
                    stdout=(revert.stdout or "").strip()[:200],
                )


def run_function(
    function: Function,
    config: Config,
    engine: Engine,
    *,
    worker_label: str = "",
    warm_start: bool = False,
    progress_callback=None,
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
        current_match_pct = function.current_match_pct
        attempt_count = function.attempts

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
    isolated_worker = _uses_isolated_worker(config)
    src_path = config.melee.resolve_source_path(source_file)
    file_lock = _get_file_lock(source_file)
    try:
        lock_context = file_lock if not isolated_worker else nullcontext()
        with lock_context:
            # Save source file before agent runs for rollback on failure
            saved_source = (
                src_path.read_bytes()
                if not isolated_worker and src_path.exists()
                else None
            )

            # Capture baseline match percentages for collateral damage guard.
            baseline = _capture_baseline_with_retry(
                source_file,
                config,
                bound_log,
                function_name=func_name,
            )

            # Look up best prior attempt for warm start
            prior_best_code: str | None = None
            prior_match_pct: float = 0
            if warm_start and attempt_count > 0:
                with Session(engine) as session:
                    best = get_best_attempt(session, function.id)  # type: ignore[arg-type]
                    if best is not None:
                        prior_best_code = best.final_code
                        prior_match_pct = best.best_match_pct
                        bound_log.info(
                            "warm_start",
                            prior_match=prior_match_pct,
                        )
            if warm_start and prior_best_code is None and current_match_pct > 0:
                try:
                    current_source = read_source_file(src_path)
                    seeded_code = get_function_source(current_source, func_name)
                    if seeded_code:
                        prior_best_code = seeded_code
                        prior_match_pct = current_match_pct
                        bound_log.info(
                            "warm_start_from_current_source",
                            prior_match=prior_match_pct,
                            code_len=len(seeded_code),
                        )
                except Exception as e:
                    bound_log.warning(
                        "warm_start_current_source_failed",
                        function=func_name,
                        error=str(e),
                    )

            # Run the agent
            try:
                if _provider_enabled(config.claude_code):
                    from decomp_agent.orchestrator.headless import run_headless

                    result = run_headless(
                        func_name,
                        source_file,
                        config,
                        worker_label=worker_label,
                        prior_best_code=prior_best_code,
                        prior_match_pct=prior_match_pct,
                        progress_callback=progress_callback,
                    )
                elif _provider_enabled(config.codex_code):
                    from decomp_agent.orchestrator.codex_headless import (
                        run_codex_headless,
                    )

                    result = run_codex_headless(
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

            if (
                isolated_worker
                and result.patch_path
                and result.best_match_percent >= 100.0
                and result.termination_reason != "isolated_patch_ready"
            ):
                bound_log.info(
                    "isolated_patch_ready_recovered",
                    function=func_name,
                    prior_reason=result.termination_reason,
                )
                result.termination_reason = "isolated_patch_ready"

            if isolated_worker and result.termination_reason == "isolated_patch_ready":
                with file_lock:
                    result = _promote_isolated_patch(
                        result,
                        func_name,
                        source_file,
                        config,
                        baseline,
                        bound_log,
                    )
            else:
                result = _promote_isolated_patch(
                    result,
                    func_name,
                    source_file,
                    config,
                    baseline,
                    bound_log,
                )

            # Collateral damage check: reject match if other functions got worse
            if result.matched and baseline is not None and not isolated_worker:
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

            # Capture final function code BEFORE reverting — this is our
            # safety net.  headless.py already sets result.final_code, but
            # if that failed (get_function_source returned None), we read
            # the source file here while it still contains the agent's work.
            if not result.final_code:
                try:
                    current_source = read_source_file(src_path)
                    captured = get_function_source(current_source, func_name)
                    if captured:
                        result.final_code = captured
                        bound_log.info(
                            "final_code_captured_before_revert",
                            function=func_name,
                            code_len=len(captured),
                        )
                except Exception as e:
                    bound_log.warning(
                        "final_code_capture_failed",
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
            elif function.attempts >= config.orchestration.max_attempts_per_function:
                function.status = "skipped"
                bound_log.info(
                    "function_skipped",
                    function=func_name,
                    attempts=function.attempts,
                    best_match=function.current_match_pct,
                    reason="max_attempts_exceeded",
                )
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


def run_file(
    source_file: str,
    config: Config,
    *,
    engine: Engine | None = None,
    worker_label: str = "",
) -> AgentResult:
    """Run the agent on an entire source file to match all unmatched functions.

    Unlike run_function() which targets a single function:
    - Tells the agent to match ALL unmatched functions in the file
    - Captures baseline for all functions before the run
    - Reports per-function deltas after the run
    - Reverts only if previously-matched functions regressed (collateral damage)
    - Auto-commits each newly matched function

    If ``engine`` is provided, creates a Run + per-function Attempts in the DB
    and updates Function statuses.
    """
    bound_log = log.bind(
        source_file=source_file,
        worker=worker_label,
        file_mode=True,
    )

    # Serialize on the source file
    file_lock = _get_file_lock(source_file)
    saved_source: bytes | None = None

    try:
        with file_lock:
            # Save source for rollback
            src_path = config.melee.resolve_source_path(source_file)
            saved_source = src_path.read_bytes() if src_path.exists() else None

            # Capture baseline
            baseline: dict[str, float] = {}
            try:
                baseline_result = check_match(source_file, config)
                if baseline_result.success:
                    baseline = {
                        f.name: f.fuzzy_match_percent
                        for f in baseline_result.functions
                    }
            except Exception as e:
                bound_log.warning("baseline_capture_error", error=str(e))

            bound_log.info(
                "file_run_start",
                total_functions=len(baseline),
                already_matched=sum(1 for v in baseline.values() if v >= 100.0),
            )

            # Run headless in file mode
            try:
                if _provider_enabled(config.claude_code):
                    from decomp_agent.orchestrator.headless import run_headless

                    result = run_headless(
                        None,  # file mode
                        source_file,
                        config,
                        worker_label=worker_label,
                    )
                elif _provider_enabled(config.codex_code):
                    from decomp_agent.orchestrator.codex_headless import (
                        run_codex_headless,
                    )

                    result = run_codex_headless(
                        None,  # file mode
                        source_file,
                        config,
                        worker_label=worker_label,
                    )
                else:
                    raise RuntimeError("file-mode requires a headless provider")
            except Exception as e:
                bound_log.error("agent_crash", error=str(e))
                result = AgentResult(
                    error=str(e),
                    termination_reason="agent_crash",
                    file_mode=True,
                )
                return result

            # Fill in baseline "before" values in function_deltas
            for func_name, (_, after) in result.function_deltas.items():
                before = baseline.get(func_name, 0.0)
                result.function_deltas[func_name] = (before, after)

            # Determine newly matched (wasn't 100% before, is now)
            result.newly_matched = [
                name for name, (before, after)
                in result.function_deltas.items()
                if before < 100.0 and after >= 100.0
            ]
            result.matched = len(result.newly_matched) > 0

            # Collateral damage check: did any previously-matched function regress?
            collateral = []
            for func_name, (before, after) in result.function_deltas.items():
                if before >= 100.0 and after < 100.0:
                    collateral.append((func_name, before, after))

            if collateral:
                for fn, before, after in collateral:
                    bound_log.warning(
                        "collateral_damage",
                        damaged_function=fn,
                        before=before,
                        after=after,
                    )
                # Revert the whole file — can't accept breaking matched functions
                if saved_source is not None:
                    src_path.write_bytes(saved_source)
                result.matched = False
                result.newly_matched = []
                result.termination_reason = "collateral_damage"
                bound_log.warning(
                    "file_reverted_collateral_damage",
                    num_damaged=len(collateral),
                )
            elif result.newly_matched:
                # Auto-commit each newly matched function
                for func_name in result.newly_matched:
                    _auto_commit_match(func_name, source_file, config, bound_log)

            # Record to DB
            if engine is not None:
                from decomp_agent.cost import calculate_cost

                cost = calculate_cost(result, config.pricing)
                with Session(engine) as session:
                    functions_by_name = get_functions_for_file(session, source_file)
                    record_run(
                        session,
                        result,
                        cost,
                        functions_by_name=functions_by_name,
                        source_file=source_file,
                    )

            # Log summary
            improved = [
                (name, before, after)
                for name, (before, after) in result.function_deltas.items()
                if after > before and name not in result.newly_matched
            ]
            bound_log.info(
                "file_run_complete",
                newly_matched=result.newly_matched,
                num_improved=len(improved),
                reason=result.termination_reason,
                elapsed=round(result.elapsed_seconds, 1),
            )
            for name, before, after in improved:
                bound_log.info(
                    "function_improved",
                    function=name,
                    before=round(before, 1),
                    after=round(after, 1),
                )

            return result

    except (KeyboardInterrupt, SystemExit):
        bound_log.warning("interrupted")
        if saved_source is not None:
            try:
                src_path = config.melee.resolve_source_path(source_file)
                src_path.write_bytes(saved_source)
            except Exception:
                pass
        raise
