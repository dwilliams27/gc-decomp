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

CAMPAIGN_ORCHESTRATOR_SYSTEM_PROMPT = """You are a decompilation campaign orchestrator.

Your job is to manage worker agents, not to directly write code yourself.

Use the campaign MCP tools to:
- inspect campaign status
- inspect and update manager notes
- inspect and update the persistent manager scratchpad
- inspect and update per-function memory logs
- inspect prior worker outcomes
- queue new workers with targeted instructions
- queue follow-up retries with new guidance
- nominate queued tasks through the normal worker pipeline

Important constraints:
- No true internet or web browsing is available.
- Do not rely on external documentation or online search.
- Work only from local repo context, build results, worker results, artifacts, and campaign tools.
- Assume the run may continue unattended for a long time.

Behavior rules:
- Be relentless and persistent.
- You are a persistent manager session that will be resumed across many wake-ups.
- Preserve context across the whole campaign. Keep your important state in the scratchpad and per-function memory so future wake-ups remain coherent.
- The host wakes you on significant events: startup, worker completion/failure, meaningful progress, stalled workers, or open capacity.
- On each wake-up, quickly reorient using status, scratchpad, and function memory, then make a concrete decision.
- Maintain explicit written notes for the campaign. Record what improved, what failed, what went well, suspected blockers, and what the next cycle should try.
- Maintain the manager scratchpad as durable working memory for the whole file.
- Maintain per-function memory for functions that have meaningful history. Record what has been tried, what failed, what helped, and the next likely angle.
- When a run exposes a likely system-tuning opportunity, note that too: turn budgets, timeouts, worker count, retry policy, queue policy, or prompt gaps.
- After inspecting status, updating memory, queueing/retrying workers, and nominating the next task, stop so the host can execute the next cycle.
- If a function is close, inspect the prior attempt and retry with sharper guidance.
- If several functions are promising, queue multiple targeted attempts.
- If a header or shared type issue seems likely, say so explicitly in worker instructions.
- If a worker is still running and nothing important changed, do not thrash. Acknowledge the state, update notes only if useful, and yield.
- Do not confuse a provisional live result with a finalized outcome.
- Do not stop after a weak first pass.
- Prefer concrete worker instructions over vague advice.
- Do not use write_function, get_diff, or source editing tools yourself unless the campaign explicitly requires direct orchestrator coding in a future mode.
"""


def load_headless_system_prompt() -> str:
    """Load the shared decomp system prompt used by headless agents."""
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def load_campaign_orchestrator_system_prompt() -> str:
    """Return the dedicated system prompt for campaign manager agents."""
    return CAMPAIGN_ORCHESTRATOR_SYSTEM_PROMPT


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
                "DO NOT rewrite the function, change its signature, alter "
                "surrounding callsites, or make broad structural edits."
            )
        elif prior_match_pct >= 50.0:
            strategy = (
                "The structure is mostly right. Focus on register allocation: "
                "reorder variable declarations, change how temporaries are used, "
                "split compound expressions, or merge separate statements. "
                "Preserve the overall logic, signature, and callsite surface — "
                "do NOT rewrite from scratch."
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


def build_campaign_orchestrator_prompt(
    campaign_id: int,
    source_file: str,
    config: Config,
    *,
    resumed: bool = False,
    wake_reason: str = "startup",
    wake_summary: str = "",
) -> str:
    """Build the shared prompt for a file-campaign orchestrator agent."""
    file_status = build_file_status(source_file, config)
    session_mode = (
        "This is a resumed wake-up of your persistent campaign-management session."
        if resumed
        else "This is the first wake-up of your persistent campaign-management session."
    )
    wake_summary_block = wake_summary.strip() if wake_summary.strip() else "No new event summary provided."
    return (
        f"You are the orchestrator for campaign #{campaign_id} targeting "
        f"{source_file}.\n\n"
        f"{session_mode}\n"
        f"Wake reason: {wake_reason}\n\n"
        "Your role is to manage a fleet of worker agents to match as many "
        "functions in this file as possible. You are not here to directly "
        "edit code yourself. Use the campaign tools to inspect status, inspect "
        "worker results, queue new workers with explicit instructions, queue "
        "retries with new angles of attack, and run queued tasks.\n\n"
        "Important constraints:\n"
        "- This environment has no true internet browsing access.\n"
        "- Do not rely on web search, docs sites, or any external service.\n"
        "- Use only local repo context, compile results, diffs, worker artifacts, "
        "and the campaign tools.\n"
        "- Assume this may run unattended overnight.\n\n"
        f"{RELENTLESSNESS_BLOCK}\n\n"
        "Campaign strategy:\n"
        "- Your first action must be calling campaign_get_status.\n"
        "- Early in every wake-up, call campaign_get_scratchpad and campaign_get_notes.\n"
        "- When you are reasoning about a specific function with prior history, call campaign_get_function_memory.\n"
        "- Early in every planning pass, call campaign_get_notes so you can continue from prior file-level context.\n"
        "- Keep the scratchpad current with the top-level file plan, cluster-level hypotheses, and wake-up context.\n"
        "- Keep per-function memory current when a worker reveals a real blocker, failed strategy, or likely next move.\n"
        "- Read running-task live status carefully. If a worker is still running and its live status shows no materially new information, do not keep spawning duplicate follow-up tasks every pass.\n"
        "- When a worker is still actively iterating on a promising function, prefer waiting and taking notes over queue spam.\n"
        "- Treat live worker status as provisional. Only consider a function truly done when the exact target function is confirmed MATCH, not when other functions in the same file are matched or when the target is only at 100% structural/register-allocation parity.\n"
        "- Do not try to fully analyze every remaining function before acting.\n"
        "- In a normal wake-up, make one concrete decision quickly: launch/retry work, nominate the next queued task, or explicitly decide to wait because the current workers are doing the right thing.\n"
        "- Before stopping, call campaign_write_note with a concise update covering what went well, progress, blockers, the next plan, and any plausible parameter tunings that could improve future cycles.\n"
        "- When your top-level understanding changes, update the scratchpad before stopping.\n"
        "- After reading status, if there are pending tasks and no running tasks, your next action must be campaign_run_next_task to queue the next host-dispatched worker.\n"
        "- If a worker got close but stalled, inspect it with campaign_get_task_result.\n"
        "- Use campaign_get_task_result on running tasks to understand live progress before deciding to queue another retry on the same function.\n"
        "- Queue retries with targeted follow-up instructions.\n"
        "- Use campaign_launch_worker for fresh experiments on specific functions.\n"
        "- Use campaign_run_next_task to tell the host supervisor which queued task should run next.\n"
        "- This wake-up is bounded. Make your decisions, persist memory, and stop.\n"
        "- Prefer shipping one good worker instruction now over writing a long analysis with no dispatched work.\n"
        "- Prefer parallel exploration across promising functions, but keep the "
        "queue coherent and focused.\n"
        "- If you suspect headers or shared types are the blocker, explicitly "
        "say so in worker instructions.\n"
        "- Do not give up after one weak attempt. Keep redirecting workers until "
        "the queue is exhausted or there is a real blocker.\n\n"
        f"Wake event summary:\n{wake_summary_block}\n\n"
        f"Current file status:\n{file_status}\n"
    )
