"""Core agent loop: iteratively match a function via OpenAI tool-calling.

Uses the OpenAI Responses API (client.responses.create) with
previous_response_id for multi-turn conversation state management.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import structlog
from openai import OpenAI

from decomp_agent.agent.context_mgmt import ContextConfig
from decomp_agent.agent.prompts import build_system_prompt
from decomp_agent.config import Config
from decomp_agent.tools.registry import build_registry

log = structlog.get_logger()


@dataclass
class AgentResult:
    """Outcome of one agent run on a single function."""

    matched: bool = False
    best_match_percent: float = 0.0
    iterations: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    elapsed_seconds: float = 0.0
    final_code: str | None = None
    error: str | None = None
    termination_reason: str = ""
    """One of: matched, model_stopped, max_iterations, token_budget, api_error"""


_FUNC_MATCH_RE = re.compile(r"(\w+):\s*MATCH\b")
_FUNC_PCT_RE = re.compile(r"(\w+):\s*(\d+(?:\.\d+)?)%")


def _update_best_match(
    tool_name: str, tool_result: str, current_best: float, function_name: str
) -> float:
    """Parse compile_and_check output to track the target function's match.

    Only considers the target function's line in the output. Recognises:
      - "All functions match!" → 100.0
      - "function_name: MATCH (size: N)" → 100.0
      - "function_name: 85.3% (size: N)" → 85.3
    Returns the max of current_best and the target function's match.
    """
    if tool_name != "compile_and_check":
        return current_best

    if "All functions match!" in tool_result:
        return 100.0

    best = current_best

    for m in _FUNC_MATCH_RE.finditer(tool_result):
        if m.group(1) == function_name:
            best = max(best, 100.0)

    for m in _FUNC_PCT_RE.finditer(tool_result):
        if m.group(1) == function_name:
            pct = float(m.group(2))
            if pct > best:
                best = pct

    return best


def _target_function_matched(
    tool_name: str, tool_result: str, function_name: str
) -> bool:
    """Check if the target function specifically shows MATCH in compile output."""
    if tool_name != "compile_and_check":
        return False

    if "All functions match!" in tool_result:
        return True

    for m in _FUNC_MATCH_RE.finditer(tool_result):
        if m.group(1) == function_name:
            return True

    return False


def run_agent(
    function_name: str,
    source_file: str,
    config: Config,
    *,
    context_config: ContextConfig | None = None,
) -> AgentResult:
    """Run the agent loop to match a single function.

    Args:
        function_name: Name of the function to match
        source_file: Object name from configure.py, e.g. "melee/lb/lbcommand.c"
        config: Project configuration
        context_config: Unused (kept for backward compatibility).
            Context is managed server-side via truncation="auto".

    Returns:
        AgentResult with the outcome.
    """
    start_time = time.monotonic()
    bound_log = log.bind(function=function_name, source_file=source_file)

    # Build components
    system_prompt = build_system_prompt(function_name, source_file)
    registry = build_registry(config)
    tools = registry.get_responses_api_tools()

    client = OpenAI()

    result = AgentResult()
    max_iterations = config.agent.max_iterations
    previous_response_id: str | None = None

    # First turn: user message with the assignment
    current_input: str | list[dict] = (
        f"Match function {function_name} in {source_file}. "
        f"Start by calling get_target_assembly and get_context to orient yourself."
    )

    for iteration in range(1, max_iterations + 1):
        result.iterations = iteration
        bound_log.info("iteration_start", iteration=iteration, max=max_iterations)

        # Call the Responses API
        kwargs: dict = {
            "model": config.agent.model,
            "instructions": system_prompt,
            "tools": tools,
            "input": current_input,
            "truncation": "auto",
        }
        if previous_response_id is not None:
            kwargs["previous_response_id"] = previous_response_id

        try:
            response = client.responses.create(**kwargs)
        except Exception as e:
            bound_log.error("api_error", error=str(e))
            result.error = str(e)
            result.termination_reason = "api_error"
            break

        previous_response_id = response.id

        # Track token usage
        if response.usage:
            result.total_tokens += response.usage.total_tokens
            result.input_tokens += getattr(response.usage, "input_tokens", 0)
            result.output_tokens += getattr(response.usage, "output_tokens", 0)
            details = getattr(response.usage, "input_tokens_details", None)
            if details:
                result.cached_tokens += getattr(details, "cached_tokens", 0)

        # Extract function calls from response output
        function_calls = [
            item for item in response.output
            if getattr(item, "type", None) == "function_call"
        ]

        # Check if model stopped (no tool calls)
        if not function_calls:
            bound_log.info("model_stopped")
            result.termination_reason = "model_stopped"
            break

        # Dispatch each function call
        tool_outputs: list[dict] = []
        for fc in function_calls:
            bound_log.info("tool_call", tool=fc.name)
            tool_result = registry.dispatch(fc.name, fc.arguments)

            tool_outputs.append({
                "type": "function_call_output",
                "call_id": fc.call_id,
                "output": tool_result,
            })

            # Track best match
            previous_best = result.best_match_percent
            result.best_match_percent = _update_best_match(
                fc.name, tool_result, result.best_match_percent, function_name
            )
            if result.best_match_percent > previous_best:
                bound_log.info(
                    "match_improved",
                    previous=previous_best,
                    new=result.best_match_percent,
                )

            # Auto-detect match from compile_and_check output
            if _target_function_matched(fc.name, tool_result, function_name):
                result.matched = True
                result.termination_reason = "matched"
                bound_log.info("function_matched", trigger="compile_and_check")

            # Also accept explicit mark_complete
            if fc.name == "mark_complete":
                result.matched = True
                result.termination_reason = "matched"
                bound_log.info("function_matched", trigger="mark_complete")

        if result.matched:
            break

        # Next turn: send tool outputs
        current_input = tool_outputs

        # Check token budget
        if result.total_tokens >= config.agent.max_tokens_per_attempt:
            bound_log.warning(
                "token_budget_exhausted",
                used=result.total_tokens,
                budget=config.agent.max_tokens_per_attempt,
            )
            result.termination_reason = "token_budget"
            break
    else:
        result.termination_reason = "max_iterations"

    # Read final function code from the source file
    try:
        from decomp_agent.tools.source import get_function_source, read_source_file

        src_path = config.melee.repo_path / "src" / source_file
        if src_path.exists():
            source = read_source_file(src_path)
            result.final_code = get_function_source(source, function_name)
    except Exception:
        bound_log.warning("final_code_read_failed", exc_info=True)

    result.elapsed_seconds = time.monotonic() - start_time

    bound_log.info(
        "agent_finished",
        reason=result.termination_reason,
        matched=result.matched,
        best_match=result.best_match_percent,
        iterations=result.iterations,
        tokens=result.total_tokens,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cached_tokens=result.cached_tokens,
        elapsed=round(result.elapsed_seconds, 1),
    )

    return result
