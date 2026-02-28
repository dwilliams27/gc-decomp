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
    if prior_best_code is not None:
        prompt = (
            f"Match function {function_name} in {source_file}.\n\n"
            f"A previous attempt reached {prior_match_pct:.1f}% match with this code:\n\n"
            f"```c\n{prior_best_code}\n```\n\n"
            f"Start by writing this code with write_function, then analyze the diff "
            f"to find remaining mismatches. Focus on improving from this baseline "
            f"rather than starting from scratch."
        )
    else:
        prompt = (
            f"Match function {function_name} in {source_file}. "
            f"Start by calling get_target_assembly and get_context to orient yourself."
        )

    # Build the docker exec command
    container = config.claude_code.container_name
    max_turns = config.claude_code.max_turns
    timeout = config.claude_code.timeout_seconds

    # Build a shell command that reads the system prompt file inside the container
    system_prompt_path = "/app/system-prompt.md"

    claude_args = [
        "claude",
        "-p", shlex.quote(prompt),
        "--output-format", "json",
        "--append-system-prompt", f'"$(cat {system_prompt_path})"',
        "--mcp-config", "/app/mcp.json",
        "--dangerously-skip-permissions",
        "--disallowedTools",
        "Edit,Write,Bash,NotebookEdit,EnterWorktree",
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

    # Check for rate limiting in stderr
    stderr = proc.stderr or ""
    if "rate limit" in stderr.lower() or "429" in stderr:
        result.elapsed_seconds = time.monotonic() - start_time
        result.termination_reason = "rate_limited"
        result.error = f"Rate limited: {stderr.strip()}"
        bound_log.warning("headless_rate_limited", stderr=stderr.strip())
        return result

    # Check for non-zero exit
    if proc.returncode != 0:
        result.elapsed_seconds = time.monotonic() - start_time
        result.termination_reason = "api_error"
        result.error = f"Claude Code exited with code {proc.returncode}: {stderr.strip()}"
        bound_log.error(
            "headless_error",
            returncode=proc.returncode,
            stderr=stderr.strip(),
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

    # Extract result text
    result_text = output.get("result", "")
    session_id = output.get("session_id", "")

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
