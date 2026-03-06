"""Utilities for prefetching m2c output into the first model prompt."""

from __future__ import annotations

from decomp_agent.config import Config
from decomp_agent.tools.m2c_tool import run_m2c

_DEFAULT_MAX_CHARS = 6000
_TRUNCATION_NOTICE = "\n/* ... m2c output truncated for prompt budget ... */\n"


def _truncate_m2c(code: str, max_chars: int) -> str:
    if len(code) <= max_chars:
        return code
    keep = max(0, max_chars - len(_TRUNCATION_NOTICE))
    return code[:keep] + _TRUNCATION_NOTICE


def build_prefetched_m2c_block(
    function_name: str,
    source_file: str,
    config: Config,
    *,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Run m2c once and format its output for inclusion in the first prompt."""
    try:
        m2c = run_m2c(
            function_name,
            source_file,
            config,
            flags=["no_casts", "globals_none", "no_switches"],
        )
    except Exception as e:
        return (
            "\n\nPrefetched m2c output: unavailable "
            f"({type(e).__name__}: {e})."
        )

    if m2c.error:
        error = " ".join(m2c.error.split())
        return f"\n\nPrefetched m2c output: unavailable ({error})."

    code = _truncate_m2c(m2c.c_code or "", max_chars=max_chars)
    return (
        "\n\nPrefetched m2c first-pass output "
        "(use as an initial scaffold, not ground truth):\n\n"
        f"```c\n{code}\n```"
    )

