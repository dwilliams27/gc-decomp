"""End-to-end integration tests for the full agent pipeline.

Mock only two things:
  1. OpenAI API — scripted tool-call sequences (deterministic, no cost)
  2. run_in_repo — subprocess calls that need ninja/dtk/objdiff

Everything else is real: agent loop iteration, token tracking, tool
registry dispatch, Pydantic validation, source file read/write/replace,
assembly extraction, DB operations.

Uses the Responses API (client.responses.create) via ScriptedOpenAI mock.
"""

from __future__ import annotations

import json
import types as _types
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from decomp_agent.agent.loop import AgentResult, run_agent
from decomp_agent.config import Config
from decomp_agent.models.db import Attempt, Function, get_engine, sync_from_report
from decomp_agent.melee.functions import FunctionInfo
from decomp_agent.melee.project import ObjectStatus
from decomp_agent.orchestrator.batch import run_batch
from decomp_agent.orchestrator.runner import run_function

from tests.fixtures.fake_repo import create_fake_repo
from tests.fixtures.openai_mock import ScriptedOpenAI, ScriptedResponse, ScriptedToolCall


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_db(engine, repo_path: Path, config: Config) -> list[Function]:
    """Insert Function rows from the fixture report into the DB."""
    report_path = config.melee.report_path
    report_data = json.loads(report_path.read_text())

    infos = []
    for unit in report_data["units"]:
        for func_data in unit["functions"]:
            infos.append(
                FunctionInfo(
                    name=func_data["name"],
                    address=int(func_data["metadata"]["virtual_address"], 0),
                    size=func_data["size"],
                    fuzzy_match_percent=func_data["fuzzy_match_percent"],
                    unit_name=unit["name"].removeprefix("main/"),
                    source_file="melee/test/testfile.c",
                    object_status=ObjectStatus.NON_MATCHING,
                    library="test (Library)",
                )
            )

    with Session(engine) as session:
        inserted = sync_from_report(session, infos)
        assert inserted == len(infos)

    with Session(engine) as session:
        return list(session.exec(select(Function)).all())


_DEFAULT_SIZES = {"simple_init": 40, "simple_add": 8, "simple_loop": 48}


def _make_check_match_mock(match_side_effects: dict[int, dict[str, float]] | None = None):
    """Create a check_match_via_disasm mock that returns controlled CompileResults.

    Args:
        match_side_effects: Map from call index to per-function match overrides.
            If None, returns 100% for all functions on every call.
    """
    from decomp_agent.tools.build import CompileResult, FunctionMatch

    call_count = [0]

    def mock_check_match(object_name: str, config: Config) -> CompileResult:
        idx = call_count[0]
        call_count[0] += 1

        if match_side_effects and idx in match_side_effects:
            overrides = match_side_effects[idx]
        else:
            overrides = {
                "simple_init": 100.0,
                "simple_add": 100.0,
                "simple_loop": 100.0,
            }

        functions = []
        for name, pct in overrides.items():
            functions.append(FunctionMatch(
                name=name,
                fuzzy_match_percent=pct,
                size=_DEFAULT_SIZES.get(name, 20),
            ))

        return CompileResult(
            object_name=object_name, success=True, functions=functions
        )

    return mock_check_match


# ---------------------------------------------------------------------------
# Test 1: Happy path — agent matches a function
# ---------------------------------------------------------------------------


