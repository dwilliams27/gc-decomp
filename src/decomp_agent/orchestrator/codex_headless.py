"""Headless Codex CLI runner for decomp agent."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time

import structlog

from decomp_agent.agent.m2c_seed import build_prefetched_m2c_block
from decomp_agent.agent.loop import AgentResult
from decomp_agent.config import Config

log = structlog.get_logger()

_RATE_LIMIT_PATTERNS = (
    "rate limit",
    "rate_limit",
    "429",
    "overloaded",
    "too many requests",
)


def _parse_jsonl_events(output: str) -> list[dict]:
    """Extract JSONL events from mixed stdout/stderr output."""
    events: list[dict] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _parse_codex_result(
    stdout: str,
    stderr: str,
    result: AgentResult,
) -> tuple[str, str]:
    """Populate result fields from Codex JSONL output.

    Returns:
        (termination_reason, error_detail)
    """
    events = _parse_jsonl_events(stdout)
    result.iterations = sum(1 for e in events if e.get("type") == "turn.started")

    for event in events:
        if event.get("type") == "thread.started":
            result.session_id = event.get("thread_id", "")

    messages: list[str] = []
    for event in events:
        message = event.get("message")
        if isinstance(message, str):
            messages.append(message)
        item = event.get("item")
        if isinstance(item, dict):
            item_message = item.get("message")
            if isinstance(item_message, str):
                messages.append(item_message)

    lower_messages = "\n".join(messages).lower()
    lower_stderr = stderr.lower()

    if any(pat in lower_messages or pat in lower_stderr for pat in _RATE_LIMIT_PATTERNS):
        return "rate_limited", messages[-1] if messages else stderr.strip()

    for event in reversed(events):
        if event.get("type") == "turn.failed":
            error = event.get("error")
            if isinstance(error, dict):
                detail = error.get("message", "")
            else:
                detail = ""
            return "api_error", detail or stderr.strip() or "(no output)"

    return "model_stopped", messages[-1] if messages else ""


def run_codex_headless(
    function_name: str | None,
    source_file: str,
    config: Config,
    *,
    worker_label: str = "",
    prior_best_code: str | None = None,
    prior_match_pct: float = 0,
) -> AgentResult:
    """Run Codex CLI headless inside the configured worker container."""
    del prior_match_pct  # Warm-start prompt carries the key signal for now.

    file_mode = function_name is None
    start_time = time.monotonic()
    bound_log = log.bind(
        function=function_name or "(file mode)",
        source_file=source_file,
        worker=worker_label,
        file_mode=file_mode,
    )

    if file_mode:
        prompt = (
            f"Match all unmatched functions in {source_file}.\n\n"
            f"Use the decomp MCP tools directly. Work through unmatched functions, "
            f"write code, compile, diff, and call mark_complete when a function "
            f"hits 100%."
        )
    elif prior_best_code is not None:
        prompt = (
            f"Match function {function_name} in {source_file}.\n\n"
            f"A previous attempt produced this code:\n\n"
            f"```c\n{prior_best_code}\n```\n\n"
            f"Start from that baseline, improve it with the MCP tools, and stop "
            f"once the function matches."
        )
    else:
        m2c_seed = build_prefetched_m2c_block(
            function_name, source_file, config, max_chars=6000
        )
        prompt = (
            f"Match function {function_name} in {source_file}.\n\n"
            f"Use get_target_assembly, get_context, get_m2c_decompilation, "
            f"write_function, and get_diff to iterate until it matches."
            f"{m2c_seed}"
        )

    container = config.codex_code.container_name
    timeout = config.codex_code.timeout_seconds

    codex_args = [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "--cd",
        str(config.melee.repo_path),
        "--model",
        config.agent.model,
        shlex.quote(prompt),
    ]
    shell_cmd = " ".join(codex_args)
    cmd = ["docker", "exec", container, "sh", "-lc", shell_cmd]

    bound_log.info(
        "codex_headless_start",
        container=container,
        timeout=timeout,
        warm_start=prior_best_code is not None,
    )

    result = AgentResult(
        model="codex-code-headless",
        warm_start=prior_best_code is not None,
    )

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result.elapsed_seconds = time.monotonic() - start_time
        result.termination_reason = "timeout"
        result.error = f"Codex timed out after {timeout}s"
        bound_log.warning("codex_headless_timeout", timeout=timeout)
        return result

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    termination_reason, error_detail = _parse_codex_result(stdout, stderr, result)
    result.termination_reason = termination_reason
    result.error = error_detail or None

    if proc.returncode != 0 and termination_reason == "model_stopped":
        result.termination_reason = "api_error"
        result.error = error_detail or stderr.strip() or stdout.strip()[:500] or "(no output)"

    # Attempt to infer match % from any plain-text summary in the stream.
    pct_match = re.search(r"(\d+(?:\.\d+)?)%\s*match", stdout)
    if pct_match:
        result.best_match_percent = float(pct_match.group(1))

    try:
        from decomp_agent.tools.build import check_match

        check = check_match(source_file, config)
        if check.success:
            if file_mode:
                result.file_mode = True
                result.function_deltas = {
                    f.name: (0.0, f.fuzzy_match_percent) for f in check.functions
                }
                result.newly_matched = [
                    f.name for f in check.functions if f.is_matched
                ]
                if result.newly_matched:
                    result.matched = True
                    result.termination_reason = "matched"
                result.best_match_percent = (
                    sum(f.fuzzy_match_percent for f in check.functions)
                    / max(len(check.functions), 1)
                )
            elif function_name is not None:
                func_result = check.get_function(function_name)
                if func_result is not None:
                    result.best_match_percent = max(
                        result.best_match_percent,
                        func_result.fuzzy_match_percent,
                    )
                    if func_result.is_matched:
                        result.matched = True
                        result.termination_reason = "matched"
                        result.best_match_percent = 100.0
    except Exception:
        bound_log.warning("codex_post_run_check_failed", exc_info=True)

    if function_name is not None:
        try:
            from decomp_agent.tools.source import get_function_source, read_source_file

            src_path = config.melee.resolve_source_path(source_file)
            if src_path.exists():
                source = read_source_file(src_path)
                result.final_code = get_function_source(source, function_name)
        except Exception:
            bound_log.warning("codex_final_code_read_failed", exc_info=True)

    result.elapsed_seconds = time.monotonic() - start_time
    bound_log.info(
        "codex_headless_finished",
        reason=result.termination_reason,
        matched=result.matched,
        best_match=result.best_match_percent,
        iterations=result.iterations,
        elapsed=round(result.elapsed_seconds, 1),
        session_id=result.session_id,
    )
    return result
