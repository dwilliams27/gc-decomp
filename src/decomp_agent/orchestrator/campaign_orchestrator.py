"""Provider-specific runner for campaign orchestrator sessions."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, select

from decomp_agent.agent.loop import AgentResult
from decomp_agent.config import Config
from decomp_agent.models.db import Campaign, CampaignTask, get_campaign
from decomp_agent.orchestrator.headless import (
    cleanup_shared_claude_processes,
    claude_shared_worker_lock,
)
from decomp_agent.orchestrator.campaign import (
    _compute_rate_limit_cooldown,
    _ensure_utc,
    _provider_cooldown_until,
)
from decomp_agent.orchestrator.headless_context import (
    build_campaign_orchestrator_prompt,
    load_campaign_orchestrator_system_prompt,
)


@dataclass(frozen=True)
class CampaignOrchestratorSummary:
    campaign_id: int
    sessions_run: int
    pending_tasks: int
    running_tasks: int
    completed_tasks: int
    failed_tasks: int
    timed_out: bool
    stopped_by_limit: bool


@contextmanager
def _campaign_orchestrator_lock(campaign: Campaign):
    """Prevent overlapping orchestrator sessions for one campaign."""
    artifact_dir = Path(campaign.artifact_dir) if campaign.artifact_dir else None
    if artifact_dir is None:
        raise ValueError(f"Campaign #{campaign.id} has no artifact_dir for lock management")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    lock_path = artifact_dir / "orchestrator.lock"
    payload = json.dumps(
        {
            "campaign_id": campaign.id,
            "pid": os.getpid(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            existing = {}
        stale_pid = int(existing.get("pid", -1)) if isinstance(existing, dict) else -1
        if stale_pid > 0:
            try:
                os.kill(stale_pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
        else:
            alive = False
        if not alive:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        else:
            raise RuntimeError(
                f"Campaign #{campaign.id} already has an active orchestrator session "
                f"({lock_path})"
            ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _store_orchestrator_session_id(
    engine: Engine,
    *,
    campaign_id: int,
    session_id: str,
) -> None:
    if not session_id:
        return
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        campaign.orchestrator_session_id = session_id
        session.add(campaign)
        session.commit()


def _set_orchestrator_provider_cooldown(
    engine: Engine,
    *,
    campaign_id: int,
    provider: str,
    until: datetime,
) -> None:
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        if provider == "claude":
            campaign.claude_cooldown_until = until
        elif provider == "codex":
            campaign.codex_cooldown_until = until
        else:
            raise ValueError(f"Unsupported provider '{provider}'")
        campaign.updated_at = datetime.now(timezone.utc)
        session.add(campaign)
        session.commit()


def _run_claude_orchestrator(
    campaign: Campaign,
    prompt: str,
    config: Config,
) -> AgentResult:
    start_time = time.monotonic()
    result = AgentResult(model="claude-campaign-orchestrator")
    max_turns = max(config.claude_code.orchestrator_max_turns, 1)
    timeout = max(config.claude_code.orchestrator_timeout_seconds, 60)
    system_prompt = load_campaign_orchestrator_system_prompt()

    claude_args = [
        "claude",
        "-p", shlex.quote(prompt),
        "--output-format", "json",
        "--model", "claude-opus-4-6",
        "--append-system-prompt", shlex.quote(system_prompt),
        "--mcp-config", "/app/mcp.json",
        "--dangerously-skip-permissions",
        "--max-turns", str(max_turns),
    ]
    cmd = [
        "docker",
        "exec",
        config.claude_code.container_name,
        "sh",
        "-c",
        " ".join(claude_args),
    ]
    with claude_shared_worker_lock():
        cleanup_shared_claude_processes(config)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            cleanup_shared_claude_processes(config)
            result.elapsed_seconds = time.monotonic() - start_time
            result.termination_reason = "timeout"
            result.error = f"Claude orchestrator timed out after {timeout}s"
            return result

    if proc.returncode != 0:
        result.elapsed_seconds = time.monotonic() - start_time
        result.termination_reason = "api_error"
        result.error = proc.stderr.strip() or proc.stdout.strip()[:500] or "(no output)"
        return result

    try:
        output = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        result.elapsed_seconds = time.monotonic() - start_time
        result.termination_reason = "api_error"
        result.error = f"Failed to parse Claude orchestrator JSON: {exc}"
        return result

    usage = output.get("usage", {})
    result.input_tokens = usage.get("input_tokens", 0)
    result.output_tokens = usage.get("output_tokens", 0)
    result.cached_tokens = usage.get("cache_read_input_tokens", 0)
    result.total_tokens = result.input_tokens + result.output_tokens
    result.session_id = output.get("session_id", "")
    result.iterations = output.get("num_turns", 0)
    result.final_code = output.get("result", "")
    subtype = output.get("subtype", "")
    result.termination_reason = (
        "max_iterations" if subtype == "error_max_turns" else "model_stopped"
    )
    result.elapsed_seconds = time.monotonic() - start_time
    return result


def _run_codex_orchestrator(
    campaign: Campaign,
    prompt: str,
    config: Config,
) -> AgentResult:
    from decomp_agent.orchestrator.codex_headless import _parse_codex_result

    start_time = time.monotonic()
    result = AgentResult(model="codex-campaign-orchestrator")
    timeout = max(config.codex_code.timeout_seconds, 5400)
    system_prompt = load_campaign_orchestrator_system_prompt()
    combined_prompt = f"{system_prompt}\n\n## Campaign Assignment\n\n{prompt}"

    if campaign.orchestrator_session_id:
        codex_args = [
            "codex",
            "exec",
            "resume",
            campaign.orchestrator_session_id,
            shlex.quote(combined_prompt),
        ]
    else:
        codex_args = [
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--cd",
            str(config.melee.repo_path),
            "--model",
            config.agent.model,
            shlex.quote(combined_prompt),
        ]

    cmd = [
        "docker",
        "exec",
        config.codex_code.container_name,
        "sh",
        "-lc",
        " ".join(codex_args),
    ]
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
        result.error = f"Codex orchestrator timed out after {timeout}s"
        return result

    termination_reason, error_detail = _parse_codex_result(proc.stdout or "", proc.stderr or "", result)
    result.termination_reason = termination_reason
    result.error = error_detail or None
    result.final_code = (proc.stdout or "").strip()[-4000:] or None
    result.elapsed_seconds = time.monotonic() - start_time
    if proc.returncode != 0 and termination_reason == "model_stopped":
        result.termination_reason = "api_error"
        result.error = error_detail or proc.stderr.strip() or "(no output)"
    return result


def run_campaign_orchestrator_once(
    engine: Engine,
    config: Config,
    *,
    campaign_id: int,
) -> tuple[Campaign, AgentResult]:
    """Run one orchestrator session for a campaign using its configured provider."""
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
    with _campaign_orchestrator_lock(campaign):
        prompt = build_campaign_orchestrator_prompt(campaign_id, campaign.source_file, config)

        if campaign.orchestrator_provider == "claude":
            result = _run_claude_orchestrator(campaign, prompt, config)
        elif campaign.orchestrator_provider == "codex":
            result = _run_codex_orchestrator(campaign, prompt, config)
        else:
            raise ValueError(f"Unsupported orchestrator provider '{campaign.orchestrator_provider}'")

        _store_orchestrator_session_id(
            engine,
            campaign_id=campaign_id,
            session_id=result.session_id,
        )

    with Session(engine) as session:
        refreshed_campaign = get_campaign(session, campaign_id)
        if refreshed_campaign is None:
            raise ValueError(f"Campaign #{campaign_id} disappeared after orchestrator run")
        return refreshed_campaign, result


def run_campaign_orchestrator_loop(
    engine: Engine,
    config: Config,
    *,
    campaign_id: int,
    max_sessions: int | None = None,
) -> tuple[Campaign, CampaignOrchestratorSummary]:
    """Run orchestrator sessions repeatedly until work is exhausted, timeout hits, or a limit is reached."""
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        started_at = campaign.started_at or datetime.now(timezone.utc)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        timeout_deadline = started_at + timedelta(hours=campaign.timeout_hours)

    sessions_run = 0
    timed_out = False

    while True:
        if max_sessions is not None and sessions_run >= max_sessions:
            break

        if datetime.now(timezone.utc) >= timeout_deadline:
            timed_out = True
            break

        with Session(engine) as session:
            tasks = session.exec(
                select(CampaignTask).where(CampaignTask.campaign_id == campaign_id)
            ).all()
            campaign = get_campaign(session, campaign_id)
            if campaign is None:
                raise ValueError(f"Campaign #{campaign_id} not found")
        pending_tasks = sum(1 for task in tasks if task.status == "pending")
        running_tasks = sum(1 for task in tasks if task.status == "running")
        if pending_tasks == 0 and running_tasks == 0:
            break

        cooldown_until = _provider_cooldown_until(campaign, campaign.orchestrator_provider)
        now = datetime.now(timezone.utc)
        if cooldown_until is not None and _ensure_utc(cooldown_until) > now:
            if max_sessions is not None:
                break
            sleep_seconds = min(
                max((_ensure_utc(cooldown_until) - now).total_seconds(), 1.0),
                max(float(config.campaign.orchestrator_poll_seconds), 1.0),
            )
            time.sleep(sleep_seconds)
            continue

        refreshed_campaign, result = run_campaign_orchestrator_once(
            engine,
            config,
            campaign_id=campaign_id,
        )
        sessions_run += 1
        if result is not None and result.termination_reason == "rate_limited":
            now = datetime.now(timezone.utc)
            cooldown = _compute_rate_limit_cooldown(
                config,
                provider=refreshed_campaign.orchestrator_provider,
                error=result.error or "",
                retry_count=0,
                now=now,
            )
            _set_orchestrator_provider_cooldown(
                engine,
                campaign_id=campaign_id,
                provider=refreshed_campaign.orchestrator_provider,
                until=now + cooldown,
            )
            break

        with Session(engine) as session:
            tasks = session.exec(
                select(CampaignTask).where(CampaignTask.campaign_id == campaign_id)
            ).all()
        running_after = sum(1 for task in tasks if task.status == "running")
        pending_after = sum(1 for task in tasks if task.status == "pending")
        if running_after > 0:
            if max_sessions is not None:
                break
            time.sleep(max(config.campaign.orchestrator_poll_seconds, 1))
            continue
        if pending_after == 0:
            break

    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} disappeared after orchestrator loop")
        tasks = session.exec(
            select(CampaignTask).where(CampaignTask.campaign_id == campaign_id)
        ).all()

    pending_tasks = sum(1 for task in tasks if task.status == "pending")
    running_tasks = sum(1 for task in tasks if task.status == "running")
    completed_tasks = sum(1 for task in tasks if task.status == "completed")
    failed_tasks = sum(1 for task in tasks if task.status == "failed")

    return campaign, CampaignOrchestratorSummary(
        campaign_id=campaign_id,
        sessions_run=sessions_run,
        pending_tasks=pending_tasks,
        running_tasks=running_tasks,
        completed_tasks=completed_tasks,
        failed_tasks=failed_tasks,
        timed_out=timed_out,
        stopped_by_limit=max_sessions is not None and sessions_run >= max_sessions,
    )