class TestE2EHappyPathMatch:
    def test_agent_matches_function(self, tmp_path):
        repo_path, config = create_fake_repo(tmp_path)
        config.agent.max_iterations = 10
        engine = get_engine(":memory:")
        functions = _seed_db(engine, repo_path, config)

        target_func = next(f for f in functions if f.name == "simple_add")

        # Scripted sequence: orient -> read -> write -> compile -> mark_complete
        scripted = ScriptedOpenAI([
            # 1. Get target assembly
            ScriptedResponse(tool_calls=[
                ScriptedToolCall("get_target_assembly", {
                    "function_name": "simple_add",
                    "source_file": "melee/test/testfile.c",
                }),
            ]),
            # 2. Read the source file
            ScriptedResponse(tool_calls=[
                ScriptedToolCall("read_source_file", {
                    "source_file": "melee/test/testfile.c",
                }),
            ]),
            # 3. Write improved function + compile
            ScriptedResponse(tool_calls=[
                ScriptedToolCall("write_function", {
                    "source_file": "melee/test/testfile.c",
                    "function_name": "simple_add",
                    "code": "s32 simple_add(s32 a, s32 b) {\n    return a + b;\n}",
                }),
                ScriptedToolCall("compile_and_check", {
                    "source_file": "melee/test/testfile.c",
                }),
            ]),
            # 4. Mark complete
            ScriptedResponse(tool_calls=[
                ScriptedToolCall("mark_complete", {
                    "function_name": "simple_add",
                    "source_file": "melee/test/testfile.c",
                }),
            ]),
        ])

        mock_match = _make_check_match_mock()

        with (
            patch("decomp_agent.agent.loop.OpenAI", return_value=scripted),
            patch("decomp_agent.tools.disasm.check_match_via_disasm", side_effect=mock_match),
        ):
            result = run_function(target_func, config, engine)

        # Assert AgentResult
        assert result.matched is True
        assert result.termination_reason == "matched"
        assert result.best_match_percent == 100.0
        assert result.iterations >= 1

        # Assert DB state
        with Session(engine) as session:
            loaded = session.exec(
                select(Function).where(Function.name == "simple_add")
            ).first()
            assert loaded.status == "matched"
            assert loaded.matched_at is not None
            assert loaded.attempts == 1
            assert loaded.current_match_pct == 100.0

        # Assert Attempt row created
        with Session(engine) as session:
            func = session.exec(
                select(Function).where(Function.name == "simple_add")
            ).first()
            attempt = session.exec(
                select(Attempt).where(Attempt.function_id == func.id)
            ).first()
            assert attempt is not None
            assert attempt.matched is True
            assert attempt.termination_reason == "matched"

        # Assert source file was actually modified on disk
        src_path = repo_path / "src" / "melee" / "test" / "testfile.c"
        source = src_path.read_text()
        assert "simple_add" in source


# ---------------------------------------------------------------------------
# Test 2: Max iterations then retry then fail
# ---------------------------------------------------------------------------


