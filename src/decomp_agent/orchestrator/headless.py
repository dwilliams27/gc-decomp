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

from decomp_agent.agent.m2c_seed import build_prefetched_m2c_block
from decomp_agent.agent.loop import AgentResult
from decomp_agent.config import Config

log = structlog.get_logger()


def run_headless(
    function_name: str,
    source_file: str,
    config: Config,
    *,
    worker_label: str = "",
    prior_best_code: str | None = None,
    prior_match_pct: float = 0,
) -> AgentResult:
    """Run Claude Code headless to match a single function.

    Constructs a prompt, invokes `docker exec <container> claude -p ...`
    with the MCP server and system prompt, parses the JSON output, and
    returns an AgentResult.
    """
    start_time = time.monotonic()
    bound_log = log.bind(
        function=function_name,
        source_file=source_file,
        worker=worker_label,
    )

    # Build the prompt
    m2c_seed = build_prefetched_m2c_block(
        function_name, source_file, config, max_chars=6000
    )

    if prior_best_code is not None:
        # Pre-fetch the diff if the prior code is already written to file
        diff_block = ""
        try:
            from decomp_agent.tools.disasm import get_function_diff
            diff_text = get_function_diff(function_name, source_file, config)
            diff_block = (
                f"\n\nCurrent diff (target vs compiled):\n```\n{diff_text}\n```"
            )
        except Exception:
            pass

        # Tailor guidance based on match quality
        if prior_match_pct >= 80.0:
            strategy = (
                "You are VERY close. Make only tiny, targeted changes: "
                "reorder variable declarations, split or merge expressions, "
                "adjust casts, or change variable types. "
                "DO NOT rewrite the function or change its structure."
            )
        elif prior_match_pct >= 50.0:
            strategy = (
                "The structure is mostly right. Focus on register allocation: "
                "reorder variable declarations, change how temporaries are used, "
                "split compound expressions, or merge separate statements. "
                "Preserve the overall logic — do NOT rewrite from scratch."
            )
        else:
            strategy = (
                "Start by writing this code with write_function, then analyze "
                "the diff to find remaining mismatches. Focus on improving from "
                "this baseline rather than starting from scratch."
            )

        prompt = (
            f"Match function {function_name} in {source_file}.\n\n"
            f"A previous attempt reached {prior_match_pct:.1f}% match with this code:\n\n"
            f"```c\n{prior_best_code}\n```\n\n"
            f"{strategy}"
            f"{diff_block}"
            f"{m2c_seed}"
        )
    else:
        prompt = (
            f"Match function {function_name} in {source_file}.\n\n"
            f"You have tools to read assembly, get context/headers, "
            f"write code, check diffs, and iterate. The m2c output below "
            f"is a starting scaffold. Use get_context and get_target_assembly "
            f"to understand what the function does, then iterate with "
            f"write_function and get_diff until you reach 100% match.\n\n"
            f"If you hit compile errors from undeclared symbols, check the "
            f"extern references section below — it tells you which headers "
            f"to include or what extern declarations to add."
            f"{m2c_seed}"
        )

    # Build the docker exec command
    container = config.claude_code.container_name
    max_turns = config.claude_code.max_turns
    timeout = config.claude_code.timeout_seconds

    # Warm starts get more turns — they skip orientation and spend all
    # turns productively iterating on register allocation and fine-tuning.
    if prior_best_code is not None:
        if prior_match_pct >= 75.0:
            max_turns = max(max_turns, 80)
            timeout = max(timeout, 3600)
        else:
            max_turns = max(max_turns, 50)
            timeout = max(timeout, 2400)

    # Build a shell command that reads the system prompt file inside the container
    system_prompt_path = "/app/system-prompt.md"

    claude_args = [
        "claude",
        "-p", shlex.quote(prompt),
        "--output-format", "json",
        "--model", "claude-opus-4-6",
        "--append-system-prompt", f'"$(cat {system_prompt_path})"',
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

    # Check for rate limiting in any output
    rate_limited = (
        "rate limit" in all_output
        or "rate_limit" in all_output
        or "429" in all_output
        or "overloaded" in all_output
        or "too many requests" in all_output
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
    if not result.matched:
        try:
            from decomp_agent.tools.build import check_match

            check = check_match(source_file, config)
            if check.success:
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
    try:
        from decomp_agent.tools.source import get_function_source, read_source_file

        src_path = config.melee.resolve_source_path(source_file)
        if src_path.exists():
            source = read_source_file(src_path)
            result.final_code = get_function_source(source, function_name)
    except Exception:
        bound_log.warning("final_code_read_failed", exc_info=True)

    result.elapsed_seconds = time.monotonic() - start_time

    bound_log.info(
        "headless_finished",
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

    return result
