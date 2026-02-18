"""Cost estimation and tracking for decompilation runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel
from sqlmodel import Session

if TYPE_CHECKING:
    from decomp_agent.agent.loop import AgentResult
    from decomp_agent.models.db import Function


class PricingConfig(BaseModel):
    input_per_million: float = 1.75
    cached_input_per_million: float = 0.175
    output_per_million: float = 14.00


def calculate_cost(result: AgentResult, pricing: PricingConfig) -> float:
    """Compute actual dollar cost from an AgentResult's token counts."""
    input_cost = result.input_tokens * pricing.input_per_million / 1_000_000
    cached_cost = result.cached_tokens * pricing.cached_input_per_million / 1_000_000
    output_cost = result.output_tokens * pricing.output_per_million / 1_000_000
    return input_cost + cached_cost + output_cost


def estimate_function_cost(
    size: int, session: Session, pricing: PricingConfig
) -> float:
    """Estimate the cost to decompile a function of the given size.

    Uses historical average tokens for similar-sized functions if available,
    otherwise falls back to a heuristic of size * 15 tokens.
    """
    from decomp_agent.models.db import get_historical_avg_tokens

    # Look at functions within +/- 50% of this size
    low = max(1, int(size * 0.5))
    high = int(size * 1.5)
    avg_tokens = get_historical_avg_tokens(session, size_range=(low, high))

    if avg_tokens is not None:
        total_tokens = avg_tokens
    else:
        total_tokens = size * 15

    # Rough split: 70% input, 10% cached, 20% output
    input_tokens = total_tokens * 0.7
    cached_tokens = total_tokens * 0.1
    output_tokens = total_tokens * 0.2

    input_cost = input_tokens * pricing.input_per_million / 1_000_000
    cached_cost = cached_tokens * pricing.cached_input_per_million / 1_000_000
    output_cost = output_tokens * pricing.output_per_million / 1_000_000
    return input_cost + cached_cost + output_cost


def estimate_batch_cost(
    candidates: list[Function], session: Session, pricing: PricingConfig
) -> float:
    """Estimate total cost for a batch of candidate functions."""
    return sum(
        estimate_function_cost(c.size, session, pricing) for c in candidates
    )