class TestE2EMaxIterationsRetryThenFail:
    def test_exhausts_iterations_and_retries(self, tmp_path):
        repo_path, config = create_fake_repo(tmp_path)
        config.agent.max_iterations = 3
        config.orchestration.max_attempts_per_function = 2
        engine = get_engine(":memory:")
        functions = _seed_db(engine, repo_path, config)

        target_func = next(f for f in functions if f.name == "simple_loop")

        # Each attempt: 3 iterations of write+compile, never reaching 100%
        def _make_attempt_script():
            return [
                ScriptedResponse(tool_calls=[
                    ScriptedToolCall("write_function", {
                        "source_file": "melee/test/testfile.c",
                        "function_name": "simple_loop",
                        "code": "s32 simple_loop(s32 n) {\n    s32 result = 0;\n    s32 i;\n    for (i = 0; i < n; i++) {\n        result += i;\n    }\n    return result;\n}",
                    }),
                    ScriptedToolCall("compile_and_check", {
                        "source_file": "melee/test/testfile.c",
                    }),
                ]),
            ] * 3  # 3 iterations

        # Match side effects: ascending but never 100%
        match_effects = {
            0: {"simple_loop": 60.0, "simple_init": 55.0, "simple_add": 60.0},
            1: {"simple_loop": 75.0, "simple_init": 55.0, "simple_add": 60.0},
            2: {"simple_loop": 80.0, "simple_init": 55.0, "simple_add": 60.0},
            3: {"simple_loop": 65.0, "simple_init": 55.0, "simple_add": 60.0},
            4: {"simple_loop": 78.0, "simple_init": 55.0, "simple_add": 60.0},
            5: {"simple_loop": 82.0, "simple_init": 55.0, "simple_add": 60.0},
        }

        mock_match = _make_check_match_mock(match_effects)

        # --- Attempt 1 ---
        script1 = ScriptedOpenAI(_make_attempt_script())
        with (
            patch("decomp_agent.agent.loop.OpenAI", return_value=script1),
            patch("decomp_agent.tools.disasm.check_match_via_disasm", side_effect=mock_match),
        ):
            result1 = run_function(target_func, config, engine)

        assert result1.matched is False
        assert result1.termination_reason == "max_iterations"
        assert result1.best_match_percent >= 60.0

        # Check DB after attempt 1: status should be pending (retries remain)
        with Session(engine) as session:
            loaded = session.exec(
                select(Function).where(Function.name == "simple_loop")
            ).first()
            assert loaded.status == "pending"
            assert loaded.attempts == 1
            assert loaded.current_match_pct >= 60.0

        # --- Attempt 2 ---
        # Need to reload the function for the next run
        with Session(engine) as session:
            target_func = session.exec(
                select(Function).where(Function.name == "simple_loop")
            ).first()

        script2 = ScriptedOpenAI(_make_attempt_script())
        mock_match2 = _make_check_match_mock(match_effects)
        with (
            patch("decomp_agent.agent.loop.OpenAI", return_value=script2),
            patch("decomp_agent.tools.disasm.check_match_via_disasm", side_effect=mock_match2),
        ):
            result2 = run_function(target_func, config, engine)

        assert result2.matched is False

        # Check DB after attempt 2: status should be failed (max attempts reached)
        with Session(engine) as session:
            loaded = session.exec(
                select(Function).where(Function.name == "simple_loop")
            ).first()
            assert loaded.status == "failed"
            assert loaded.attempts == 2


# ---------------------------------------------------------------------------
# Test 3: Agent crash records error
# ---------------------------------------------------------------------------


class TestE2EAgentCrashRecordsError:
    def test_crash_in_loop_records_error(self, tmp_path):
        repo_path, config = create_fake_repo(tmp_path)
        config.orchestration.max_attempts_per_function = 3
        engine = get_engine(":memory:")
        functions = _seed_db(engine, repo_path, config)

        target_func = next(f for f in functions if f.name == "simple_init")

        # Update to attempts=2 so next failure triggers "failed" status
        with Session(engine) as session:
            session.add(target_func)
            target_func.attempts = 2
            session.commit()
            session.refresh(target_func)

        # Mock that returns a response with empty output list
        # This causes the loop to see no function_calls, so it stops
        # with model_stopped. To get a real crash, we return an output
        # item that triggers an AttributeError when accessed.
        def _crashy_openai_factory():
            client = ScriptedOpenAI([])
            # Override create to return a response where iterating
            # output raises an error (simulating API corruption)
            original_create = client.responses.create

            def crash_create(**kwargs):
                return _types.SimpleNamespace(
                    id="resp_crash",
                    output=[
                        # Item with type that will pass the filter but
                        # missing 'name' attr → AttributeError in dispatch
                        _types.SimpleNamespace(
                            type="function_call",
                            call_id="call_crash",
                            name="nonexistent_tool_crash",
                            arguments="INVALID JSON {{{",
                        ),
                    ],
                    output_text="",
                    usage=_types.SimpleNamespace(
                        input_tokens=100,
                        output_tokens=100,
                        total_tokens=200,
                    ),
                )

            client.responses.create = crash_create
            return client

        crash_client = _crashy_openai_factory()

        with patch("decomp_agent.agent.loop.OpenAI", return_value=crash_client):
            result = run_function(target_func, config, engine)

        # The agent should handle the bad tool gracefully (dispatch returns error string)
        # but will loop forever since we only have one crash response.
        # Since the dispatch returns an error string for invalid JSON,
        # and the model keeps returning the same crash response,
        # it'll hit max_iterations.
        # Either way, runner.py should record the result.
        assert result.error is not None or result.termination_reason in (
            "agent_crash", "max_iterations", "model_stopped",
        )

        # DB should be updated
        with Session(engine) as session:
            loaded = session.exec(
                select(Function).where(Function.name == "simple_init")
            ).first()
            assert loaded.status == "failed"
            assert loaded.attempts == 3

            # Attempt row should exist
            attempt = session.exec(
                select(Attempt).where(Attempt.function_id == loaded.id)
            ).first()
            assert attempt is not None


