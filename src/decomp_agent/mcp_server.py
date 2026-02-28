"""MCP server exposing decomp tools for Claude Code headless mode.

Wraps the existing tool handlers from registry.py as MCP tools using
FastMCP. Each tool takes the same parameters, calls the same handler
functions, and returns the result string as MCP text content.

Run standalone:  python -m decomp_agent.mcp_server
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from decomp_agent.config import Config, load_config
from decomp_agent.tools.registry import (
    _check_inline_asm,
    _handle_compile_and_check,
    _handle_get_context,
    _handle_get_diff,
    _handle_get_ghidra_decompilation,
    _handle_get_m2c_decompilation,
    _handle_get_target_assembly,
    _handle_mark_complete,
    _handle_read_source_file,
    _handle_write_function,
    _normalize_source_file,
)
from decomp_agent.tools.schemas import (
    CompileAndCheckParams,
    GetContextParams,
    GetDiffParams,
    GetGhidraDecompilationParams,
    GetM2CDecompilationParams,
    GetTargetAssemblyParams,
    MarkCompleteParams,
    ReadSourceFileParams,
    WriteFunctionParams,
)

mcp = FastMCP("decomp-tools")

_config: Config | None = None


def _get_config() -> Config:
    """Load config once, cached for the process lifetime."""
    global _config
    if _config is None:
        config_path = os.environ.get("DECOMP_CONFIG")
        _config = load_config(Path(config_path) if config_path else None)
    return _config


# ---------------------------------------------------------------------------
# Tool registrations — one per handler, matching the schemas from schemas.py
# ---------------------------------------------------------------------------


@mcp.tool()
def get_target_assembly(function_name: str, source_file: str) -> str:
    """Get the target PowerPC assembly for a function. Returns the disassembled
    instructions from the original game binary that your C code must match."""
    source_file = _normalize_source_file(source_file)
    params = GetTargetAssemblyParams(
        function_name=function_name, source_file=source_file
    )
    return _handle_get_target_assembly(params, _get_config())


@mcp.tool()
def get_ghidra_decompilation(function_name: str) -> str:
    """Get Ghidra's decompiled C code for a function. Provides type-aware
    decompilation with struct access patterns and control flow."""
    config = _get_config()
    if not config.ghidra.enabled:
        return "Error: Ghidra is not enabled in this environment."
    params = GetGhidraDecompilationParams(function_name=function_name)
    return _handle_get_ghidra_decompilation(params, config)


@mcp.tool()
def get_m2c_decompilation(function_name: str, source_file: str) -> str:
    """Run m2c on a function's target assembly to produce an initial C
    decompilation. The output is matching-oriented and makes a good starting
    template, but usually needs manual adjustment."""
    source_file = _normalize_source_file(source_file)
    params = GetM2CDecompilationParams(
        function_name=function_name, source_file=source_file
    )
    return _handle_get_m2c_decompilation(params, _get_config())


@mcp.tool()
def get_context(function_name: str, source_file: str) -> str:
    """Get preprocessed headers, types, and nearby matched functions for
    context. Returns everything the function can reference: struct definitions,
    enums, externs, and examples of already-matched functions in the same file."""
    source_file = _normalize_source_file(source_file)
    params = GetContextParams(
        function_name=function_name, source_file=source_file
    )
    return _handle_get_context(params, _get_config())


@mcp.tool()
def read_source_file(source_file: str) -> str:
    """Read the current contents of a C source file. Use this to see the
    existing code, includes, and function stubs before making changes."""
    source_file = _normalize_source_file(source_file)
    params = ReadSourceFileParams(source_file=source_file)
    return _handle_read_source_file(params, _get_config())


@mcp.tool()
def write_function(source_file: str, function_name: str, code: str) -> str:
    """Replace a function's implementation, compile, and return match results.
    Provide the complete function including signature and body. The old
    implementation is found by name and fully replaced. Automatically compiles
    and returns match percentages. If compilation fails, the code is reverted
    to the previous working version."""
    source_file = _normalize_source_file(source_file)
    params = WriteFunctionParams(
        source_file=source_file, function_name=function_name, code=code
    )
    return _handle_write_function(params, _get_config())


@mcp.tool()
def compile_and_check(source_file: str) -> str:
    """Compile the source file and check match status for all functions in the
    translation unit. Returns per-function fuzzy match percentages showing how
    close each function is to matching the target."""
    source_file = _normalize_source_file(source_file)
    params = CompileAndCheckParams(source_file=source_file)
    return _handle_compile_and_check(params, _get_config())


@mcp.tool()
def get_diff(source_file: str, function_name: str) -> str:
    """Get the assembly diff between compiled and target code for a specific
    function. Shows instruction-level differences so you can see exactly which
    instructions don't match and adjust your C code accordingly."""
    source_file = _normalize_source_file(source_file)
    params = GetDiffParams(
        source_file=source_file, function_name=function_name
    )
    return _handle_get_diff(params, _get_config())


# TODO: re-enable permuter
# The run_permuter tool is excluded from the initial MCP build.
# Permuter requires cloning decomp-permuter, its Python dependencies
# (pycparser, etc.), and a cc preprocessor inside the container.
#
# To add later:
# 1. Add to Dockerfile: RUN git clone ... decomp-permuter && pip install pycparser
# 2. Register run_permuter here (same pattern as other tools)
# 3. In container.toml, permuter paths resolve natively (no Docker indirection)
# 4. Add "run_permuter" back to system prompt's tool list
# 5. Update --disallowedTools to not block the MCP permuter tool


@mcp.tool()
def mark_complete(function_name: str, source_file: str) -> str:
    """Mark a function as successfully matched. Call this after compile_and_check
    confirms 100% match for your target function."""
    source_file = _normalize_source_file(source_file)
    params = MarkCompleteParams(
        function_name=function_name, source_file=source_file
    )
    return _handle_mark_complete(params, _get_config())


if __name__ == "__main__":
    mcp.run()
