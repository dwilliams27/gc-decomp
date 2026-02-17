"""Tool registration and dispatch for the agent loop.

Maps tool names to handler functions, generates the OpenAI tools list
from Pydantic schemas, and dispatches tool calls by validating args,
injecting config, and calling the underlying tool functions.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from openai import pydantic_function_tool

from decomp_agent.config import Config
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

log = logging.getLogger(__name__)


class ToolRegistry:
    """Holds config and maps tool names to handler callables."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._handlers: dict[str, tuple[type, Callable[..., str]]] = {}

    def register(
        self,
        name: str,
        schema: type,
        handler: Callable[..., str],
    ) -> None:
        """Register a tool by name with its Pydantic schema and handler."""
        self._handlers[name] = (schema, handler)

    def get_openai_tools(self) -> list[dict]:
        """Generate the ``tools`` list for the OpenAI chat API."""
        tools = []
        for name, (schema, _) in self._handlers.items():
            tool_def = pydantic_function_tool(schema, name=name)
            tools.append(tool_def)
        return tools

    def dispatch(self, tool_name: str, arguments_json: str) -> str:
        """Validate args, inject config, call handler, return result string.

        Exceptions from handlers are caught and returned as error strings
        so the LLM can see what went wrong and course-correct.
        """
        if tool_name not in self._handlers:
            return f"Error: unknown tool '{tool_name}'"

        schema, handler = self._handlers[tool_name]

        try:
            args = json.loads(arguments_json)
        except json.JSONDecodeError as e:
            return f"Error: invalid JSON arguments: {e}"

        try:
            params = schema(**args)
        except Exception as e:
            return f"Error: invalid arguments for {tool_name}: {e}"

        try:
            return handler(params, self.config)
        except Exception as e:
            log.exception("Tool %s raised an exception", tool_name)
            return f"Error in {tool_name}: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Handler functions — bridge Pydantic schemas to raw tool functions
# ---------------------------------------------------------------------------


def _load_report(config: Config):
    """Load the current report.json if available, or None."""
    from decomp_agent.melee.report import Report, parse_report

    report_path = config.melee.report_path
    if report_path.exists():
        return parse_report(report_path)
    return None


def _handle_get_target_assembly(
    params: GetTargetAssemblyParams, config: Config
) -> str:
    from decomp_agent.tools.m2c_tool import get_target_assembly

    result = get_target_assembly(params.function_name, params.source_file, config)
    if result is None:
        return (
            f"Function '{params.function_name}' not found in assembly for "
            f"{params.source_file}"
        )
    return result


def _handle_get_ghidra_decompilation(
    params: GetGhidraDecompilationParams, config: Config
) -> str:
    from decomp_agent.tools.ghidra import get_ghidra_decompilation

    result = get_ghidra_decompilation(params.function_name, config)
    return result.format_for_llm()


def _handle_get_m2c_decompilation(
    params: GetM2CDecompilationParams, config: Config
) -> str:
    from decomp_agent.tools.m2c_tool import run_m2c

    result = run_m2c(params.function_name, params.source_file, config)
    if result.error:
        return f"m2c error: {result.error}"
    return result.c_code or "m2c produced no output"


def _handle_get_context(params: GetContextParams, config: Config) -> str:
    from decomp_agent.tools.context import get_function_context

    report = _load_report(config)
    ctx = get_function_context(
        params.function_name, params.source_file, config, report=report
    )
    return ctx.format_for_llm()


def _handle_read_source_file(
    params: ReadSourceFileParams, config: Config
) -> str:
    from decomp_agent.tools.source import read_source_file

    src_path = config.melee.repo_path / "src" / params.source_file
    if not src_path.exists():
        return f"Error: source file not found: {src_path}"
    return read_source_file(src_path)


def _handle_write_function(
    params: WriteFunctionParams, config: Config
) -> str:
    from decomp_agent.tools.source import (
        read_source_file,
        replace_function,
        write_source_file,
    )

    src_path = config.melee.repo_path / "src" / params.source_file
    if not src_path.exists():
        return f"Error: source file not found: {src_path}"

    source = read_source_file(src_path)
    updated = replace_function(source, params.function_name, params.code)
    if updated is None:
        return (
            f"Error: function '{params.function_name}' not found in "
            f"{params.source_file}. Use read_source_file to check the file."
        )
    write_source_file(src_path, updated)
    return f"Successfully wrote {params.function_name} to {params.source_file}"


def _handle_compile_and_check(
    params: CompileAndCheckParams, config: Config
) -> str:
    from decomp_agent.tools.build import check_match

    result = check_match(params.source_file, config)
    if not result.success:
        return f"Compilation failed:\n{result.error}"

    lines = [f"Compilation successful. Match results for {params.source_file}:\n"]
    for func in result.functions:
        status = "MATCH" if func.is_matched else f"{func.fuzzy_match_percent:.1f}%"
        lines.append(f"  {func.name}: {status} (size: {func.size})")

    if result.all_matched:
        lines.append("\nAll functions match!")
    else:
        lines.append(f"\nOverall: {result.match_percent:.1f}% average match")

    return "\n".join(lines)


def _handle_get_diff(params: GetDiffParams, config: Config) -> str:
    from decomp_agent.tools.build import get_function_diff

    return get_function_diff(params.source_file, params.function_name, config)


def _handle_run_permuter(params: RunPermuterParams, config: Config) -> str:
    from decomp_agent.tools.permuter import run_permuter

    result = run_permuter(params.function_name, params.source_file, config)
    if result.error:
        return f"Permuter error: {result.error}"
    if result.success:
        return (
            f"Permuter found a perfect match after {result.iterations} iterations!\n"
            f"Best code:\n{result.best_code}"
        )
    if result.improved:
        return (
            f"Permuter improved score to {result.best_score} after "
            f"{result.iterations} iterations.\n"
            f"Best code:\n{result.best_code}"
        )
    return f"Permuter ran {result.iterations} iterations with no improvement."


def _handle_mark_complete(params: MarkCompleteParams, config: Config) -> str:
    # Phase 5 stub — will integrate with orchestrator to record matched status
    return (
        f"Function {params.function_name} in {params.source_file} "
        f"marked as complete."
    )


def build_registry(config: Config) -> ToolRegistry:
    """Create a fully-populated ToolRegistry with all 10 tools."""
    registry = ToolRegistry(config)

    registry.register(
        "get_target_assembly", GetTargetAssemblyParams, _handle_get_target_assembly
    )
    registry.register(
        "get_ghidra_decompilation",
        GetGhidraDecompilationParams,
        _handle_get_ghidra_decompilation,
    )
    registry.register(
        "get_m2c_decompilation",
        GetM2CDecompilationParams,
        _handle_get_m2c_decompilation,
    )
    registry.register("get_context", GetContextParams, _handle_get_context)
    registry.register(
        "read_source_file", ReadSourceFileParams, _handle_read_source_file
    )
    registry.register(
        "write_function", WriteFunctionParams, _handle_write_function
    )
    registry.register(
        "compile_and_check", CompileAndCheckParams, _handle_compile_and_check
    )
    registry.register("get_diff", GetDiffParams, _handle_get_diff)
    registry.register(
        "run_permuter", RunPermuterParams, _handle_run_permuter
    )
    registry.register(
        "mark_complete", MarkCompleteParams, _handle_mark_complete
    )

    return registry