# ---------------------------------------------------------------------------
# Test 4: Batch with mixed outcomes
# ---------------------------------------------------------------------------


class TestE2EBatchMixedOutcomes:
    def test_batch_processes_multiple_functions(self, tmp_path):
        repo_path, config = create_fake_repo(tmp_path)
        config.agent.max_iterations = 5
        config.orchestration.max_attempts_per_function = 1
        engine = get_engine(":memory:")
        _seed_db(engine, repo_path, config)

        # Build separate scripts for each function.
        # The agent loop creates a new OpenAI() client per call to run_agent,
        # so we use side_effect to return a different ScriptedOpenAI each time.

        # Function 1 (simple_add, smallest): matches
        script_match = ScriptedOpenAI([
            ScriptedResponse(tool_calls=[
                ScriptedToolCall("write_function", {
                    "source_file": "melee/test/testfile.c",
                    "function_name": "simple_add",
                    "code": "s32 simple_add(s32 a, s32 b) {\n    return a + b;\n}",
                }),
                ScriptedToolCall("compile_and_check", {
                    "source_file": "melee/test/testfile.c",
                }),
            ]),
            ScriptedResponse(tool_calls=[
                ScriptedToolCall("mark_complete", {
                    "function_name": "simple_add",
                    "source_file": "melee/test/testfile.c",
                }),
            ]),
        ])

        # Function 2 (simple_init): model stops (no tool calls)
        script_stop = ScriptedOpenAI([
            ScriptedResponse(content="I cannot match this function."),
        ])

        # Function 3 (simple_loop): exhausts iterations
        script_exhaust = ScriptedOpenAI([
            ScriptedResponse(tool_calls=[
                ScriptedToolCall("write_function", {
                    "source_file": "melee/test/testfile.c",
                    "function_name": "simple_loop",
                    "code": "s32 simple_loop(s32 n) {\n    s32 result = 0;\n    s32 i;\n    for (i = 0; i < n; i++) {\n        result += i;\n    }\n    return result;\n}",
                }),
                ScriptedToolCall("compile_and_check", {
                    "source_file": "melee/test/testfile.c",
                }),
            ]),
        ] * 5)

        clients = iter([script_match, script_stop, script_exhaust])

        match_effects = {
            # First call for simple_add: 100% match
            0: {"simple_add": 100.0, "simple_init": 55.0, "simple_loop": 50.0},
            # mark_complete for simple_add
            1: {"simple_add": 100.0, "simple_init": 55.0, "simple_loop": 50.0},
            # Subsequent calls for simple_loop: never 100%
            2: {"simple_loop": 70.0, "simple_init": 55.0, "simple_add": 100.0},
            3: {"simple_loop": 72.0, "simple_init": 55.0, "simple_add": 100.0},
            4: {"simple_loop": 74.0, "simple_init": 55.0, "simple_add": 100.0},
            5: {"simple_loop": 76.0, "simple_init": 55.0, "simple_add": 100.0},
            6: {"simple_loop": 78.0, "simple_init": 55.0, "simple_add": 100.0},
        }
        mock_match = _make_check_match_mock(match_effects)

        with (
            patch("decomp_agent.agent.loop.OpenAI", side_effect=lambda: next(clients)),
            patch("decomp_agent.tools.disasm.check_match_via_disasm", side_effect=mock_match),
        ):
            batch_result = run_batch(config, engine, limit=3, auto_approve=True)

        assert batch_result.attempted == 3
        assert batch_result.matched >= 1

        # Check DB statuses
        with Session(engine) as session:
            add_func = session.exec(
                select(Function).where(Function.name == "simple_add")
            ).first()
            assert add_func.status == "matched"

            init_func = session.exec(
                select(Function).where(Function.name == "simple_init")
            ).first()
            # model_stopped with max_attempts=1 -> failed
            assert init_func.status == "failed"

            loop_func = session.exec(
                select(Function).where(Function.name == "simple_loop")
            ).first()
            # max_iterations with max_attempts=1 -> failed
            assert loop_func.status == "failed"


