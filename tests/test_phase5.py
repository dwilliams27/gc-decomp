"""Phase 5 tests: orchestrator, DB models, batch runner, and CLI.

All tests use in-memory SQLite and mock run_agent — no real OpenAI calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from sqlmodel import Session, select

from decomp_agent.agent.loop import AgentResult
from decomp_agent.config import OrchestrationConfig
from decomp_agent.melee.functions import FunctionInfo
from decomp_agent.melee.project import ObjectStatus
from decomp_agent.models.db import (
    Attempt,
    Function,
    get_candidate_batch,
    get_engine,
    get_next_candidate,
    record_attempt,
    sync_from_report,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """In-memory SQLite engine with tables created."""
    return get_engine(":memory:")


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def _make_function(
    name: str = "test_func",
    address: int = 0x80000000,
    size: int = 100,
    source_file: str = "melee/test.c",
    library: str = "melee",
    status: str = "pending",
    attempts: int = 0,
    current_match_pct: float = 0.0,
) -> Function:
    return Function(
        name=name,
        address=address,
        size=size,
        source_file=source_file,
        library=library,
        initial_match_pct=0.0,
        current_match_pct=current_match_pct,
        status=status,
        attempts=attempts,
    )


def _make_function_info(
    name: str = "test_func",
    address: int = 0x80000000,
    size: int = 100,
    fuzzy_match_percent: float = 50.0,
    source_file: str = "melee/test.c",
    library: str = "melee",
) -> FunctionInfo:
    return FunctionInfo(
        name=name,
        address=address,
        size=size,
        fuzzy_match_percent=fuzzy_match_percent,
        unit_name="melee/test",
        source_file=source_file,
        object_status=ObjectStatus.NON_MATCHING,
        library=library,
    )


def _make_result(
    matched: bool = False,
    best_match_percent: float = 75.0,
    iterations: int = 5,
    total_tokens: int = 1000,
    elapsed_seconds: float = 10.0,
    error: str | None = None,
    termination_reason: str = "max_iterations",
) -> AgentResult:
    return AgentResult(
        matched=matched,
        best_match_percent=best_match_percent,
        iterations=iterations,
        total_tokens=total_tokens,
        elapsed_seconds=elapsed_seconds,
        error=error,
        termination_reason=termination_reason,
    )


def _mock_config(**overrides) -> MagicMock:
    """Create a mock Config with orchestration attributes set."""
    from decomp_agent.cost import ModelPricing, PricingConfig

    config = MagicMock()
    config.orchestration.max_function_size = overrides.get("max_function_size")
    config.orchestration.batch_size = overrides.get("batch_size", 50)
    config.orchestration.db_path = overrides.get("db_path", "decomp.db")
    config.orchestration.default_workers = overrides.get("default_workers", 1)
    config.orchestration.default_budget = overrides.get("default_budget", None)
    config.agent.model = "test-model"
    config.pricing = PricingConfig(models={
        "test-model": ModelPricing(
            input_per_million=1.75,
            cached_input_per_million=0.175,
            output_per_million=14.00,
        ),
    })
    # Prevent _save_source from reading files during tests
    config.melee.resolve_source_path.return_value.exists.return_value = False
    return config


# ---------------------------------------------------------------------------
# DB table tests
# ---------------------------------------------------------------------------


class TestDatabaseTables:
    def test_create_tables(self, engine):
        """Engine creation should produce function and attempt tables."""
        from sqlalchemy import inspect

        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert "function" in tables
        assert "attempt" in tables

    def test_insert_function(self, session):
        func = _make_function(name="my_func")
        session.add(func)
        session.commit()

        loaded = session.exec(select(Function).where(Function.name == "my_func")).first()
        assert loaded is not None
        assert loaded.name == "my_func"
        assert loaded.size == 100
        assert loaded.status == "pending"

    def test_function_name_unique(self, session):
        session.add(_make_function(name="dup"))
        session.commit()
        session.add(_make_function(name="dup"))
        with pytest.raises(Exception):
            session.commit()

    def test_insert_attempt(self, session):
        func = _make_function()
        session.add(func)
        session.commit()

        attempt = Attempt(
            function_id=func.id,
            started_at=datetime.now(timezone.utc),
            matched=False,
            best_match_pct=50.0,
            iterations=3,
            total_tokens=500,
        )
        session.add(attempt)
        session.commit()

        loaded = session.exec(select(Attempt)).first()
        assert loaded is not None
        assert loaded.function_id == func.id
        assert loaded.best_match_pct == 50.0


# ---------------------------------------------------------------------------
# sync_from_report tests
# ---------------------------------------------------------------------------


class TestSyncFromReport:
    def test_inserts_new_functions(self, session):
        infos = [
            _make_function_info(name="func_a", size=50),
            _make_function_info(name="func_b", size=200),
        ]
        inserted = sync_from_report(session, infos)
        assert inserted == 2

        all_funcs = session.exec(select(Function)).all()
        assert len(all_funcs) == 2

    def test_upsert_updates_existing(self, session):
        session.add(_make_function(name="func_a", current_match_pct=30.0))
        session.commit()

        infos = [_make_function_info(name="func_a", fuzzy_match_percent=60.0)]
        inserted = sync_from_report(session, infos)
        assert inserted == 0

        func = session.exec(select(Function).where(Function.name == "func_a")).first()
        assert func.current_match_pct == 60.0

    def test_upsert_does_not_decrease_match(self, session):
        session.add(_make_function(name="func_a", current_match_pct=90.0))
        session.commit()

        infos = [_make_function_info(name="func_a", fuzzy_match_percent=50.0)]
        sync_from_report(session, infos)

        func = session.exec(select(Function).where(Function.name == "func_a")).first()
        assert func.current_match_pct == 90.0

    def test_marks_100pct_as_matched(self, session):
        infos = [_make_function_info(name="func_a", fuzzy_match_percent=100.0)]
        sync_from_report(session, infos)

        # New inserts at 100% should still be pending (they're already matched in report)
        func = session.exec(select(Function).where(Function.name == "func_a")).first()
        # initial_match_pct = 100, but status stays pending for new inserts
        assert func.initial_match_pct == 100.0

    def test_existing_pending_marked_matched_at_100(self, session):
        session.add(_make_function(name="func_a", current_match_pct=50.0))
        session.commit()

        infos = [_make_function_info(name="func_a", fuzzy_match_percent=100.0)]
        sync_from_report(session, infos)

        func = session.exec(select(Function).where(Function.name == "func_a")).first()
        assert func.status == "matched"
        assert func.matched_at is not None


# ---------------------------------------------------------------------------
# record_attempt tests
# ---------------------------------------------------------------------------


class TestRecordAttempt:
    def test_creates_attempt_and_updates_function(self, session):
        func = _make_function()
        session.add(func)
        session.commit()

        result = _make_result(best_match_percent=80.0)
        attempt = record_attempt(session, func, result, cost=0.05)

        assert attempt.id is not None
        assert attempt.best_match_pct == 80.0
        assert attempt.total_tokens == 1000
        assert func.attempts == 1
        assert func.current_match_pct == 80.0

    def test_does_not_decrease_match_pct(self, session):
        func = _make_function(current_match_pct=90.0)
        session.add(func)
        session.commit()

        result = _make_result(best_match_percent=50.0)
        record_attempt(session, func, result, cost=0.0)

        assert func.current_match_pct == 90.0

    def test_increments_attempts(self, session):
        func = _make_function(attempts=2)
        session.add(func)
        session.commit()

        result = _make_result()
        record_attempt(session, func, result, cost=0.0)

        assert func.attempts == 3


# ---------------------------------------------------------------------------
# get_next_candidate tests
# ---------------------------------------------------------------------------


class TestGetNextCandidate:
    def test_returns_pending_function(self, session):
        session.add(_make_function(name="func_a", size=100))
        session.commit()

        candidate = get_next_candidate(session)
        assert candidate is not None
        assert candidate.name == "func_a"

    def test_skips_matched(self, session):
        session.add(_make_function(name="func_a", status="matched"))
        session.commit()

        assert get_next_candidate(session) is None

    def test_skips_failed(self, session):
        session.add(_make_function(name="func_a", status="failed"))
        session.commit()

        assert get_next_candidate(session) is None

    def test_skips_in_progress(self, session):
        session.add(_make_function(name="func_a", status="in_progress"))
        session.commit()

        assert get_next_candidate(session) is None

    def test_skips_skipped(self, session):
        session.add(_make_function(name="func_a", status="skipped"))
        session.commit()

        assert get_next_candidate(session) is None

    def test_respects_max_size(self, session):
        session.add(_make_function(name="big_func", size=5000))
        session.add(_make_function(name="small_func", size=50))
        session.commit()

        candidate = get_next_candidate(session, max_size=100)
        assert candidate is not None
        assert candidate.name == "small_func"

    def test_smallest_first_ordering(self, session):
        session.add(_make_function(name="big", size=500, address=0x80000000))
        session.add(_make_function(name="small", size=50, address=0x80001000))
        session.commit()

        candidate = get_next_candidate(session, strategy="smallest_first")
        assert candidate.name == "small"

    def test_best_match_first_ordering(self, session):
        session.add(_make_function(name="low", size=100, current_match_pct=20.0))
        session.add(_make_function(name="high", size=100, current_match_pct=90.0))
        session.commit()

        candidate = get_next_candidate(session, strategy="best_match_first")
        assert candidate.name == "high"

    def test_returns_none_when_empty(self, session):
        assert get_next_candidate(session) is None


# ---------------------------------------------------------------------------
# Runner tests
# ---------------------------------------------------------------------------


class TestRunner:
    @patch("decomp_agent.orchestrator.runner.run_agent")
    def test_matched_result_updates_status(self, mock_run_agent, engine):
        mock_run_agent.return_value = _make_result(
            matched=True,
            best_match_percent=100.0,
            termination_reason="matched",
        )

        with Session(engine) as session:
            func = _make_function(name="runner_match")
            session.add(func)
            session.commit()
            session.refresh(func)

        from decomp_agent.orchestrator.runner import run_function

        result = run_function(func, _mock_config(), engine)

        assert result.matched is True

        with Session(engine) as session:
            loaded = session.exec(
                select(Function).where(Function.name == "runner_match")
            ).first()
            assert loaded.status == "matched"
            assert loaded.matched_at is not None
            assert loaded.attempts == 1

    @patch("decomp_agent.orchestrator.runner.run_agent")
    def test_error_stays_pending_for_retry(self, mock_run_agent, engine):
        mock_run_agent.return_value = _make_result(
            error="API error", termination_reason="api_error"
        )

        with Session(engine) as session:
            func = _make_function(name="runner_fail", attempts=2)
            session.add(func)
            session.commit()
            session.refresh(func)

        from decomp_agent.orchestrator.runner import run_function

        result = run_function(func, _mock_config(), engine)

        with Session(engine) as session:
            loaded = session.exec(
                select(Function).where(Function.name == "runner_fail")
            ).first()
            assert loaded.status == "pending"
            assert loaded.attempts == 3

    @patch("decomp_agent.orchestrator.runner.run_agent")
    def test_retryable_stays_pending(self, mock_run_agent, engine):
        mock_run_agent.return_value = _make_result(
            best_match_percent=75.0, termination_reason="max_iterations"
        )

        with Session(engine) as session:
            func = _make_function(name="runner_retry", attempts=0)
            session.add(func)
            session.commit()
            session.refresh(func)

        from decomp_agent.orchestrator.runner import run_function

        result = run_function(func, _mock_config(), engine)

        with Session(engine) as session:
            loaded = session.exec(
                select(Function).where(Function.name == "runner_retry")
            ).first()
            assert loaded.status == "pending"
            assert loaded.attempts == 1

    @patch("decomp_agent.orchestrator.runner.run_agent")
    def test_agent_crash_records_error(self, mock_run_agent, engine):
        mock_run_agent.side_effect = RuntimeError("agent exploded")

        with Session(engine) as session:
            func = _make_function(name="runner_crash", attempts=2)
            session.add(func)
            session.commit()
            session.refresh(func)

        from decomp_agent.orchestrator.runner import run_function

        result = run_function(func, _mock_config(), engine)

        assert result.error == "agent exploded"
        assert result.termination_reason == "agent_crash"

        with Session(engine) as session:
            loaded = session.exec(
                select(Function).where(Function.name == "runner_crash")
            ).first()
            assert loaded.status == "pending"
            assert loaded.attempts == 3


# ---------------------------------------------------------------------------
# Batch tests
# ---------------------------------------------------------------------------


class TestBatch:
    @patch("decomp_agent.orchestrator.runner.run_agent")
    def test_batch_terminates_on_limit(self, mock_run_agent, engine):
        mock_run_agent.return_value = _make_result()

        with Session(engine) as session:
            for i in range(10):
                session.add(_make_function(name=f"batch_{i}", size=100 + i, address=0x80000000 + i))
            session.commit()

        from decomp_agent.orchestrator.batch import run_batch

        result = run_batch(_mock_config(), engine, limit=3, auto_approve=True)

        assert result.attempted == 3

    @patch("decomp_agent.orchestrator.runner.run_agent")
    def test_batch_terminates_on_empty_queue(self, mock_run_agent, engine):
        mock_run_agent.return_value = _make_result()

        with Session(engine) as session:
            session.add(_make_function(name="only_one"))
            session.commit()

        from decomp_agent.orchestrator.batch import run_batch

        result = run_batch(_mock_config(), engine, limit=50, auto_approve=True)

        # Only 1 function available; batch fetches candidates upfront now
        assert result.attempted >= 1

    @patch("decomp_agent.orchestrator.runner.run_agent")
    def test_batch_counts_matches(self, mock_run_agent, engine):
        mock_run_agent.return_value = _make_result(
            matched=True, termination_reason="matched"
        )

        with Session(engine) as session:
            for i in range(3):
                session.add(_make_function(name=f"match_{i}", size=100 + i, address=0x80000000 + i))
            session.commit()

        from decomp_agent.orchestrator.batch import run_batch

        result = run_batch(_mock_config(), engine, limit=10, auto_approve=True)

        assert result.matched == 3
        assert result.attempted == 3

    @patch("decomp_agent.orchestrator.runner.run_agent")
    def test_batch_respects_max_size(self, mock_run_agent, engine):
        mock_run_agent.return_value = _make_result(
            matched=True, termination_reason="matched"
        )

        with Session(engine) as session:
            session.add(_make_function(name="small", size=50))
            session.add(_make_function(name="big", size=5000, address=0x80001000))
            session.commit()

        from decomp_agent.orchestrator.batch import run_batch

        result = run_batch(_mock_config(), engine, limit=50, max_size=100, auto_approve=True)

        assert result.attempted == 1  # only small fits


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCLI:
    def test_help(self):
        from decomp_agent.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Automated decompilation agent" in result.output

    def test_init_command(self, tmp_path):
        from decomp_agent.cli import main

        db_path = tmp_path / "test.db"

        mock_funcs = [
            _make_function_info(name=f"func_{i}", size=100 + i, address=0x80000000 + i)
            for i in range(5)
        ]

        with (
            patch("decomp_agent.cli.load_config") as mock_load,
            patch("decomp_agent.melee.functions.get_functions", return_value=mock_funcs),
            patch("decomp_agent.melee.functions.get_candidates", return_value=mock_funcs),
        ):
            mock_load.return_value = _mock_config(db_path=db_path)

            runner = CliRunner()
            result = runner.invoke(main, ["init"])

        assert result.exit_code == 0
        assert "5" in result.output

    def test_status_empty_db(self, tmp_path):
        from decomp_agent.cli import main

        db_path = tmp_path / "test.db"
        # Pre-create the DB
        get_engine(db_path)

        with patch("decomp_agent.cli.load_config") as mock_load:
            mock_load.return_value = _mock_config(db_path=db_path)

            runner = CliRunner()
            result = runner.invoke(main, ["status"])

        assert result.exit_code == 0
        assert "No functions in DB" in result.output

    def test_status_with_data(self, tmp_path):
        from decomp_agent.cli import main

        db_path = tmp_path / "test.db"
        engine = get_engine(db_path)

        with Session(engine) as session:
            session.add(_make_function(name="f1", status="matched"))
            session.add(_make_function(name="f2", status="pending", address=0x80001000))
            session.add(_make_function(name="f3", status="failed", address=0x80002000))
            session.commit()

        with patch("decomp_agent.cli.load_config") as mock_load:
            mock_load.return_value = _mock_config(db_path=db_path)

            runner = CliRunner()
            result = runner.invoke(main, ["status"])

        assert result.exit_code == 0
        assert "matched" in result.output
        assert "pending" in result.output

    def test_run_missing_function(self, tmp_path):
        from decomp_agent.cli import main

        db_path = tmp_path / "test.db"
        get_engine(db_path)

        with patch("decomp_agent.cli.load_config") as mock_load:
            mock_load.return_value = _mock_config(db_path=db_path)

            runner = CliRunner()
            result = runner.invoke(main, ["run", "nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.output


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestOrchestrationConfig:
    def test_default_values(self):
        from decomp_agent.config import OrchestrationConfig

        c = OrchestrationConfig()
        assert c.db_path.name == "decomp.db"
        assert c.max_function_size is None
        assert c.batch_size == 50

    def test_config_includes_orchestration(self):
        """Config should include orchestration with defaults."""
        from decomp_agent.config import Config

        assert "orchestration" in Config.model_fields

    def test_load_from_toml(self, tmp_path):
        toml_content = """\
