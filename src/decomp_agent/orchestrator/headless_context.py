"""Shared prompt/context builders for headless coding agents."""

from __future__ import annotations

from pathlib import Path

from decomp_agent.agent.m2c_seed import build_prefetched_m2c_block
from decomp_agent.config import Config

SYSTEM_PROMPT_PATH = Path(__file__).parents[3] / "docker" / "system-prompt.md"

RELENTLESSNESS_BLOCK = (
    "This may be a long, difficult decompilation task that takes many turns, "
    "many tool calls, and multiple fundamentally different approaches. "
    "Be relentless. Do not stop after a first draft or first non-matching "
    "attempt. Keep iterating with write_function, get_diff, get_context, "
    "header fixes, and type fixes until you either reach 100%, hit a concrete "
    "external blocker, or have exhausted every realistic avenue you can find."
)


def load_headless_system_prompt() -> str:
    """Load the shared decomp system prompt used by headless agents."""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def build_file_status(source_file: str, config: Config) -> str:
    """Build a status summary of all functions in a source file."""
    try:
        from decomp_agent.tools.build import check_match

        result = check_match(source_file, config)
        if not result.success:
            return f"(could not compile {source_file} to check status)"

        lines = []
        matched = []
        unmatched = []
        for f in result.functions:
            if f.is_matched:
                matched.append(f.name)
                lines.append(f"  {f.name}: MATCH ({f.size} bytes)")
            elif f.fuzzy_match_percent > 0:
                unmatched.append(f.name)
                lines.append(
                    f"  {f.name}: {f.fuzzy_match_percent:.1f}% ({f.size} bytes)"
                )
            else:
                unmatched.append(f.name)
                lines.append(f"  {f.name}: stub ({f.size} bytes)")

        header = (
            f"{len(matched)}/{len(result.functions)} matched, "
            f"{len(unmatched)} remaining"
        )
        return header + "\n" + "\n".join(lines)
    except Exception as e:
        return f"(error getting file status: {e})"


def build_headless_task_prompt(
    function_name: str | None,
    source_file: str,
    config: Config,
    *,
    prior_best_code: str | None = None,
    prior_match_pct: float = 0,
) -> str:
    """Build the shared task prompt for headless Claude/Codex runs."""
    file_mode = function_name is None

    if file_mode:
        file_status = build_file_status(source_file, config)
        return (
            f"Match all unmatched functions in {source_file}.\n\n"
            f"Current status:\n{file_status}\n\n"
            f"{RELENTLESSNESS_BLOCK}\n\n"
            f"Work through the unmatched functions, starting with the smallest "
            f"or easiest ones. For each function:\n"
            f"- Use get_target_assembly and get_context to understand it\n"
            f"- Use write_function to write and test your code\n"
            f"- Use get_diff to analyze mismatches and iterate\n"
            f"- Call mark_complete when a function hits 100%%\n\n"
            f"You have full access to the codebase. Edit headers, add "
            f"#includes, fix UNK_RET/UNK_PARAMS signatures, add extern "
            f"declarations — whatever helps. Each improvement you make to "
            f"headers and types helps all subsequent functions.\n\n"
            f"If you get stuck on one function, move on to another and come "
            f"back later with fresh context."
        )

    if prior_best_code is not None:
        diff_block = ""
        try:
            from decomp_agent.tools.disasm import get_function_diff

            diff_text = get_function_diff(function_name, source_file, config)
            diff_block = (
                f"\n\nCurrent diff (target vs compiled):\n```\n{diff_text}\n```"
            )
        except Exception:
            pass

        if prior_match_pct >= 80.0:
            strategy = (
                "You are VERY close. Make only tiny, targeted changes: "
                "reorder variable declarations, split or merge expressions, "
                "adjust casts, or change variable types. "
                "DO NOT rewrite the function or change its structure."
            )
        elif prior_match_pct >= 50.0:
            strategy = (
                "The structure is mostly right. Focus on register allocation: "
                "reorder variable declarations, change how temporaries are used, "
                "split compound expressions, or merge separate statements. "
                "Preserve the overall logic — do NOT rewrite from scratch."
            )
        else:
            strategy = (
                "Start by writing this code with write_function, then analyze "
                "the diff to find remaining mismatches. Focus on improving from "
                "this baseline rather than starting from scratch."
            )

        m2c_seed = build_prefetched_m2c_block(
            function_name, source_file, config, max_chars=6000
        )
        return (
            f"Match function {function_name} in {source_file}.\n\n"
            f"{RELENTLESSNESS_BLOCK}\n\n"
            f"A previous attempt reached {prior_match_pct:.1f}% match with this code:\n\n"
            f"```c\n{prior_best_code}\n```\n\n"
            f"{strategy}"
            f"{diff_block}"
            f"{m2c_seed}"
        )

    m2c_seed = build_prefetched_m2c_block(
        function_name, source_file, config, max_chars=6000
    )
    return (
        f"Match function {function_name} in {source_file}.\n\n"
        f"{RELENTLESSNESS_BLOCK}\n\n"
        f"You have tools to read assembly, get context/headers, "
        f"write code, check diffs, and iterate. The m2c output below "
        f"is a starting scaffold. Use get_context and get_target_assembly "
        f"to understand what the function does, then iterate with "
        f"write_function and get_diff until you reach 100% match.\n\n"
        f"If you hit compile errors from undeclared symbols, check the "
        f"extern references section below — it tells you which headers "
        f"to include or what extern declarations to add."
        f"{m2c_seed}"
    )
