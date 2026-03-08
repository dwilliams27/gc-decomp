"""Tool registration and dispatch for the agent loop.

Maps tool names to handler functions, generates the OpenAI tools list
from Pydantic schemas, and dispatches tool calls by validating args,
injecting config, and calling the underlying tool functions.
"""

from __future__ import annotations

import json
import re
import time
from typing import Callable

import structlog
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

log = structlog.get_logger()

# Regex to strip common hallucinated prefixes from source_file paths.
# The canonical form is "melee/lb/lbcommand.c" (no "src/" prefix).
_PATH_PREFIX_RE = re.compile(r"^(?:src/)?(?=melee/|dolphin/|Runtime/|TRK_MINNOW_DOLPHIN/)")


def _normalize_source_file(path: str) -> str:
    """Normalize a source_file path to the canonical object-name format.

    The model sometimes hallucinates prefixes like "src/melee/..." or drops
    the "melee/" prefix entirely. This strips known bad prefixes.
    """
    return _PATH_PREFIX_RE.sub("", path)


class ToolRegistry:
    """Holds config and maps tool names to handler callables."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._handlers: dict[str, tuple[type, Callable[..., str]]] = {}
        self.log_prefix: str = ""

    def register(
        self,
        name: str,
        schema: type,
        handler: Callable[..., str],
    ) -> None:
        """Register a tool by name with its Pydantic schema and handler."""
        self._handlers[name] = (schema, handler)

    def get_openai_tools(self) -> list[dict]:
        """Generate the ``tools`` list for the OpenAI chat completions API."""
        tools = []
        for name, (schema, _) in self._handlers.items():
            tool_def = pydantic_function_tool(schema, name=name)
            tools.append(tool_def)
        return tools

    def get_responses_api_tools(self) -> list[dict]:
        """Generate the ``tools`` list for the OpenAI Responses API.

        Responses API tools use a flat format::

            {"type": "function", "name": "...", "description": "...",
             "parameters": {...}, "strict": True}

        Unlike chat completions which nests under a ``function`` key.
        """
        tools = []
        for name, (schema, _) in self._handlers.items():
            chat_tool = pydantic_function_tool(schema, name=name)
            fn = chat_tool["function"]
            tools.append({
                "type": "function",
                "name": fn["name"],
                "description": fn.get("description", ""),
                "parameters": fn["parameters"],
                "strict": fn.get("strict", True),
            })
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

        # Normalize source_file paths to fix model hallucinations
        if hasattr(params, "source_file"):
            original = params.source_file
            params.source_file = _normalize_source_file(params.source_file)
            if params.source_file != original:
                log.info(
                    "path_normalized",
                    tool=tool_name,
                    original=original,
                    normalized=params.source_file,
                )

        try:
            t0 = time.monotonic()
            result = handler(params, self.config)
            elapsed_ms = (time.monotonic() - t0) * 1000
            log.info(
                f"{self.log_prefix}tool_dispatch",
                tool=tool_name,
                elapsed_ms=round(elapsed_ms, 1),
                result_length=len(result),
            )
            return result
        except Exception as e:
            log.exception(f"{self.log_prefix}tool_error", tool=tool_name, error=str(e))
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

    try:
        result = run_m2c(
            params.function_name,
            params.source_file,
            config,
            flags=params.flags,
            union_fields=params.union_fields,
        )
    except ValueError as e:
        return f"m2c error: {e}"
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

    src_path = config.melee.resolve_source_path(params.source_file)
    if not src_path.exists():
        return f"Error: source file not found: {src_path}"
    return read_source_file(src_path)


def _format_match_result(result, source_file: str) -> str:
    """Format a check_match result into a human-readable string."""
    if not result.success:
        return f"Compilation failed:\n{result.error}"

    lines = [f"Compilation successful. Match results for {source_file}:\n"]
    for func in result.functions:
        if func.is_matched:
            status = "MATCH"
        elif func.fuzzy_match_percent >= 99.95:
            status = f"{min(func.fuzzy_match_percent, 99.99):.2f}%"
        else:
            status = f"{func.fuzzy_match_percent:.1f}%"

        # Annotate with structural match info for non-matched functions
        annotation = ""
        if not func.is_matched and func.mismatch_type:
            if func.mismatch_type == "register_only":
                annotation = (
                    f" ({func.structural_match_percent:.0f}% structural"
                    f" — register allocation only)"
                )
            elif func.mismatch_type == "opcode":
                annotation = (
                    f" ({func.structural_match_percent:.0f}% structural"
                    f" — wrong instructions)"
                )
            elif func.mismatch_type == "structural":
                annotation = (
                    f" ({func.structural_match_percent:.0f}% structural"
                    f" — structural difference)"
                )
            elif func.mismatch_type == "mixed":
                annotation = (
                    f" ({func.structural_match_percent:.0f}% structural"
                    f" — mixed issues)"
                )

        lines.append(f"  {func.name}: {status}{annotation} (size: {func.size})")

    if result.all_matched:
        lines.append("\nAll functions match!")
    else:
        lines.append(f"\nOverall: {result.match_percent:.1f}% average match")

    return "\n".join(lines)


_ASM_BLOCK_RE = re.compile(r"\basm\s*\{([^}]*)\}", re.DOTALL)
_NOT_IMPLEMENTED_RE = re.compile(r"\bNOT_IMPLEMENTED\b")
_RAW_OFFSET_ACCESS_RE = re.compile(
    r"\(\s*(?:u8|s8|char|unsigned\s+char)\s*\*\s*\)\s*"
    r"[A-Za-z_]\w*\s*\+\s*0x[0-9A-Fa-f]+"
)
_FOR_DECLARATION_RE = re.compile(
    r"\bfor\s*\(\s*"
    r"(?:const\s+|volatile\s+|unsigned\s+|signed\s+|long\s+|short\s+|"
    r"struct\s+\w+\s+|enum\s+\w+\s+|union\s+\w+\s+)*"
    r"[A-Za-z_]\w*(?:\s*\*+\s*|\s+)\w+\s*="
)

# Detect m2c-generated variable names like "var_r31", "var_r3_2", "var1".
# Pattern: var_rNN, var_rNN_N (register-based), or varN (numbered).
# Does NOT match legitimate names like "var_count" or "variable".
_M2C_VAR_DECL_RE = re.compile(
    r"(?:^|[;{}\s])\s*"                       # statement boundary
    r"(?:const\s+|volatile\s+|unsigned\s+|signed\s+|long\s+|short\s+|"
    r"struct\s+\w+\s+|enum\s+\w+\s+|union\s+\w+\s+)*"
    r"[A-Za-z_]\w*(?:\s*\*+\s*|\s+)"          # type
    r"(var_r\d+(?:_\d+)?|var\d+)"             # m2c variable name
    r"\s*[;=,\)]",                             # followed by assignment, semicolon, etc.
    re.MULTILINE,
)

# Match percentage comments like "// 95% match", "/* 42.3% */", "// 100%"
_MATCH_PCT_COMMENT_RE = re.compile(
    r"(?://|/\*)\s*\d+(?:\.\d+)?%\s*(?:match|matched|matching)?\s*(?:\*/)?",
    re.IGNORECASE,
)

# Max number of assembly instructions allowed in a single asm {} block.
# Single-instruction intrinsics (mfspr, psq_st, nop) are legitimate;
# multi-instruction blocks that replace C logic are not decompilation.
_MAX_ASM_INSTRUCTIONS = 1


def _check_inline_asm(code: str) -> str | None:
    """Return an error message if code contains banned multi-instruction asm blocks."""
    for m in _ASM_BLOCK_RE.finditer(code):
        body = m.group(1).strip()
        # Count non-empty lines as instructions
        instructions = [line.strip() for line in body.splitlines() if line.strip()]
        if len(instructions) > _MAX_ASM_INSTRUCTIONS:
            return (
                f"Error: code contains a multi-instruction asm block "
                f"({len(instructions)} instructions). Inline assembly blocks "
                f"that replace C logic are banned — the goal is to produce C "
                f"code that compiles to matching assembly. Single-instruction "
                f"asm for hardware intrinsics (mfspr, psq_st, etc.) is OK. "
                f"Rewrite this as C code."
            )
    return None


def _check_placeholder_stubs(code: str) -> str | None:
    """Return an error if code includes placeholder implementation macros."""
    if _NOT_IMPLEMENTED_RE.search(code):
        return (
            "Error: code contains NOT_IMPLEMENTED placeholder logic. "
            "Do not replace stubs with placeholders; either provide a real "
            "decompilation attempt or keep the original /// #function marker."
        )
    return None


def _check_field_access_style(code: str) -> str | None:
    """Reject raw offset pointer arithmetic style access.

    Maintainer preference is named fields where possible, or M2C_FIELD as a
    temporary bridge when offsets are still unknown.
    """
    if "M2C_FIELD(" in code:
        return None
    if _RAW_OFFSET_ACCESS_RE.search(code):
        return (
            "Error: code uses raw offset pointer arithmetic "
            "((u8*)ptr + 0xNN style). This is considered slop. "
            "Use named struct fields when available, or M2C_FIELD(...) "
            "instead of manual byte-offset casts."
        )
    return None


def _check_c89_declarations(code: str) -> str | None:
    """Reject common C99 declaration patterns not allowed by the project."""
    if _FOR_DECLARATION_RE.search(code):
        return (
            "Error: code uses a declaration inside a for-loop initializer "
            "(C99 style). This project expects C89/C90 declaration style; "
            "declare variables at the top of the scope."
        )
    return None


def _check_var_names(code: str) -> str | None:
    """Reject m2c-generated variable names that should be renamed."""
    match = _M2C_VAR_DECL_RE.search(code)
    if match:
        var_name = match.group(1)
        return (
            f"Error: code contains m2c-generated variable name '{var_name}'. "
            f"Rename all var_* / var# names to meaningful identifiers: "
            f"use 'i'/'j' for loop indices, 'jobj'/'gobj' for objects, "
            f"'pos'/'vel' for vectors, etc. m2c artifacts are not acceptable "
            f"in final code."
        )
    return None


def _check_match_comments(code: str) -> str | None:
    """Reject match percentage comments — they're noise."""
    if _MATCH_PCT_COMMENT_RE.search(code):
        return (
            "Error: code contains match percentage comments (e.g. '// 95% match'). "
            "Remove these — objdiff shows match status. Match % comments are "
            "noise that reviewers will flag."
        )
    return None


