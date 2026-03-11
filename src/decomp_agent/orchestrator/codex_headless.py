"""Headless Codex CLI runner for decomp agent."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from pathlib import Path

import structlog

from decomp_agent.agent.loop import AgentResult
from decomp_agent.config import Config
from decomp_agent.orchestrator.headless_context import (
    build_headless_task_prompt,
    load_headless_system_prompt,
)
from decomp_agent.orchestrator.worker_launcher import (
    WorkerSpec,
    build_worker_container_run_args,
    create_worker_spec,
)
from decomp_agent.orchestrator.worker_results import (
    export_worker_patch,
    write_worker_artifact_manifest,
    write_worker_result,
)

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

def _config_for_repo_path(config: Config, repo_path: Path) -> Config:
    return config.model_copy(
        update={
            "melee": config.melee.model_copy(update={"repo_path": repo_path}),
            "docker": config.docker.model_copy(update={"enabled": False}),
        }
    )


def _post_run_check(
    *,
    result: AgentResult,
    function_name: str | None,
    source_file: str,
    config: Config,
    file_mode: bool,
) -> None:
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
        log.warning("codex_post_run_check_failed", exc_info=True)


def _read_final_code(
    *,
    result: AgentResult,
    function_name: str | None,
    source_file: str,
    config: Config,
) -> None:
    if function_name is None:
        return
    try:
        from decomp_agent.tools.source import get_function_source, read_source_file

        src_path = config.melee.resolve_source_path(source_file)
        if src_path.exists():
            source = read_source_file(src_path)
            result.final_code = get_function_source(source, function_name)
    except Exception:
        log.warning("codex_final_code_read_failed", exc_info=True)


def _run_shared_container(
    *,
    prompt: str,
    config: Config,
    bound_log,
) -> subprocess.CompletedProcess[str]:
    system_prompt = load_headless_system_prompt()
    combined_prompt = (
        f"{system_prompt}\n\n"
        f"## Your Assignment\n\n"
        f"{prompt}"
    )
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
        shlex.quote(combined_prompt),
    ]
    shell_cmd = " ".join(codex_args)
    cmd = ["docker", "exec", config.codex_code.container_name, "sh", "-lc", shell_cmd]
    bound_log.info(
        "codex_headless_start",
        container=config.codex_code.container_name,
        timeout=config.codex_code.timeout_seconds,
    )
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=config.codex_code.timeout_seconds,
    )


def _run_isolated_worker(
    *,
    function_name: str | None,
    source_file: str,
    prompt: str,
    config: Config,
    bound_log,
) -> tuple[subprocess.CompletedProcess[str], Config, AgentResult, WorkerSpec]:
    spec = create_worker_spec(
        config,
        source_file=source_file,
        function_name=function_name,
    )
    write_worker_artifact_manifest(spec)
    worker_config = _config_for_repo_path(config, spec.melee_worktree.worktree_path)
    start_args = build_worker_container_run_args(spec, config)

    subprocess.run(
        start_args,
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        system_prompt = load_headless_system_prompt()
        combined_prompt = (
            f"{system_prompt}\n\n"
            f"## Your Assignment\n\n"
            f"{prompt}"
        )
        codex_args = [
            "codex",
            "exec",
            "--json",
            "--ephemeral",
            "--dangerously-bypass-approvals-and-sandbox",
            "--cd",
            str(spec.melee_worktree.worktree_path),
            "--model",
            config.agent.model,
            shlex.quote(combined_prompt),
        ]
        shell_cmd = " ".join(codex_args)
        cmd = ["docker", "exec", spec.container_name, "sh", "-lc", shell_cmd]
        bound_log.info(
            "codex_isolated_start",
            container=spec.container_name,
            worker_id=spec.worker_id,
            artifact_dir=str(spec.output_dir),
        )
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.codex_code.timeout_seconds,
        )
    finally:
        subprocess.run(
            ["docker", "stop", spec.container_name],
            capture_output=True,
            text=True,
        )

    result = AgentResult(model="codex-code-headless")
    result.artifact_dir = str(spec.output_dir)
    result.patch_path = str(export_worker_patch(spec))
    return proc, worker_config, result, spec


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
    file_mode = function_name is None
    start_time = time.monotonic()
    bound_log = log.bind(
        function=function_name or "(file mode)",
        source_file=source_file,
        worker=worker_label,
        file_mode=file_mode,
    )

    prompt = build_headless_task_prompt(
        function_name,
        source_file,
        config,
        prior_best_code=prior_best_code,
        prior_match_pct=prior_match_pct,
    )

    timeout = config.codex_code.timeout_seconds

    result = AgentResult(
        model="codex-code-headless",
        warm_start=prior_best_code is not None,
    )

    try:
        if config.codex_code.isolated_worker_enabled:
            proc, worker_config, isolated_result, isolated_spec = _run_isolated_worker(
                function_name=function_name,
                source_file=source_file,
                prompt=prompt,
                config=config,
                bound_log=bound_log,
            )
            result.artifact_dir = isolated_result.artifact_dir
            result.patch_path = isolated_result.patch_path
        else:
            proc = _run_shared_container(
                prompt=prompt,
                config=config,
                bound_log=bound_log,
            )
            worker_config = config
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

    _post_run_check(
        result=result,
        function_name=function_name,
        source_file=source_file,
        config=worker_config,
        file_mode=file_mode,
    )

    _read_final_code(
        result=result,
        function_name=function_name,
        source_file=source_file,
        config=worker_config,
    )

    if config.codex_code.isolated_worker_enabled:
        if result.matched:
            result.matched = False
            result.termination_reason = "isolated_patch_ready"
            result.error = (
                f"Worker produced a matching patch in {result.patch_path}. "
                f"Primary checkout was not modified."
            )
        elif result.patch_path:
            result.error = (
                result.error
                or f"Worker artifacts captured in {result.artifact_dir}"
            )
        write_worker_result(
            isolated_spec,
            result,
            extra={"source_file": source_file, "function_name": function_name},
        )

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
