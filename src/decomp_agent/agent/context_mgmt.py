"""Context window management for the agent loop.

Provides progressive truncation of the message history to stay within
the model's context window.  Most runs never need truncation — a typical
15-iteration run uses ~67k tokens.  But complex functions with large
headers and 30+ iterations can exceed 128k, so we need a safety net.

Priority (highest → lowest):
  1. System prompt — always kept
  2. Orientation messages (first assembly + context + m2c) — most valuable
  3. Recent iteration cycle (last N messages) — active feedback loop
  4. Old iteration results — superseded by newer attempts
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContextConfig:
    """Tunable parameters for context management."""

    max_context_tokens: int = 120_000
    """Total token budget for the conversation."""

    output_reserve: int = 4_096
    """Tokens reserved for the model's response."""

    protect_first_n: int = 0
    """Number of messages after system to always keep.
    0 means auto-detect via find_orientation_boundary."""

    protect_last_n: int = 12
    """Number of trailing messages to always keep (~3 iteration cycles)."""

    truncate_threshold: int = 4_000
    """First-pass: truncate tool results above this many chars."""

    aggressive_threshold: int = 1_000
    """Second-pass: more aggressive truncation target."""


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def _message_tokens(message: dict) -> int:
    """Estimate tokens for a single message dict."""
    total = 0
    content = message.get("content")
    if isinstance(content, str):
        total += estimate_tokens(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                text = part.get("text", "")
                total += estimate_tokens(text)
    # Tool calls in assistant messages
    for tc in message.get("tool_calls", []):
        fn = tc.get("function", {})
        total += estimate_tokens(fn.get("name", ""))
        total += estimate_tokens(fn.get("arguments", ""))
    return total


def _total_tokens(messages: list[dict]) -> int:
    """Estimate total tokens across all messages."""
    return sum(_message_tokens(m) for m in messages)


def truncate_tool_result(content: str, max_chars: int) -> str:
    """Truncate a tool result, keeping head and tail for context.

    Keeps 60% from the start and 30% from the end, with a notice
    in the middle showing how much was removed.
    """
    if len(content) <= max_chars:
        return content
    head_size = int(max_chars * 0.6)
    tail_size = int(max_chars * 0.3)
    removed = len(content) - head_size - tail_size
    return (
        content[:head_size]
        + f"\n\n... [{removed} chars truncated] ...\n\n"
        + content[-tail_size:]
    )


def find_orientation_boundary(messages: list[dict]) -> int:
    """Find where orientation phase ends in the message history.

    The orientation phase ends when the model first calls write_function
    or compile_and_check — that's when iterative matching begins.

    Returns the index of the first message that is part of the iteration
    phase.  If no boundary is found, returns len(messages) (everything
    is orientation).
    """
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            fn_name = tc.get("function", {}).get("name", "")
            if fn_name in ("write_function", "compile_and_check"):
                return i
    return len(messages)


def manage_context(
    messages: list[dict],
    config: ContextConfig | None = None,
) -> list[dict]:
    """Progressively trim messages to fit within the token budget.

    Strategy (applied in order until under budget):
      1. Fast path — if under budget, return as-is.
      2. Truncate long tool results in the middle zone.
      3. More aggressive truncation of middle zone results.
      4. Replace middle tool results with short placeholders.
      5. Drop oldest middle messages.

    Never touches: system prompt (index 0), orientation messages
    (indices 1..boundary), or the last ``protect_last_n`` messages.

    Returns a new list; never mutates the input.
    """
    if config is None:
        config = ContextConfig()

    budget = config.max_context_tokens - config.output_reserve

    # Fast path
    if _total_tokens(messages) <= budget:
        return list(messages)

    # Determine protected ranges
    # Index 0 is always the system prompt
    if config.protect_first_n > 0:
        orientation_end = 1 + config.protect_first_n
    else:
        orientation_end = find_orientation_boundary(messages)

    n = len(messages)
    recent_start = max(orientation_end, n - config.protect_last_n)

    # Middle zone: messages between orientation and recent
    # These are old iteration cycles — least valuable
    result = list(messages)

    # Phase 1: Truncate long tool results in middle zone
    for i in range(orientation_end, recent_start):
        if result[i].get("role") == "tool":
            content = result[i].get("content", "")
            if isinstance(content, str) and len(content) > config.truncate_threshold:
                result[i] = dict(result[i])
                result[i]["content"] = truncate_tool_result(
                    content, config.truncate_threshold
                )

    if _total_tokens(result) <= budget:
        return result

    # Phase 2: Aggressive truncation of middle zone
    for i in range(orientation_end, recent_start):
        if result[i].get("role") == "tool":
            content = result[i].get("content", "")
            if (
                isinstance(content, str)
                and len(content) > config.aggressive_threshold
            ):
                result[i] = dict(result[i])
                result[i]["content"] = truncate_tool_result(
                    content, config.aggressive_threshold
                )

    if _total_tokens(result) <= budget:
        return result

    # Phase 3: Replace middle tool results with placeholders
    for i in range(orientation_end, recent_start):
        if result[i].get("role") == "tool":
            result[i] = dict(result[i])
            result[i]["content"] = "[previous tool result removed to save context]"

    if _total_tokens(result) <= budget:
        return result

    # Phase 4: Drop oldest middle messages
    # Keep system + orientation + recent, drop everything in between
    protected_head = result[:orientation_end]
    protected_tail = result[recent_start:]
    middle = result[orientation_end:recent_start]

    # Drop from oldest first
    while middle and _total_tokens(protected_head + middle + protected_tail) > budget:
        middle.pop(0)

    return protected_head + middle + protected_tail