def _handle_write_function(
    params: WriteFunctionParams, config: Config
) -> str:
    from decomp_agent.tools.build import check_match
    from decomp_agent.tools.source import (
        read_source_file,
        replace_function,
        write_source_file,
    )

    # Reject multi-instruction inline asm blocks
    asm_error = _check_inline_asm(params.code)
    if asm_error is not None:
        return asm_error
    stub_error = _check_placeholder_stubs(params.code)
    if stub_error is not None:
        return stub_error
    field_access_error = _check_field_access_style(params.code)
    if field_access_error is not None:
        return field_access_error
    c89_error = _check_c89_declarations(params.code)
    if c89_error is not None:
        return c89_error
    var_error = _check_var_names(params.code)
    if var_error is not None:
        return var_error
    match_comment_error = _check_match_comments(params.code)
    if match_comment_error is not None:
        return match_comment_error

    src_path = config.melee.resolve_source_path(params.source_file)
    if not src_path.exists():
        return f"Error: source file not found: {src_path}"

    source = read_source_file(src_path)

    # Capture prior match % for this function before overwriting
    prior_match_pct = 0.0
    try:
        prior_result = check_match(params.source_file, config)
        if prior_result.success:
            prior_func = prior_result.get_function(params.function_name)
            if prior_func is not None:
                prior_match_pct = prior_func.fuzzy_match_percent
    except Exception:
        pass

    updated = replace_function(source, params.function_name, params.code)
    if updated is None:
        return (
            f"Error: function '{params.function_name}' not found in "
            f"{params.source_file}. Use read_source_file to check the file."
        )
    write_source_file(src_path, updated)

    # Auto-compile and return match results inline.
    # If compilation fails, restore the previous source so the agent
    # isn't building on broken state in subsequent iterations.
    result = check_match(params.source_file, config)
    if not result.success:
        write_source_file(src_path, source)
        return (
            f"Wrote {params.function_name}, but compilation failed "
            f"(reverted to previous code):\n{result.error}"
        )

    # Check for match regression — reject writes that make things worse.
    # Only applies when there was a prior non-zero match (not stubs).
    new_func = result.get_function(params.function_name)
    new_match_pct = new_func.fuzzy_match_percent if new_func else 0.0
    if prior_match_pct > 5.0 and new_match_pct < prior_match_pct - 1.0:
        write_source_file(src_path, source)
        return (
            f"Wrote {params.function_name}, but match REGRESSED from "
            f"{prior_match_pct:.1f}% to {new_match_pct:.1f}% "
            f"(reverted to previous code). Make smaller, targeted changes "
            f"instead of rewriting. Use get_diff to see what's wrong and "
            f"fix specific mismatches."
        )

    return (
        f"Wrote {params.function_name} to {params.source_file}.\n\n"
        + _format_match_result(result, params.source_file)
    )


