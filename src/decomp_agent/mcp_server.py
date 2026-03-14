"""MCP server exposing decomp tools for Claude Code headless mode.

Wraps the existing tool handlers from registry.py as MCP tools using
FastMCP. Each tool takes the same parameters, calls the same handler
functions, and returns the result string as MCP text content.

Run standalone:  python -m decomp_agent.mcp_server
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from decomp_agent.config import Config, load_config
from decomp_agent.models.db import get_engine
from decomp_agent.orchestrator.campaign import (
    append_campaign_note,
    create_campaign_worker_task,
    format_campaign_status,
    format_campaign_task_result,
    get_campaign_notes,
    run_campaign_next_task_summary,
    retry_campaign_task,
)
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
    CampaignGetStatusParams,
    CampaignGetTaskResultParams,
    CampaignGetNotesParams,
    CampaignLaunchWorkerParams,
    CampaignRunNextTaskParams,
    CampaignRetryTaskParams,
    CampaignWriteNoteParams,
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
_engine = None
_campaign_log_path = Path("/tmp/decomp-campaign-mcp.log")


def _get_config() -> Config:
    """Load config once, cached for the process lifetime."""
    global _config
    if _config is None:
        config_path = os.environ.get("DECOMP_CONFIG")
        if config_path:
            p = Path(config_path)
            if not p.exists() or not Path(load_config(p).melee.repo_path).exists():
                # Try worker-specific config paths
                import glob
                worker_configs = glob.glob("/tmp/decomp-claude-workers/*/config/container.toml")
                for wc in worker_configs:
                    try:
                        candidate = load_config(Path(wc))
                        if Path(candidate.melee.repo_path).exists():
                            config_path = wc
                            break
                    except Exception:
                        continue
        _config = load_config(Path(config_path) if config_path else None)
    return _config


def _get_engine():
    global _engine
    if _engine is None:
        _engine = get_engine(_get_config().orchestration.db_path)
    return _engine


def _log_campaign_tool(tool_name: str, payload: dict[str, object]) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "payload": payload,
    }
    try:
        with _campaign_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except Exception:
        pass


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


@mcp.tool()
def campaign_get_status(campaign_id: int) -> str:
    """Get the current status of a file campaign, including queued, running,
    completed, and failed worker tasks."""
    params = CampaignGetStatusParams(campaign_id=campaign_id)
    _log_campaign_tool("campaign_get_status", {"campaign_id": params.campaign_id})
    return format_campaign_status(_get_engine(), params.campaign_id)


@mcp.tool()
def campaign_get_task_result(campaign_id: int, task_id: int) -> str:
    """Get the detailed result of one campaign task, including match percent,
    artifacts, and any recorded error."""
    params = CampaignGetTaskResultParams(campaign_id=campaign_id, task_id=task_id)
    _log_campaign_tool(
        "campaign_get_task_result",
        {"campaign_id": params.campaign_id, "task_id": params.task_id},
    )
    return format_campaign_task_result(_get_engine(), params.campaign_id, params.task_id)


@mcp.tool()
def campaign_launch_worker(
    campaign_id: int,
    function_name: str,
    provider: str | None = None,
    instructions: str | None = None,
    priority: int | None = None,
    scope: str | None = None,
) -> str:
    """Queue a new worker task for a function within a campaign. Use this to
    dispatch a fresh attempt with optional provider and guidance."""
    params = CampaignLaunchWorkerParams(
        campaign_id=campaign_id,
        function_name=function_name,
        provider=provider,
        instructions=instructions,
        priority=priority,
        scope=scope,
    )
    _log_campaign_tool(
        "campaign_launch_worker",
        {
            "campaign_id": params.campaign_id,
            "function_name": params.function_name,
            "provider": params.provider or "",
            "priority": params.priority,
            "scope": params.scope or "function",
            "instructions": params.instructions or "",
        },
    )
    task = create_campaign_worker_task(
        _get_engine(),
        campaign_id=params.campaign_id,
        function_name=params.function_name,
        provider=params.provider or "",
        instructions=params.instructions or "",
        priority=params.priority,
        scope=params.scope or "function",
    )
    return (
        f"Queued campaign task #{task.id} for {task.function_name} "
        f"(provider={task.provider or 'default'}, priority={task.priority}, scope={task.scope})"
    )


@mcp.tool()
def campaign_retry_task(
    campaign_id: int,
    task_id: int,
    provider: str | None = None,
    instructions: str | None = None,
    priority: int | None = None,
) -> str:
    """Queue a follow-up attempt for a previous campaign task, preserving the
    target function and optionally adding explicit new guidance."""
    params = CampaignRetryTaskParams(
        campaign_id=campaign_id,
        task_id=task_id,
        provider=provider,
        instructions=instructions,
        priority=priority,
    )
    _log_campaign_tool(
        "campaign_retry_task",
        {
            "campaign_id": params.campaign_id,
            "task_id": params.task_id,
            "provider": params.provider or "",
            "priority": params.priority,
            "instructions": params.instructions or "",
        },
    )
    task = retry_campaign_task(
        _get_engine(),
        campaign_id=params.campaign_id,
        task_id=params.task_id,
        provider=params.provider or "",
        instructions=params.instructions or "",
        priority=params.priority,
    )
    return (
        f"Queued retry task #{task.id} for {task.function_name or task.scope} "
        f"(provider={task.provider or 'default'}, priority={task.priority})"
    )


@mcp.tool()
def campaign_run_next_task(campaign_id: int) -> str:
    """Run the highest-priority pending campaign task through the normal worker
    pipeline and return the result summary."""
    params = CampaignRunNextTaskParams(campaign_id=campaign_id)
    _log_campaign_tool("campaign_run_next_task", {"campaign_id": params.campaign_id})
    return run_campaign_next_task_summary(
        _get_engine(),
        _get_config(),
        campaign_id=params.campaign_id,
    )


@mcp.tool()
def campaign_write_note(campaign_id: int, note: str) -> str:
    """Append a manager note to the campaign notes log."""
    params = CampaignWriteNoteParams(campaign_id=campaign_id, note=note)
    _log_campaign_tool(
        "campaign_write_note",
        {"campaign_id": params.campaign_id, "note_preview": params.note[:160]},
    )
    path = append_campaign_note(_get_engine(), params.campaign_id, params.note)
    return f"Wrote manager note for campaign #{params.campaign_id} to {path}"


@mcp.tool()
def campaign_get_notes(campaign_id: int) -> str:
    """Read the manager notes log for a campaign."""
    params = CampaignGetNotesParams(campaign_id=campaign_id)
    _log_campaign_tool("campaign_get_notes", {"campaign_id": params.campaign_id})
    return get_campaign_notes(_get_engine(), params.campaign_id)


if __name__ == "__main__":
    mcp.run()
