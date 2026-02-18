"""Phase 4 tests: agent loop components.

Tests schemas, registry dispatch, context management, prompts, and
AgentResult. No live OpenAI calls.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from openai import pydantic_function_tool

from decomp_agent.agent.context_mgmt import (
    ContextConfig,
    estimate_tokens,
    find_orientation_boundary,
    manage_context,
    truncate_tool_result,
)
from decomp_agent.agent.loop import AgentResult, _target_function_matched, _update_best_match
from decomp_agent.agent.prompts import SYSTEM_PROMPT, build_system_prompt
from decomp_agent.tools.schemas import (
    CompileAndCheckParams,
    GetContextParams,
    GetDiffParams,
    GetGhidraDecompilationParams,
    GetM2CDecompilationParams,
    GetTargetAssemblyParams,
    MarkCompleteParams,
    ReadSourceFileParams,
    RunPermuterParams,
    WriteFunctionParams,
)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


ALL_SCHEMAS = [
    GetTargetAssemblyParams,
    GetGhidraDecompilationParams,
    GetM2CDecompilationParams,
    GetContextParams,
    ReadSourceFileParams,
    WriteFunctionParams,
    CompileAndCheckParams,
    GetDiffParams,
    RunPermuterParams,
    MarkCompleteParams,
]


class TestSchemas:
    """Verify all 10 schemas produce valid OpenAI tool definitions."""

    @pytest.mark.parametrize("schema", ALL_SCHEMAS, ids=lambda s: s.__name__)
    def test_pydantic_function_tool_generates_valid_schema(self, schema):
        """Each schema should produce a dict with type, function, name."""
        tool_def = pydantic_function_tool(schema)
        assert tool_def["type"] == "function"
        assert "function" in tool_def
        fn = tool_def["function"]
        assert "name" in fn
        assert "parameters" in fn
        # Parameters should have "properties" (at minimum)
        params = fn["parameters"]
        assert "properties" in params

    def test_schema_count(self):
        assert len(ALL_SCHEMAS) == 10

    def test_write_function_has_code_field(self):
        tool_def = pydantic_function_tool(WriteFunctionParams)
        props = tool_def["function"]["parameters"]["properties"]
        assert "code" in props
        assert "function_name" in props
        assert "source_file" in props

    def test_get_target_assembly_fields(self):
        tool_def = pydantic_function_tool(GetTargetAssemblyParams)
        props = tool_def["function"]["parameters"]["properties"]
        assert "function_name" in props
        assert "source_file" in props


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config():
    """Create a minimal mock Config for registry tests."""
    config = MagicMock()
    config.melee.repo_path = MagicMock()
    config.melee.repo_path.__truediv__ = MagicMock(return_value=MagicMock())
    config.melee.build_dir = "build"
    config.melee.version = "GALE01"
    config.ghidra.enabled = False
    return config


class TestRegistry:
    def test_build_registry_has_10_tools(self, mock_config):
        from decomp_agent.tools.registry import build_registry

        registry = build_registry(mock_config)
        tools = registry.get_openai_tools()
        assert len(tools) == 10

    def test_dispatch_unknown_tool(self, mock_config):
        from decomp_agent.tools.registry import build_registry

        registry = build_registry(mock_config)
        result = registry.dispatch("nonexistent_tool", "{}")
        assert "unknown tool" in result.lower()

    def test_dispatch_invalid_json(self, mock_config):
        from decomp_agent.tools.registry import build_registry

        registry = build_registry(mock_config)
        result = registry.dispatch("get_target_assembly", "not json")
        assert "invalid JSON" in result

    def test_dispatch_invalid_args(self, mock_config):
        from decomp_agent.tools.registry import build_registry

        registry = build_registry(mock_config)
        # Missing required field
        result = registry.dispatch("get_target_assembly", '{"function_name": "foo"}')
        assert "invalid arguments" in result.lower() or "Error" in result

    @patch("decomp_agent.tools.build.check_match")
    def test_dispatch_mark_complete_verified(self, mock_check, mock_config):
        """mark_complete should verify via check_match and confirm a true match."""
        from decomp_agent.tools.build import CompileResult, FunctionMatch
        from decomp_agent.tools.registry import build_registry

        mock_check.return_value = CompileResult(
            object_name="melee/test.c",
            success=True,
            functions=[FunctionMatch(name="my_func", fuzzy_match_percent=100.0, size=64)],
        )

        registry = build_registry(mock_config)
        result = registry.dispatch(
            "mark_complete",
            json.dumps(
                {"function_name": "my_func", "source_file": "melee/test.c"}
            ),
        )
        assert "verified" in result.lower()
        assert "confirmed match" in result.lower()

    @patch("decomp_agent.tools.build.check_match")
    def test_dispatch_mark_complete_rejects_non_match(self, mock_check, mock_config):
        """mark_complete should reject functions that aren't truly matched."""
        from decomp_agent.tools.build import CompileResult, FunctionMatch
        from decomp_agent.tools.registry import build_registry

        mock_check.return_value = CompileResult(
            object_name="melee/test.c",
            success=True,
            functions=[FunctionMatch(name="my_func", fuzzy_match_percent=99.96, size=64)],
        )

        registry = build_registry(mock_config)
        result = registry.dispatch(
            "mark_complete",
            json.dumps(
                {"function_name": "my_func", "source_file": "melee/test.c"}
            ),
        )
        assert "not matched" in result.lower()
        assert "99.96" in result

    def test_dispatch_handler_exception_surfaced(self, mock_config):
        """Handler exceptions should be returned as error strings, not raised."""
        from decomp_agent.tools.registry import ToolRegistry
        from decomp_agent.tools.schemas import MarkCompleteParams

        registry = ToolRegistry(mock_config)

        def bad_handler(params, config):
            raise RuntimeError("something broke")

        registry.register("bad_tool", MarkCompleteParams, bad_handler)
        result = registry.dispatch(
            "bad_tool",
            json.dumps(
                {"function_name": "foo", "source_file": "melee/fake.c"}
            ),
        )
        assert "Error" in result
        assert "something broke" in result

    def test_openai_tools_have_correct_names(self, mock_config):
        from decomp_agent.tools.registry import build_registry

        registry = build_registry(mock_config)
        tools = registry.get_openai_tools()
        names = {t["function"]["name"] for t in tools}
        expected = {
            "get_target_assembly",
            "get_ghidra_decompilation",
            "get_m2c_decompilation",
            "get_context",
            "read_source_file",
            "write_function",
            "compile_and_check",
            "get_diff",
            "run_permuter",
            "mark_complete",
        }
        assert names == expected

    def test_dispatch_normalizes_source_file(self, mock_config):
        """dispatch() should strip 'src/' prefix from source_file paths."""
        from decomp_agent.tools.registry import ToolRegistry
        from decomp_agent.tools.schemas import MarkCompleteParams

        registry = ToolRegistry(mock_config)
        captured = {}

        def capturing_handler(params, config):
            captured["source_file"] = params.source_file
            return "ok"

        registry.register("mark_complete", MarkCompleteParams, capturing_handler)
        registry.dispatch(
            "mark_complete",
            json.dumps(
                {"function_name": "foo", "source_file": "src/melee/test.c"}
            ),
        )
        assert captured["source_file"] == "melee/test.c"

    def test_dispatch_preserves_correct_paths(self, mock_config):
        """dispatch() should not alter already-correct source_file paths."""
        from decomp_agent.tools.registry import ToolRegistry
        from decomp_agent.tools.schemas import MarkCompleteParams

        registry = ToolRegistry(mock_config)
        captured = {}

        def capturing_handler(params, config):
            captured["source_file"] = params.source_file
            return "ok"

        registry.register("mark_complete", MarkCompleteParams, capturing_handler)
        registry.dispatch(
            "mark_complete",
            json.dumps(
                {"function_name": "foo", "source_file": "melee/lb/lbcommand.c"}
            ),
        )
        assert captured["source_file"] == "melee/lb/lbcommand.c"

    def test_responses_api_tools_have_correct_format(self, mock_config):
        from decomp_agent.tools.registry import build_registry

        registry = build_registry(mock_config)
        tools = registry.get_responses_api_tools()
        assert len(tools) == 10
        for t in tools:
            assert t["type"] == "function"
            # Responses API format: name is at top level, not nested
            assert "name" in t
            assert "parameters" in t
            assert "function" not in t  # not nested like chat completions

        names = {t["name"] for t in tools}
        expected = {
            "get_target_assembly",
            "get_ghidra_decompilation",
            "get_m2c_decompilation",
            "get_context",
            "read_source_file",
            "write_function",
            "compile_and_check",
            "get_diff",
            "run_permuter",
            "mark_complete",
        }
        assert names == expected