def _handle_compile_and_check(
    params: CompileAndCheckParams, config: Config
) -> str:
    from decomp_agent.tools.build import check_match

    result = check_match(params.source_file, config)
    return _format_match_result(result, params.source_file)


def _handle_get_diff(params: GetDiffParams, config: Config) -> str:
    from decomp_agent.tools.disasm import get_function_diff

    return get_function_diff(params.function_name, params.source_file, config)


def _handle_run_permuter(params: RunPermuterParams, config: Config) -> str:
    from decomp_agent.tools.permuter import run_permuter

    result = run_permuter(
        params.function_name, params.source_file, config,
        timeout=params.timeout, workers=params.workers,
    )
    if result.error:
        return f"Permuter error: {result.error}"
    if result.success:
        msg = f"Permuter found a perfect match after {result.iterations} iterations!"
        if result.best_code:
            msg += f"\nBest code:\n{result.best_code}"
        return msg
    if result.improved:
        return (
            f"Permuter improved score to {result.best_score} after "
            f"{result.iterations} iterations.\n"
            f"Best code:\n{result.best_code}"
        )
    return f"Permuter ran {result.iterations} iterations with no improvement."


def _handle_mark_complete(params: MarkCompleteParams, config: Config) -> str:
    from decomp_agent.tools.build import check_match

    result = check_match(params.source_file, config)
    if not result.success:
        return f"Error: compilation failed, cannot verify match: {result.error}"

    func = result.get_function(params.function_name)
    if func is None:
        return (
            f"Error: function {params.function_name} not found in "
            f"compile output for {params.source_file}"
        )

    if not func.is_matched:
        return (
            f"Error: {params.function_name} is NOT matched "
            f"(fuzzy_match_percent={func.fuzzy_match_percent:.4f}%). "
            f"Keep iterating."
        )

    return (
        f"Verified: {params.function_name} in {params.source_file} "
        f"is a confirmed MATCH."
    )


def build_registry(config: Config) -> ToolRegistry:
    """Create a fully-populated ToolRegistry with all available tools."""
    registry = ToolRegistry(config)

    registry.register(
        "get_target_assembly", GetTargetAssemblyParams, _handle_get_target_assembly
    )
    if config.ghidra.enabled:
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