# ---------------------------------------------------------------------------
# Test 5: Many iterations survive (Responses API handles context)
# ---------------------------------------------------------------------------


class TestE2EManyIterationsSurvive:
    def test_many_iterations_complete_without_crash(self, tmp_path):
        repo_path, config = create_fake_repo(tmp_path)
        config.agent.max_iterations = 15

        # 1 orientation call + 10 write+compile cycles + model stops
        responses = [
            ScriptedResponse(tool_calls=[
                ScriptedToolCall("read_source_file", {
                    "source_file": "melee/test/testfile.c",
                }),
            ]),
        ]
        for _ in range(10):
            responses.append(ScriptedResponse(tool_calls=[
                ScriptedToolCall("write_function", {
                    "source_file": "melee/test/testfile.c",
                    "function_name": "simple_init",
                    "code": "void simple_init(s32* buf, s32 count) {\n    s32 i;\n    for (i = 0; i < count; i++) {\n        buf[i] = 0;\n    }\n}",
                }),
                ScriptedToolCall("compile_and_check", {
                    "source_file": "melee/test/testfile.c",
                }),
            ]))
        # Then model stops
        responses.append(ScriptedResponse(content="Giving up."))

        scripted = ScriptedOpenAI(responses)
        mock_match = _make_check_match_mock({
            i: {"simple_init": 70.0 + i, "simple_add": 60.0, "simple_loop": 50.0}
            for i in range(10)
        })

        with (
            patch("decomp_agent.agent.loop.OpenAI", return_value=scripted),
            patch("decomp_agent.tools.disasm.check_match_via_disasm", side_effect=mock_match),
        ):
            result = run_agent(
                "simple_init",
                "melee/test/testfile.c",
                config,
            )

        # Should complete without crash
        assert result.termination_reason == "model_stopped"
        assert result.iterations >= 11

        # Verify previous_response_id was passed on subsequent calls
        calls = scripted.calls
        assert len(calls) >= 11
        # First call should not have previous_response_id
        assert "previous_response_id" not in calls[0] or calls[0].get("previous_response_id") is None
        # Later calls should have it
        assert calls[1].get("previous_response_id") is not None


# ---------------------------------------------------------------------------
# Test 6: Token budget exhaustion
# ---------------------------------------------------------------------------


class TestE2ETokenBudgetExhaustion:
    def test_stops_at_token_budget(self, tmp_path):
        repo_path, config = create_fake_repo(tmp_path)
        config.agent.max_iterations = 20
        config.agent.max_tokens_per_attempt = 1500

        # Each response reports 600 tokens -> after 3 iterations: 1800 >= 1500
        responses = []
        for _ in range(10):
            responses.append(ScriptedResponse(
                tool_calls=[
                    ScriptedToolCall("read_source_file", {
                        "source_file": "melee/test/testfile.c",
                    }),
                ],
                tokens=600,
            ))

        scripted = ScriptedOpenAI(responses)

        with patch("decomp_agent.agent.loop.OpenAI", return_value=scripted):
            result = run_agent(
                "simple_init",
                "melee/test/testfile.c",
                config,
            )

        assert result.termination_reason == "token_budget"
        # 600 * 3 = 1800 >= 1500, so should stop at iteration 3
        assert result.iterations <= 3
        assert result.total_tokens >= 1500