# ---------------------------------------------------------------------------
# Context management tests
# ---------------------------------------------------------------------------


def _make_msg(role: str, content: str, tool_calls: list | None = None) -> dict:
    """Helper to build a message dict."""
    msg = {"role": role, "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _make_tool_call(name: str, args: str = "{}") -> dict:
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


class TestContextManagement:
    def test_estimate_tokens(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("a" * 400) == 100

    def test_truncate_tool_result_short(self):
        """Short content should pass through unchanged."""
        result = truncate_tool_result("short text", 100)
        assert result == "short text"

    def test_truncate_tool_result_long(self):
        """Long content should be truncated with notice."""
        content = "x" * 10_000
        result = truncate_tool_result(content, 1_000)
        assert len(result) < len(content)
        assert "truncated" in result

    def test_find_orientation_boundary_no_writes(self):
        """Without write_function/compile_and_check, everything is orientation."""
        messages = [
            _make_msg("system", "prompt"),
            _make_msg(
                "assistant",
                None,
                [_make_tool_call("get_target_assembly")],
            ),
            _make_msg("tool", "asm here"),
        ]
        assert find_orientation_boundary(messages) == len(messages)

    def test_find_orientation_boundary_with_write(self):
        messages = [
            _make_msg("system", "prompt"),
            _make_msg(
                "assistant",
                None,
                [_make_tool_call("get_target_assembly")],
            ),
            _make_msg("tool", "asm here"),
            _make_msg(
                "assistant",
                None,
                [_make_tool_call("write_function")],
            ),
            _make_msg("tool", "success"),
        ]
        assert find_orientation_boundary(messages) == 3

    def test_manage_context_under_budget_passthrough(self):
        """Messages under budget should pass through unchanged."""
        messages = [
            _make_msg("system", "prompt"),
            _make_msg("user", "hello"),
            _make_msg("assistant", "hi"),
        ]
        config = ContextConfig(max_context_tokens=1_000_000)
        result = manage_context(messages, config)
        assert len(result) == len(messages)
        assert result[0]["content"] == "prompt"

    def test_manage_context_preserves_system_and_recent(self):
        """System prompt and last N messages should always be preserved."""
        messages = [_make_msg("system", "system prompt")]

        # Add orientation
        messages.append(
            _make_msg(
                "assistant",
                None,
                [_make_tool_call("get_target_assembly")],
            )
        )
        messages.append(_make_msg("tool", "asm"))

        # Add iteration phase with large tool results
        messages.append(
            _make_msg(
                "assistant",
                None,
                [_make_tool_call("write_function")],
            )
        )
        for _ in range(20):
            messages.append(_make_msg("tool", "x" * 50_000))
            messages.append(
                _make_msg(
                    "assistant",
                    None,
                    [_make_tool_call("compile_and_check")],
                )
            )

        # Recent messages
        for _ in range(6):
            messages.append(_make_msg("tool", "recent result"))
            messages.append(
                _make_msg("assistant", None, [_make_tool_call("get_diff")])
            )

        config = ContextConfig(
            max_context_tokens=10_000,
            protect_last_n=12,
        )
        result = manage_context(messages, config)

        # System prompt preserved
        assert result[0]["content"] == "system prompt"
        # Last messages preserved
        assert result[-1] == messages[-1]

    def test_manage_context_returns_new_list(self):
        """manage_context should never mutate the input list."""
        messages = [
            _make_msg("system", "prompt"),
            _make_msg("user", "hello"),
        ]
        original = list(messages)
        manage_context(messages)
        assert messages == original


# ---------------------------------------------------------------------------
# Prompt tests
# ---------------------------------------------------------------------------


class TestPrompts:
    def test_system_prompt_has_content(self):
        assert len(SYSTEM_PROMPT) > 100
        assert "CodeWarrior" in SYSTEM_PROMPT

    def test_build_system_prompt_includes_function(self):
        prompt = build_system_prompt("my_func", "melee/test.c")
        assert "my_func" in prompt
        assert "melee/test.c" in prompt

    def test_build_system_prompt_has_assignment_section(self):
        prompt = build_system_prompt("my_func", "melee/test.c")
        assert "Your Assignment" in prompt

    def test_system_prompt_lists_all_tools(self):
        tool_names = [
            "get_target_assembly",
            "get_ghidra_decompilation",
            "get_m2c_decompilation",
            "get_context",
            "read_source_file",
            "write_function",
            "compile_and_check",
            "get_diff",
            "run_permuter",
            "mark_complete",
        ]
        for name in tool_names:
            assert name in SYSTEM_PROMPT, f"Tool {name} missing from prompt"


# ---------------------------------------------------------------------------
# AgentResult tests
# ---------------------------------------------------------------------------


class TestAgentResult:
    def test_defaults(self):
        r = AgentResult()
        assert r.matched is False
        assert r.best_match_percent == 0.0
        assert r.iterations == 0
        assert r.total_tokens == 0
        assert r.input_tokens == 0
        assert r.output_tokens == 0
        assert r.cached_tokens == 0
        assert r.elapsed_seconds == 0.0
        assert r.final_code is None
        assert r.error is None
        assert r.termination_reason == ""


# ---------------------------------------------------------------------------
# _update_best_match tests
# ---------------------------------------------------------------------------


class TestUpdateBestMatch:
    def test_ignores_non_compile_tools(self):
        result = _update_best_match("get_diff", "func_a: 50.0%", 0.0, "func_a")
        assert result == 0.0

    def test_parses_target_percentage(self):
        text = "  func_a: 85.3% (size: 120)\n  func_b: 92.1% (size: 80)"
        result = _update_best_match("compile_and_check", text, 0.0, "func_b")
        assert result == 92.1

    def test_ignores_non_target_percentage(self):
        text = "  func_a: 85.3% (size: 120)\n  func_b: 92.1% (size: 80)"
        result = _update_best_match("compile_and_check", text, 0.0, "func_a")
        assert result == 85.3

    def test_all_functions_match(self):
        text = "Compilation successful.\n\nAll functions match!"
        result = _update_best_match("compile_and_check", text, 50.0, "any_func")
        assert result == 100.0

    def test_keeps_previous_best(self):
        text = "  func_a: 60.0% (size: 100)"
        result = _update_best_match("compile_and_check", text, 90.0, "func_a")
        assert result == 90.0

    def test_compilation_failure(self):
        text = "Compilation failed:\nerror: undeclared identifier"
        result = _update_best_match("compile_and_check", text, 75.0, "func_a")
        assert result == 75.0

    def test_match_status_line(self):
        text = "  func_a: MATCH (size: 100)"
        result = _update_best_match("compile_and_check", text, 0.0, "func_a")
        assert result == 100.0

    def test_other_func_match_not_counted(self):
        """Another function's MATCH should not affect target's best_match."""
        text = (
            "  func_a: MATCH (size: 100)\n"
            "  func_b: 85.0% (size: 200)"
        )
        result = _update_best_match("compile_and_check", text, 0.0, "func_b")
        assert result == 85.0

    def test_target_match_with_other_percentages(self):
        text = (
            "  func_a: MATCH (size: 100)\n"
            "  func_b: 85.0% (size: 200)"
        )
        result = _update_best_match("compile_and_check", text, 0.0, "func_a")
        assert result == 100.0


# ---------------------------------------------------------------------------
# _target_function_matched tests
# ---------------------------------------------------------------------------


class TestTargetFunctionMatched:
    def test_ignores_non_compile_tools(self):
        assert _target_function_matched("get_diff", "func_a: MATCH", "func_a") is False

    def test_all_functions_match(self):
        text = "Compilation successful.\n\nAll functions match!"
        assert _target_function_matched("compile_and_check", text, "anything") is True

    def test_specific_function_match(self):
        text = "  func_a: MATCH (size: 100)\n  func_b: 85.0% (size: 200)"
        assert _target_function_matched("compile_and_check", text, "func_a") is True

    def test_different_function_match_not_target(self):
        text = "  func_a: MATCH (size: 100)\n  func_b: 85.0% (size: 200)"
        assert _target_function_matched("compile_and_check", text, "func_b") is False

    def test_no_match_at_all(self):
        text = "  func_a: 60.0% (size: 100)\n  func_b: 85.0% (size: 200)"
        assert _target_function_matched("compile_and_check", text, "func_a") is False

    def test_compilation_failure(self):
        text = "Compilation failed:\nerror: undeclared identifier"
        assert _target_function_matched("compile_and_check", text, "func_a") is False

    def test_multiple_matches_includes_target(self):
        text = (
            "  func_a: MATCH (size: 100)\n"
            "  func_b: MATCH (size: 200)\n"
            "  func_c: 50.0% (size: 300)"
        )
        assert _target_function_matched("compile_and_check", text, "func_b") is True


# ---------------------------------------------------------------------------
# _normalize_source_file tests
# ---------------------------------------------------------------------------


class TestNormalizeSourceFile:
    def test_strips_src_prefix(self):
        from decomp_agent.tools.registry import _normalize_source_file

        assert _normalize_source_file("src/melee/lb/lbcommand.c") == "melee/lb/lbcommand.c"

    def test_preserves_correct_path(self):
        from decomp_agent.tools.registry import _normalize_source_file

        assert _normalize_source_file("melee/lb/lbcommand.c") == "melee/lb/lbcommand.c"

    def test_handles_dolphin_path(self):
        from decomp_agent.tools.registry import _normalize_source_file

        assert _normalize_source_file("src/dolphin/os/os.c") == "dolphin/os/os.c"

    def test_handles_runtime_path(self):
        from decomp_agent.tools.registry import _normalize_source_file

        assert _normalize_source_file("src/Runtime/global_destructor_chain.c") == "Runtime/global_destructor_chain.c"

    def test_handles_trk_path(self):
        from decomp_agent.tools.registry import _normalize_source_file

        assert _normalize_source_file("src/TRK_MINNOW_DOLPHIN/main.c") == "TRK_MINNOW_DOLPHIN/main.c"

    def test_ignores_unrecognized_prefix(self):
        from decomp_agent.tools.registry import _normalize_source_file

        assert _normalize_source_file("src/unknown/file.c") == "src/unknown/file.c"

    def test_double_src_prefix_left_alone(self):
        from decomp_agent.tools.registry import _normalize_source_file

        # "src/src/melee/..." is not stripped because the lookahead requires
        # melee/|dolphin/|etc. immediately after the optional "src/".
        # This is fine — the model won't hallucinate a double prefix.
        assert _normalize_source_file("src/src/melee/test.c") == "src/src/melee/test.c"