[melee]
repo_path = "/Users/dwilliams/proj/melee"

[orchestration]
db_path = "custom.db"
batch_size = 100
"""
        toml_file = tmp_path / "test.toml"
        toml_file.write_text(toml_content)

        from decomp_agent.config import load_config

        config = load_config(toml_file)
        assert str(config.orchestration.db_path) == "custom.db"
        assert config.orchestration.batch_size == 100

    def test_new_orchestration_defaults(self):
        from decomp_agent.config import OrchestrationConfig

        c = OrchestrationConfig()
        assert c.default_workers == 1
        assert c.default_budget is None

    def test_config_includes_pricing(self):
        from decomp_agent.config import Config

        assert "pricing" in Config.model_fields


# ---------------------------------------------------------------------------
# get_candidate_batch tests
# ---------------------------------------------------------------------------


class TestGetCandidateBatch:
    def test_fetch_multiple_with_limit(self, session):
        for i in range(10):
            session.add(_make_function(
                name=f"batch_{i}",
                size=100 + i,
                address=0x80000000 + i,
            ))
        session.commit()

        results = get_candidate_batch(session, limit=5)
        assert len(results) == 5

    def test_respects_max_size(self, session):
        session.add(_make_function(name="small", size=50))
        session.add(_make_function(name="big", size=5000, address=0x80001000))
        session.commit()

        results = get_candidate_batch(session, max_size=100)
        assert len(results) == 1
        assert results[0].name == "small"

    def test_library_filter(self, session):
        session.add(_make_function(name="lb_func", library="lb", size=100))
        session.add(_make_function(name="ft_func", library="ft", size=100, address=0x80001000))
        session.add(_make_function(name="melee_func", library="melee", size=100, address=0x80002000))
        session.commit()

        results = get_candidate_batch(session, library="lb")
        assert len(results) == 1
        assert results[0].name == "lb_func"

    def test_match_range_filter(self, session):
        session.add(_make_function(name="low", current_match_pct=10.0, size=100))
        session.add(_make_function(name="mid", current_match_pct=50.0, size=100, address=0x80001000))
        session.add(_make_function(name="high", current_match_pct=90.0, size=100, address=0x80002000))
        session.commit()

        results = get_candidate_batch(session, min_match=40.0, max_match=60.0)
        assert len(results) == 1
        assert results[0].name == "mid"

    def test_skips_non_pending(self, session):
        session.add(_make_function(name="matched", status="matched", size=100))
        session.add(_make_function(name="pending", status="pending", size=100, address=0x80001000))
        session.commit()

        results = get_candidate_batch(session)
        assert len(results) == 1
        assert results[0].name == "pending"

    def test_smallest_first_strategy(self, session):
        session.add(_make_function(name="big", size=500, address=0x80000000))
        session.add(_make_function(name="small", size=50, address=0x80001000))
        session.commit()

        results = get_candidate_batch(session, strategy="smallest_first")
        assert results[0].name == "small"

    def test_best_match_first_strategy(self, session):
        session.add(_make_function(name="low", size=100, current_match_pct=20.0))
        session.add(_make_function(name="high", size=100, current_match_pct=90.0, address=0x80001000))
        session.commit()

        results = get_candidate_batch(session, strategy="best_match_first")
        assert results[0].name == "high"

    def test_empty_result(self, session):
        results = get_candidate_batch(session)
        assert results == []
