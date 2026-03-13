"""Headless Claude Code runner for decomp agent.

Replaces run_agent() when config.claude_code.enabled is True.
Invokes Claude Code CLI inside the Docker worker container via
`docker exec`, parses the JSON output, and returns an AgentResult.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time

import structlog

from decomp_agent.agent.loop import AgentResult
from decomp_agent.config import Config
from decomp_agent.orchestrator.headless_context import (
    build_headless_task_prompt,
    load_headless_system_prompt,
)

log = structlog.get_logger()

def run_headless(
    function_name: str | None,
    source_file: str,
    config: Config,
    *,
    worker_label: str = "",
    prior_best_code: str | None = None,
    prior_match_pct: float = 0,
) -> AgentResult:
    """Run Claude Code headless to match function(s).

    Args:
        function_name: Target function, or None for file mode (match all).
        source_file: Source file path e.g. "melee/mn/mngallery.c"
        config: Project configuration.
        worker_label: Label for logging.
        prior_best_code: Prior best code for warm starts (function mode only).
        prior_match_pct: Prior match % for warm starts.

    Returns:
        AgentResult with match results.
    """
    file_mode = function_name is None
    start_time = time.monotonic()
    bound_log = log.bind(
        function=function_name or "(file mode)",
        source_file=source_file,
        worker=worker_label,
        file_mode=file_mode,
    )

    prompt = build_headless_task_prompt(
        function_name,
        source_file,
        config,
        prior_best_code=prior_best_code,
        prior_match_pct=prior_match_pct,
    )

    # Build the docker exec command
    container = config.claude_code.container_name
    max_turns = config.claude_code.max_turns
    timeout = config.claude_code.timeout_seconds

    # File mode gets many more turns — it's working on multiple functions.
    # Warm starts get more turns than cold starts.
    if file_mode:
        max_turns = max(max_turns, 100)
        timeout = max(timeout, 5400)  # 90 min
    elif prior_best_code is not None:
        if prior_match_pct >= 75.0:
            max_turns = max(max_turns, 80)
            timeout = max(timeout, 3600)
        else:
            max_turns = max(max_turns, 50)
            timeout = max(timeout, 2400)

    system_prompt = load_headless_system_prompt()

    claude_args = [
        "claude",
        "-p", shlex.quote(prompt),
        "--output-format", "json",
        "--model", "claude-opus-4-6",
        "--append-system-prompt", shlex.quote(system_prompt),
        "--mcp-config", "/app/mcp.json",
        "--dangerously-skip-permissions",
        "--max-turns", str(max_turns),
    ]

    # Use shell inside container so $(cat ...) expands
    shell_cmd = " ".join(claude_args)
    cmd = [
        "docker", "exec", container,
        "sh", "-c", shell_cmd,
    ]

    bound_log.info(
        "headless_start",
        container=container,
        max_turns=max_turns,
        timeout=timeout,
        warm_start=prior_best_code is not None,
    )

    # Run the command
    result = AgentResult(
        model="claude-code-headless",
        warm_start=prior_best_code is not None,
    )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result.elapsed_seconds = time.monotonic() - start_time
        result.termination_reason = "timeout"
        result.error = f"Claude Code timed out after {timeout}s"
        bound_log.warning("headless_timeout", timeout=timeout)
        return result

    # Combine all output for error detection — Claude Code may write errors
    # to stdout or stderr depending on failure mode.
    stderr = proc.stderr or ""
    stdout = proc.stdout or ""
    all_output = f"{stderr}\n{stdout}".lower()

    # Check for rate limiting in stderr only — stdout contains JSON with
    # large numbers that can false-positive on "429" substring matching.
    stderr_lower = stderr.lower()
    rate_limited = (
        "rate limit" in stderr_lower
        or "rate_limit" in stderr_lower
        or "429" in stderr_lower
        or "overloaded" in stderr_lower
        or "too many requests" in stderr_lower
    )
    if rate_limited:
        result.elapsed_seconds = time.monotonic() - start_time
        result.termination_reason = "rate_limited"
        error_detail = stderr.strip() or stdout.strip()[:500]
        result.error = f"Rate limited: {error_detail}"
        bound_log.warning("headless_rate_limited", error=error_detail)
        return result

    # Check for non-zero exit
    if proc.returncode != 0:
        result.elapsed_seconds = time.monotonic() - start_time
        error_detail = stderr.strip() or stdout.strip()[:500] or "(no output)"
        elapsed = result.elapsed_seconds

        # If Claude Code crashes fast with no useful output, it's almost
        # certainly an API error (overloaded, rate limited, auth issue).
        # Treat as rate_limited so the batch runner backs off.
        if elapsed < 15 and error_detail == "(no output)":
            result.termination_reason = "rate_limited"
            result.error = f"Claude Code crashed immediately (exit {proc.returncode}, {elapsed:.1f}s) — likely API overload"
            bound_log.warning(
                "headless_fast_crash",
                returncode=proc.returncode,
                elapsed=round(elapsed, 1),
                stdout=stdout.strip()[:200],
                stderr=stderr.strip()[:200],
            )
        else:
            result.termination_reason = "api_error"
            result.error = f"Claude Code exited with code {proc.returncode}: {error_detail}"
            bound_log.error(
                "headless_error",
                returncode=proc.returncode,
                elapsed=round(elapsed, 1),
                stderr=stderr.strip()[:200],
                stdout=stdout.strip()[:200],
            )
        return result

    # Parse JSON output
    try:
        output = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        result.elapsed_seconds = time.monotonic() - start_time
        result.termination_reason = "api_error"
        result.error = f"Failed to parse Claude Code JSON output: {e}"
        bound_log.error("headless_json_error", error=str(e))
        return result

    # Extract token usage
    usage = output.get("usage", {})
    result.input_tokens = usage.get("input_tokens", 0)
    result.output_tokens = usage.get("output_tokens", 0)
    result.cached_tokens = usage.get("cache_read_input_tokens", 0)
    result.total_tokens = result.input_tokens + result.output_tokens

    # Extract result text and session ID
    result_text = output.get("result", "")
    session_id = output.get("session_id", "")
    result.session_id = session_id

    # Determine iterations from num_turns
    result.iterations = output.get("num_turns", 0)

    # Determine termination reason from subtype
    subtype = output.get("subtype", "")
    if subtype == "error_max_turns":
        result.termination_reason = "max_iterations"
    else:
        result.termination_reason = "model_stopped"

    # Check result text for match signals
    if "confirmed MATCH" in result_text or "All functions match" in result_text:
        result.matched = True
        result.termination_reason = "matched"
        result.best_match_percent = 100.0
    else:
        # Try to extract match percentage from result text
        pct_match = re.search(r"(\d+(?:\.\d+)?)%\s*match", result_text)
        if pct_match:
            result.best_match_percent = float(pct_match.group(1))

    # Post-run verification: compile and check match from the host side.
    # This catches matches even when the result text is empty (e.g. max_turns).
    try:
        from decomp_agent.tools.build import check_match

        check = check_match(source_file, config)
        if check.success:
            if file_mode:
                # File mode: capture per-function results
                result.file_mode = True
                for func_result in check.functions:
                    after_pct = func_result.fuzzy_match_percent
                    result.function_deltas[func_result.name] = (0.0, after_pct)
                    if func_result.is_matched:
                        result.newly_matched.append(func_result.name)
                if result.newly_matched:
                    result.matched = True
                    result.termination_reason = "matched"
                result.best_match_percent = (
                    sum(f.fuzzy_match_percent for f in check.functions)
                    / max(len(check.functions), 1)
                )
            elif not result.matched and function_name is not None:
                func_result = check.get_function(function_name)
                if func_result is not None:
                    result.best_match_percent = max(
                        result.best_match_percent,
                        func_result.fuzzy_match_percent,
                    )
                    if func_result.is_matched:
                        result.matched = True
                        result.termination_reason = "matched"
                        result.best_match_percent = 100.0
    except Exception:
        bound_log.warning("post_run_check_failed", exc_info=True)

    # Read final function code from source file (bind-mounted, visible from host)
    if function_name is not None:
        try:
            from decomp_agent.tools.source import get_function_source, read_source_file

            src_path = config.melee.resolve_source_path(source_file)
            if src_path.exists():
                source = read_source_file(src_path)
                result.final_code = get_function_source(source, function_name)
        except Exception:
            bound_log.warning("final_code_read_failed", exc_info=True)

    result.elapsed_seconds = time.monotonic() - start_time

    log_kwargs = dict(
        reason=result.termination_reason,
        matched=result.matched,
        best_match=result.best_match_percent,
        iterations=result.iterations,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cached_tokens=result.cached_tokens,
        elapsed=round(result.elapsed_seconds, 1),
        session_id=session_id,
    )
    if file_mode:
        log_kwargs["newly_matched"] = result.newly_matched
        log_kwargs["file_mode"] = True
    bound_log.info("headless_finished", **log_kwargs)

    return result
