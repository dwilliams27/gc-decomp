"""Tests for cost estimation and budget enforcement."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from decomp_agent.agent.loop import AgentResult
from decomp_agent.cost import (
    ModelPricing,
    PricingConfig,
    calculate_cost,
    estimate_batch_cost,
    estimate_function_cost,
)
from decomp_agent.models.db import Attempt, Function, get_engine

TEST_MODEL = "test-model"
TEST_PRICING = ModelPricing(
    input_per_million=1.75,
    cached_input_per_million=0.175,
    output_per_million=14.00,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pricing():
    return PricingConfig(models={TEST_MODEL: TEST_PRICING})


@pytest.fixture
def engine():
    return get_engine(":memory:")


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def _make_result(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    model: str = TEST_MODEL,
    **kwargs,
) -> AgentResult:
    return AgentResult(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        total_tokens=input_tokens + output_tokens + cached_tokens,
        model=model,
        **kwargs,
    )


def _make_function(
    name: str = "test_func",
    size: int = 100,
    address: int = 0x80000000,
    source_file: str = "melee/test.c",
    library: str = "melee",
) -> Function:
    return Function(
        name=name,
        address=address,
        size=size,
        source_file=source_file,
        library=library,
        initial_match_pct=0.0,
        current_match_pct=0.0,
    )


# ---------------------------------------------------------------------------
# calculate_cost tests
# ---------------------------------------------------------------------------


class TestCalculateCost:
    def test_known_token_counts(self, pricing):
        """1M input + 1M cached + 1M output should cost a known amount."""
        result = _make_result(
            input_tokens=1_000_000,
            cached_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        cost = calculate_cost(result, pricing)
        # 1.75 + 0.175 + 14.00 = 15.925
        assert cost == pytest.approx(15.925)

    def test_zero_tokens(self, pricing):
        result = _make_result()
        assert calculate_cost(result, pricing) == 0.0

    def test_input_only(self, pricing):
        result = _make_result(input_tokens=500_000)
        cost = calculate_cost(result, pricing)
        # 500k * 1.75 / 1M = 0.875
        assert cost == pytest.approx(0.875)

    def test_output_only(self, pricing):
        result = _make_result(output_tokens=100_000)
        cost = calculate_cost(result, pricing)
        # 100k * 14.00 / 1M = 1.40
        assert cost == pytest.approx(1.40)

    def test_cached_only(self, pricing):
        result = _make_result(cached_tokens=2_000_000)
        cost = calculate_cost(result, pricing)
        # 2M * 0.175 / 1M = 0.35
        assert cost == pytest.approx(0.35)

    def test_custom_pricing(self):
        pricing = PricingConfig(models={
            "custom-model": ModelPricing(
                input_per_million=3.0,
                cached_input_per_million=0.3,
                output_per_million=15.0,
            ),
        })
        result = _make_result(
            input_tokens=1_000_000, output_tokens=1_000_000, model="custom-model"
        )
        cost = calculate_cost(result, pricing)
        assert cost == pytest.approx(18.0)

    def test_no_model_returns_zero(self, pricing):
        """Agent crash with no model set should return 0 cost."""
        result = _make_result(input_tokens=1000, model="")
        assert calculate_cost(result, pricing) == 0.0

    def test_unknown_model_raises(self, pricing):
        result = _make_result(input_tokens=1000, model="unknown-model")
        with pytest.raises(KeyError, match="No pricing configured"):
            calculate_cost(result, pricing)


# ---------------------------------------------------------------------------
# estimate_function_cost tests
# ---------------------------------------------------------------------------


class TestEstimateFunctionCost:
    def test_with_historical_data(self, session, pricing):
        """When historical data exists, use it instead of the heuristic."""
        func = _make_function(name="hist_func", size=200)
        session.add(func)
        session.commit()

        attempt = Attempt(
            function_id=func.id,
            started_at=datetime.now(timezone.utc),
            total_tokens=10_000,
            input_tokens=7_000,
            output_tokens=2_000,
            cached_tokens=1_000,
        )
        session.add(attempt)
        session.commit()

        cost = estimate_function_cost(200, TEST_MODEL, session, pricing)
        assert cost > 0

    def test_no_historical_data(self, session, pricing):
        """Without history, falls back to size * 15 heuristic."""
        cost = estimate_function_cost(100, TEST_MODEL, session, pricing)
        # 100 * 15 = 1500 tokens
        expected_tokens = 1500
        input_t = expected_tokens * 0.7
        cached_t = expected_tokens * 0.1
        output_t = expected_tokens * 0.2
        expected = (
            input_t * 1.75 / 1_000_000
            + cached_t * 0.175 / 1_000_000
            + output_t * 14.00 / 1_000_000
        )
        assert cost == pytest.approx(expected)

    def test_larger_function_costs_more(self, session, pricing):
        """Larger functions should cost more (heuristic)."""
        small_cost = estimate_function_cost(50, TEST_MODEL, session, pricing)
        large_cost = estimate_function_cost(500, TEST_MODEL, session, pricing)
        assert large_cost > small_cost


# ---------------------------------------------------------------------------
# estimate_batch_cost tests
# ---------------------------------------------------------------------------


class TestEstimateBatchCost:
    def test_batch_cost_is_sum(self, session, pricing):
        funcs = [_make_function(name=f"f{i}", size=100 * (i + 1), address=0x80000000 + i) for i in range(3)]
        batch_cost = estimate_batch_cost(funcs, TEST_MODEL, session, pricing)

        individual_sum = sum(estimate_function_cost(f.size, TEST_MODEL, session, pricing) for f in funcs)
        assert batch_cost == pytest.approx(individual_sum)

    def test_empty_batch(self, session, pricing):
        assert estimate_batch_cost([], TEST_MODEL, session, pricing) == 0.0


# ---------------------------------------------------------------------------
# Budget enforcement test
# ---------------------------------------------------------------------------


class TestBudgetEnforcement:
    @patch("decomp_agent.orchestrator.runner.run_agent")
    def test_batch_stops_when_budget_exceeded(self, mock_run_agent, engine):
        """Batch should stop processing when budget is exceeded."""
        mock_run_agent.return_value = AgentResult(
            matched=False,
            best_match_percent=50.0,
            iterations=5,
            total_tokens=100_000,
            input_tokens=70_000,
            output_tokens=20_000,
            cached_tokens=10_000,
            elapsed_seconds=5.0,
            termination_reason="max_iterations",
            model="gpt-5.2-codex",
        )

        with Session(engine) as session:
            for i in range(10):
                session.add(_make_function(
                    name=f"budget_{i}",
                    size=100 + i,
                    address=0x80000000 + i,
                ))
            session.commit()

        from decomp_agent.orchestrator.batch import run_batch

        # Cost per function: 70k * 1.75/1M + 10k * 0.175/1M + 20k * 14.00/1M
        # = 0.1225 + 0.00175 + 0.28 = 0.40425 per function
        # Budget of $1.00 should allow ~2 functions
        config = MagicMock()
        config.orchestration.default_workers = 1
        config.orchestration.default_budget = None
        config.agent.model = "gpt-5.2-codex"
        config.pricing = PricingConfig(models={
            "gpt-5.2-codex": ModelPricing(
                input_per_million=1.75,
                cached_input_per_million=0.175,
                output_per_million=14.00,
            ),
        })
        # Prevent _save_source from reading files during test
        config.melee.resolve_source_path.return_value.exists.return_value = False

        result = run_batch(
            config, engine,
            limit=10,
            budget=1.00,
            auto_approve=True,
        )

        # Should have attempted some but not all 10
        assert result.attempted < 10
        assert result.total_cost > 0
        assert result.total_cost <= 1.00 + 0.5  # Allow one overshoot
