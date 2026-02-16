"""Core agent loop: iteratively match a function via OpenAI tool-calling."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from openai import OpenAI

from decomp_agent.agent.context_mgmt import ContextConfig, manage_context
from decomp_agent.agent.prompts import build_system_prompt
from decomp_agent.config import Config
from decomp_agent.tools.registry import build_registry

log = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Outcome of one agent run on a single function."""

    matched: bool = False
    best_match_percent: float = 0.0
    iterations: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    final_code: str | None = None
    error: str | None = None
    termination_reason: str = ""
    """One of: matched, model_stopped, max_iterations, token_budget, api_error"""


def _message_to_dict(msg: object) -> dict:
    """Convert an OpenAI SDK ChatCompletionMessage to a plain dict.

    The SDK returns rich objects, but we need dicts to re-send in the
    messages list.  Handles assistant messages with optional tool_calls.
    """
    d: dict = {"role": msg.role}  # type: ignore[union-attr]
    if msg.content is not None:  # type: ignore[union-attr]
        d["content"] = msg.content  # type: ignore[union-attr]
    if msg.tool_calls:  # type: ignore[union-attr]
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in msg.tool_calls  # type: ignore[union-attr]
        ]
    return d


_MATCH_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)%")


def _update_best_match(
    tool_name: str, tool_result: str, current_best: float
) -> float:
    """Parse compile_and_check output to track the best match percentage.

    Looks for "X%" patterns in the result. Returns the max of current_best
    and any percentage found, or 100.0 if "All functions match!" is present.
    """
    if tool_name != "compile_and_check":
        return current_best

    if "All functions match!" in tool_result:
        return 100.0

    best = current_best
    for m in _MATCH_PCT_RE.finditer(tool_result):
        pct = float(m.group(1))
        if pct > best:
            best = pct
    return best


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
        context_config: Optional context management settings

    Returns:
        AgentResult with the outcome.
    """
    start_time = time.monotonic()
    ctx_config = context_config or ContextConfig()

    # Build components
    system_prompt = build_system_prompt(function_name, source_file)
    registry = build_registry(config)
    tools = registry.get_openai_tools()

    client = OpenAI()

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    result = AgentResult()

    max_iterations = config.agent.max_iterations

    for iteration in range(1, max_iterations + 1):
        result.iterations = iteration
        log.info("Iteration %d/%d", iteration, max_iterations)

        # Manage context window
        trimmed = manage_context(messages, ctx_config)

        # Call the model
        try:
            response = client.chat.completions.create(
                model=config.agent.model,
                messages=trimmed,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
                temperature=0.2,
            )
        except Exception as e:
            log.error("OpenAI API error: %s", e)
            result.error = str(e)
            result.termination_reason = "api_error"
            break

        # Track token usage
        if response.usage:
            result.total_tokens += response.usage.total_tokens

        choice = response.choices[0]
        assistant_msg = choice.message

        # Append assistant message to history
        messages.append(_message_to_dict(assistant_msg))

        # Check if model stopped (no tool calls)
        if not assistant_msg.tool_calls:
            log.info("Model stopped without tool calls")
            result.termination_reason = "model_stopped"
            break

        # Dispatch each tool call
        for tool_call in assistant_msg.tool_calls:
            fn_name = tool_call.function.name
            fn_args = tool_call.function.arguments

            log.info("Tool call: %s", fn_name)
            tool_result = registry.dispatch(fn_name, fn_args)

            # Append tool result to history
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )

            # Track best match
            result.best_match_percent = _update_best_match(
                fn_name, tool_result, result.best_match_percent
            )

            # Check for mark_complete
            if fn_name == "mark_complete":
                result.matched = True
                result.termination_reason = "matched"
                log.info("Function marked as complete!")

        if result.matched:
            break

        # Check token budget
        if result.total_tokens >= config.agent.max_tokens_per_attempt:
            log.warning(
                "Token budget exhausted: %d >= %d",
                result.total_tokens,
                config.agent.max_tokens_per_attempt,
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
        log.warning("Could not read final function code", exc_info=True)

    result.elapsed_seconds = time.monotonic() - start_time

    log.info(
        "Agent finished: reason=%s matched=%s best=%.1f%% iterations=%d tokens=%d",
        result.termination_reason,
        result.matched,
        result.best_match_percent,
        result.iterations,
        result.total_tokens,
    )

    return result
