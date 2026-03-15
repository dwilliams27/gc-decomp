"""Campaign control-plane helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import Future
import shutil
import time

from sqlalchemy import Engine
from sqlmodel import Session, select

from decomp_agent.agent.loop import AgentResult
from decomp_agent.config import Config
from decomp_agent.models.db import (
    Campaign,
    CampaignTask,
    Function,
    create_campaign,
    complete_campaign_task,
    create_campaign_task,
    defer_campaign_task,
    fail_campaign_task,
    get_campaign,
    get_campaign_task,
    get_next_campaign_task,
    list_campaign_tasks,
    mark_campaign_completed,
    mark_campaign_running,
    mark_campaign_stopped,
    mark_campaign_task_running,
    record_campaign_task_progress,
    requeue_running_campaign_tasks,
    set_campaign_provider_cooldown,
    seed_campaign_function_tasks,
)
VALID_PROVIDERS = frozenset({"claude", "codex"})
VALID_WORKER_POLICIES = frozenset({"claude", "codex", "mixed"})
_HARD_RATE_LIMIT_PATTERNS = (
    "usage limit",
    "reset",
    "try again later",
    "quota",
    "allowance",
)
_CLAUDE_RATE_LIMIT_ANCHOR_UTC = datetime(2026, 3, 13, 7, 4, tzinfo=timezone.utc)
_CLAUDE_RATE_LIMIT_WINDOW = timedelta(hours=5)
@dataclass(frozen=True)
class CampaignSpec:
    source_file: str
    orchestrator_provider: str
    worker_provider_policy: str
    max_active_workers: int
    timeout_hours: int
    root_dir: Path
    allow_shared_fix_workers: bool
    allow_temporary_unmatched_regressions: bool


@dataclass(frozen=True)
class CampaignWorkspace:
    root_dir: Path
    artifact_dir: Path
    staging_worktree_path: Path


@dataclass(frozen=True)
class CampaignRunSummary:
    campaign_id: int
    tasks_run: int
    completed_tasks: int
    failed_tasks: int
    pending_tasks: int
    timed_out: bool
    stopped_by_limit: bool


@dataclass(frozen=True)
class CampaignSupervisorSummary:
    campaign_id: int
    cycles_run: int
    orchestrator_sessions: int
    tasks_run: int
    completed_tasks: int
    failed_tasks: int
    pending_tasks: int
    running_tasks: int
    timed_out: bool
    stopped_by_limit: bool
    stop_reason: str
    no_progress_cycles: int
    summary_path: str = ""


@dataclass(frozen=True)
class RunningTaskLiveStatus:
    live_best_match_pct: float | None
    last_activity_at: datetime | None
    detail: str


def _normalize_task_provider(campaign: Campaign, provider: str) -> str:
    normalized = provider.strip()
    if normalized:
        return normalized
    if campaign.worker_provider_policy in VALID_PROVIDERS:
        return campaign.worker_provider_policy
    return ""


def _load_running_task_live_status(task: CampaignTask) -> RunningTaskLiveStatus | None:
    if task.function_name is None:
        return None
    if task.live_last_activity_at is None and not task.live_status_detail:
        return None
    detail_parts = []
    if task.live_best_match_pct > 0.0:
        detail_parts.append(f"live best seen: {task.live_best_match_pct:.1f}%")
    if task.live_status_detail:
        detail_parts.append(task.live_status_detail)
    if task.live_last_activity_at is not None:
        detail_parts.append(f"last activity: {_ensure_utc(task.live_last_activity_at).isoformat()}")
    detail = ", ".join(detail_parts) if detail_parts else "running with no host progress yet"
    return RunningTaskLiveStatus(
        live_best_match_pct=task.live_best_match_pct or None,
        last_activity_at=_ensure_utc(task.live_last_activity_at) if task.live_last_activity_at else None,
        detail=detail,
    )


def _campaign_notes_path(campaign: Campaign) -> Path | None:
    if not campaign.artifact_dir:
        return None
    artifact_dir = Path(campaign.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir / "manager-notes.md"


def append_campaign_note(engine: Engine, campaign_id: int, note: str) -> str:
    """Append a timestamped manager note to the campaign notes artifact and DB."""
    from decomp_agent.models.db import emit_campaign_event
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        notes_path = _campaign_notes_path(campaign)
        if notes_path is None:
            raise ValueError(f"Campaign #{campaign_id} has no artifact_dir")
        timestamp = datetime.now(timezone.utc).isoformat()
        normalized_note = note.replace("\\n", "\n").strip()
        block = f"## {timestamp}\n\n{normalized_note}\n\n"
        with notes_path.open("a", encoding="utf-8") as handle:
            handle.write(block)
        # Also persist to DB column
        if campaign.notes:
            campaign.notes += block
        else:
            campaign.notes = block
        session.add(campaign)
        session.commit()
        return str(notes_path)


def get_campaign_notes(engine: Engine, campaign_id: int) -> str:
    """Return the accumulated manager notes for a campaign."""
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        notes_path = _campaign_notes_path(campaign)
        if notes_path is None or not notes_path.exists():
            return f"Campaign #{campaign.id} has no manager notes yet."
        return notes_path.read_text(encoding="utf-8")


def _campaign_progress_snapshot(tasks: list[CampaignTask]) -> tuple[int, int, int, int, int]:
    """Return a coarse progress snapshot for no-progress detection."""
    completed = sum(1 for task in tasks if task.status == "completed")
    failed = sum(1 for task in tasks if task.status == "failed")
    pending = sum(1 for task in tasks if task.status == "pending")
    running = sum(1 for task in tasks if task.status == "running")
    total_best_match = int(round(sum(task.best_match_pct for task in tasks)))
    return completed, failed, pending, running, total_best_match


def _write_supervisor_summary_artifact(
    campaign: Campaign,
    summary: CampaignSupervisorSummary,
) -> str:
    """Persist the last supervisor summary for morning-after inspection."""
    if not campaign.artifact_dir:
        return ""
    artifact_dir = Path(campaign.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary_path = artifact_dir / "supervisor-summary.json"
    payload = {
        "campaign_id": summary.campaign_id,
        "campaign_status": campaign.status,
        "cycles_run": summary.cycles_run,
        "orchestrator_sessions": summary.orchestrator_sessions,
        "tasks_run": summary.tasks_run,
        "completed_tasks": summary.completed_tasks,
        "failed_tasks": summary.failed_tasks,
        "pending_tasks": summary.pending_tasks,
        "running_tasks": summary.running_tasks,
        "timed_out": summary.timed_out,
        "stopped_by_limit": summary.stopped_by_limit,
        "stop_reason": summary.stop_reason,
        "no_progress_cycles": summary.no_progress_cycles,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(summary_path)


def _write_supervisor_checkpoint(
    engine: Engine,
    *,
    campaign_id: int,
    cycles_run: int,
    orchestrator_sessions: int,
    tasks_run: int,
    timed_out: bool,
    stopped_by_limit: bool,
    stop_reason: str,
    no_progress_cycles: int,
) -> str:
    """Persist an interim supervisor summary while long-running work is active."""
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        tasks = list_campaign_tasks(session, campaign_id)
        pending_tasks = sum(1 for task in tasks if task.status == "pending")
        running_tasks = sum(1 for task in tasks if task.status == "running")
        failed_tasks = sum(1 for task in tasks if task.status == "failed")
        completed_tasks = sum(1 for task in tasks if task.status == "completed")
        summary = CampaignSupervisorSummary(
            campaign_id=campaign_id,
            cycles_run=cycles_run,
            orchestrator_sessions=orchestrator_sessions,
            tasks_run=tasks_run,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            pending_tasks=pending_tasks,
            running_tasks=running_tasks,
            timed_out=timed_out,
            stopped_by_limit=stopped_by_limit,
            stop_reason=stop_reason,
            no_progress_cycles=no_progress_cycles,
        )
        return _write_supervisor_summary_artifact(campaign, summary)


def _append_supervisor_cycle_note(
    engine: Engine,
    *,
    campaign_id: int,
    cycle_index: int,
    claimed_tasks: list[tuple[int, str]],
    orchestrator_sessions: int,
    stop_reason: str,
) -> None:
    """Record host-side cycle progress even if the manager forgets to write notes."""
    lines = [
        f"Supervisor cycle {cycle_index}",
        f"Orchestrator sessions so far: {orchestrator_sessions}",
    ]
    if claimed_tasks:
        lines.append("Host dispatched tasks:")
        for task_id, label in claimed_tasks:
            lines.append(f"- #{task_id} {label}")
    else:
        lines.append("Host dispatched tasks: none")
    lines.append(f"Current supervisor state: {stop_reason}")
    append_campaign_note(engine, campaign_id, "\n".join(lines))


def _task_label(task: CampaignTask) -> str:
    return task.function_name or task.scope


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize DB-loaded datetimes to UTC-aware values."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_campaign_spec(
    config: Config,
    *,
    source_file: str,
    orchestrator_provider: str | None = None,
    worker_provider_policy: str | None = None,
    max_active_workers: int | None = None,
    timeout_hours: int | None = None,
    allow_shared_fix_workers: bool | None = None,
    allow_temporary_unmatched_regressions: bool | None = None,
) -> CampaignSpec:
    """Build a validated campaign spec from config + CLI overrides."""
    orchestrator = (orchestrator_provider or config.campaign.orchestrator_provider).lower()
    workers = (worker_provider_policy or config.campaign.worker_provider_policy).lower()
    if orchestrator not in VALID_PROVIDERS:
        raise ValueError(
            f"Invalid orchestrator provider '{orchestrator}'. "
            f"Choose from: {', '.join(sorted(VALID_PROVIDERS))}"
        )
    if workers not in VALID_WORKER_POLICIES:
        raise ValueError(
            f"Invalid worker provider policy '{workers}'. "
            f"Choose from: {', '.join(sorted(VALID_WORKER_POLICIES))}"
        )

    return CampaignSpec(
        source_file=source_file,
        orchestrator_provider=orchestrator,
        worker_provider_policy=workers,
        max_active_workers=max_active_workers or config.campaign.max_active_workers,
        timeout_hours=timeout_hours or config.campaign.timeout_hours,
        root_dir=config.campaign.root_dir,
        allow_shared_fix_workers=(
            config.campaign.allow_shared_fix_workers
            if allow_shared_fix_workers is None
            else allow_shared_fix_workers
        ),
        allow_temporary_unmatched_regressions=(
            config.campaign.allow_temporary_unmatched_regressions
            if allow_temporary_unmatched_regressions is None
            else allow_temporary_unmatched_regressions
        ),
    )


def build_campaign_workspace(spec: CampaignSpec, campaign_id: int) -> CampaignWorkspace:
    """Return the filesystem layout for one campaign."""
    root_dir = spec.root_dir / f"campaign-{campaign_id}"
    return CampaignWorkspace(
        root_dir=root_dir,
        artifact_dir=root_dir / "artifacts",
        staging_worktree_path=root_dir / "staging-repo",
    )


def prepare_campaign_workspace(workspace: CampaignWorkspace) -> None:
    """Create the on-disk directory skeleton for a campaign."""
    if workspace.root_dir.exists():
        shutil.rmtree(workspace.root_dir, ignore_errors=True)
    workspace.root_dir.mkdir(parents=True, exist_ok=True)
    workspace.artifact_dir.mkdir(parents=True, exist_ok=True)


def start_campaign(
    session: Session,
    config: Config,
    *,
    source_file: str,
    orchestrator_provider: str | None = None,
    worker_provider_policy: str | None = None,
    max_active_workers: int | None = None,
    timeout_hours: int | None = None,
    allow_shared_fix_workers: bool | None = None,
    allow_temporary_unmatched_regressions: bool | None = None,
) -> Campaign:
    """Create the initial campaign DB record.

    Runtime orchestration is intentionally separate from this first slice.
    """
    spec = build_campaign_spec(
        config,
        source_file=source_file,
        orchestrator_provider=orchestrator_provider,
        worker_provider_policy=worker_provider_policy,
        max_active_workers=max_active_workers,
        timeout_hours=timeout_hours,
        allow_shared_fix_workers=allow_shared_fix_workers,
        allow_temporary_unmatched_regressions=allow_temporary_unmatched_regressions,
    )
    campaign = create_campaign(
        session,
        source_file=spec.source_file,
        orchestrator_provider=spec.orchestrator_provider,
        worker_provider_policy=spec.worker_provider_policy,
        max_active_workers=spec.max_active_workers,
        timeout_hours=spec.timeout_hours,
        allow_shared_fix_workers=spec.allow_shared_fix_workers,
        allow_temporary_unmatched_regressions=spec.allow_temporary_unmatched_regressions,
    )
    workspace = build_campaign_workspace(spec, campaign.id)
    prepare_campaign_workspace(workspace)

    campaign.artifact_dir = str(workspace.artifact_dir)
    campaign.staging_worktree_path = str(workspace.staging_worktree_path)
    session.add(campaign)

    seed_campaign_function_tasks(
        session,
        campaign_id=campaign.id,
        source_file=source_file,
        provider="" if spec.worker_provider_policy == "mixed" else spec.worker_provider_policy,
    )
    session.refresh(campaign)
    return campaign


def _config_for_provider(config: Config, provider: str) -> Config:
    """Return a config copy configured for one campaign worker provider."""
    if provider == "claude":
        return config.model_copy(
            update={
                "claude_code": config.claude_code.model_copy(
                    update={"enabled": True, "isolated_worker_enabled": True}
                ),
                "codex_code": config.codex_code.model_copy(
                    update={"enabled": False, "isolated_worker_enabled": False}
                ),
            }
        )
    if provider == "codex":
        return config.model_copy(
            update={
                "claude_code": config.claude_code.model_copy(update={"enabled": False}),
                "codex_code": config.codex_code.model_copy(
                    update={"enabled": True, "isolated_worker_enabled": True}
                ),
            }
        )
    raise ValueError(f"Unsupported provider '{provider}'")


def _resolve_task_provider(campaign: Campaign, task: CampaignTask) -> str:
    """Resolve the provider to use for a task.

    Mixed campaigns currently default to the orchestrator provider until
    task-level provider routing is implemented.
    """
    if task.provider:
        return task.provider
    if campaign.worker_provider_policy == "mixed":
        return campaign.orchestrator_provider
    return campaign.worker_provider_policy


def _provider_cooldown_until(campaign: Campaign, provider: str) -> datetime | None:
    if provider == "claude":
        return campaign.claude_cooldown_until
    if provider == "codex":
        return campaign.codex_cooldown_until
    return None


def _compute_rate_limit_cooldown(
    config: Config,
    *,
    provider: str,
    error: str,
    retry_count: int,
    now: datetime,
) -> timedelta:
    if provider == "claude":
        if now < _CLAUDE_RATE_LIMIT_ANCHOR_UTC:
            return _CLAUDE_RATE_LIMIT_ANCHOR_UTC - now
        elapsed = now - _CLAUDE_RATE_LIMIT_ANCHOR_UTC
        windows = int(elapsed.total_seconds() // _CLAUDE_RATE_LIMIT_WINDOW.total_seconds()) + 1
        next_reset = _CLAUDE_RATE_LIMIT_ANCHOR_UTC + (windows * _CLAUDE_RATE_LIMIT_WINDOW)
        return next_reset - now
    message = (error or "").lower()
    if any(pattern in message for pattern in _HARD_RATE_LIMIT_PATTERNS):
        return timedelta(hours=config.campaign.rate_limit_reset_hours)
    base = max(config.campaign.rate_limit_backoff_seconds, 30)
    seconds = min(base * (2 ** max(retry_count, 0)), config.campaign.rate_limit_reset_hours * 3600)
    return timedelta(seconds=seconds)


def run_campaign_task_once(
    engine: Engine,
    config: Config,
    *,
    campaign_id: int,
) -> tuple[Campaign, CampaignTask | None, AgentResult | None]:
    """Run the highest-priority pending task for a campaign.

    This intentionally executes only one task. A later orchestrator layer will
    own repeated scheduling and provider/task routing.
    """
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        mark_campaign_running(session, campaign)
        requeue_running_campaign_tasks(session, campaign_id)
        task = get_next_campaign_task(session, campaign_id)
        if task is None:
            return campaign, None, None
        mark_campaign_task_running(session, task)
        task_id = task.id

    return _run_claimed_campaign_task(
        engine,
        config,
        campaign_id=campaign_id,
        task_id=task_id,
    )


def _run_claimed_campaign_task(
    engine: Engine,
    config: Config,
    *,
    campaign_id: int,
    task_id: int,
) -> tuple[Campaign, CampaignTask, AgentResult]:
    """Execute a specific campaign task that is already marked running."""
    from decomp_agent.models.db import Function
    from decomp_agent.orchestrator.runner import run_function

    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        task = get_campaign_task(session, task_id)
        if campaign is None or task is None or task.campaign_id != campaign_id:
            raise ValueError(f"Campaign task #{task_id} not found in campaign #{campaign_id}")
        function_id = task.function_id
        provider = _resolve_task_provider(campaign, task)

    if function_id is None:
        raise ValueError(
            f"Campaign task #{task_id} has no function_id; non-function task execution "
            "is not implemented yet"
        )

    provider_config = _config_for_provider(config, provider)

    with Session(engine) as session:
        function = session.get(Function, function_id)
        if function is None:
            raise ValueError(f"Function #{function_id} not found for campaign task #{task_id}")
        warm_start = function.current_match_pct > 0.0

    try:
        def progress_callback(match_pct: float | None, detail: str) -> None:
            with Session(engine) as progress_session:
                live_task = progress_session.get(CampaignTask, task_id)
                if live_task is None:
                    return
                record_campaign_task_progress(
                    progress_session,
                    live_task,
                    observed_match_pct=match_pct,
                    detail=detail,
                )

        result = run_function(
            function,
            provider_config,
            engine,
            worker_label=f"[campaign {campaign_id} task {task_id}]",
            warm_start=warm_start,
            progress_callback=progress_callback,
        )
    except Exception as exc:
        with Session(engine) as session:
            failed_task = session.get(CampaignTask, task_id)
            if failed_task is None:
                raise ValueError("Campaign task disappeared during execution failure") from exc
            fail_campaign_task(session, failed_task, error=str(exc))
            refreshed_campaign = session.get(Campaign, campaign_id)
            if refreshed_campaign is None:
                raise ValueError("Campaign disappeared during execution failure") from exc
            session.refresh(failed_task)
            session.refresh(refreshed_campaign)
        raise

    with Session(engine) as session:
        refreshed_task = session.get(CampaignTask, task_id)
        refreshed_campaign = session.get(Campaign, campaign_id)
        if refreshed_task is None or refreshed_campaign is None:
            raise ValueError("Campaign state disappeared during task execution")
        if result.termination_reason == "rate_limited":
            now = datetime.now(timezone.utc)
            cooldown = _compute_rate_limit_cooldown(
                config,
                provider=provider,
                error=result.error or "",
                retry_count=refreshed_task.rate_limit_count,
                now=now,
            )
            until = now + cooldown
            defer_campaign_task(
                session,
                refreshed_task,
                until=until,
                error=result.error or "rate limited",
                termination_reason="rate_limited",
            )
            set_campaign_provider_cooldown(
                session,
                refreshed_campaign,
                provider=provider,
                until=until,
            )
        else:
            complete_campaign_task(session, refreshed_task, result)
        session.refresh(refreshed_campaign)
        session.refresh(refreshed_task)
        return refreshed_campaign, refreshed_task, result


def _claim_campaign_tasks(
    engine: Engine,
    *,
    campaign_id: int,
    dispatch_budget: int,
) -> tuple[Campaign, list[int]]:
    """Claim the next batch of pending tasks for dispatch.

    Only isolated Codex workers are dispatched in parallel today. Any task that
    resolves to a shared provider is claimed alone to preserve correctness.
    """
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        pending_tasks = list_campaign_tasks(session, campaign_id)
        claimed: list[int] = []
        for task in pending_tasks:
            if task.status != "pending":
                continue
            provider = _resolve_task_provider(campaign, task)
            cooldown_until = _provider_cooldown_until(campaign, provider)
            now = datetime.now(timezone.utc)
            if cooldown_until is not None and _ensure_utc(cooldown_until) > now:
                continue
            if task.next_eligible_at is not None and _ensure_utc(task.next_eligible_at) > now:
                continue
            isolated_safe = provider in {"codex", "claude"}
            if claimed and not isolated_safe:
                break
            mark_campaign_task_running(session, task)
            claimed.append(task.id)
            if not isolated_safe:
                break
            if len(claimed) >= dispatch_budget:
                break
        session.refresh(campaign)
        return campaign, claimed


def run_campaign_loop(
    engine: Engine,
    config: Config,
    *,
    campaign_id: int,
    max_tasks: int | None = None,
) -> tuple[Campaign, CampaignRunSummary]:
    """Run campaign tasks until the queue is empty, a limit is hit, or timeout expires."""
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        mark_campaign_running(session, campaign)
        requeue_running_campaign_tasks(session, campaign_id)
        session.refresh(campaign)
        started_at = _ensure_utc(campaign.started_at or datetime.now(timezone.utc))
        timeout_deadline = started_at + timedelta(hours=campaign.timeout_hours)

    tasks_run = 0
    timed_out = False
    worker_budget = max(config.campaign.max_active_workers, 1)
    active_futures: dict[Future, int] = {}

    with ThreadPoolExecutor(max_workers=worker_budget) as executor:
        while True:
            completed_futures = [future for future in active_futures if future.done()]
            for future in completed_futures:
                future.result()
                del active_futures[future]

            if datetime.now(timezone.utc) >= timeout_deadline and not active_futures:
                timed_out = True
                break

            with Session(engine) as session:
                campaign = get_campaign(session, campaign_id)
                if campaign is None:
                    raise ValueError("Campaign disappeared during run loop")
                tasks = list_campaign_tasks(session, campaign_id)
                pending_tasks = sum(1 for task in tasks if task.status == "pending")
                running_tasks = sum(1 for task in tasks if task.status == "running")

            if pending_tasks == 0 and running_tasks == 0 and not active_futures:
                break

            remaining_limit = None if max_tasks is None else max(max_tasks - tasks_run, 0)
            available_slots = max(worker_budget - len(active_futures), 0)
            if remaining_limit is not None:
                available_slots = min(available_slots, remaining_limit)

            if available_slots > 0 and datetime.now(timezone.utc) < timeout_deadline:
                _campaign, claimed_task_ids = _claim_campaign_tasks(
                    engine,
                    campaign_id=campaign_id,
                    dispatch_budget=available_slots,
                )
                for task_id in claimed_task_ids:
                    future = executor.submit(
                        _run_claimed_campaign_task,
                        engine,
                        config,
                        campaign_id=campaign_id,
                        task_id=task_id,
                    )
                    active_futures[future] = task_id
                    tasks_run += 1

            if max_tasks is not None and tasks_run >= max_tasks and not active_futures:
                break

            if active_futures:
                time.sleep(1.0)
            else:
                time.sleep(0.25)

    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError("Campaign disappeared during run loop")
        tasks = list_campaign_tasks(session, campaign_id)
        pending_tasks = sum(1 for task in tasks if task.status == "pending")
        failed_tasks = sum(1 for task in tasks if task.status == "failed")
        completed_tasks = sum(1 for task in tasks if task.status == "completed")

        if pending_tasks == 0:
            mark_campaign_completed(session, campaign)
        elif timed_out or (max_tasks is not None and tasks_run >= max_tasks):
            mark_campaign_stopped(session, campaign)

        session.refresh(campaign)

    return campaign, CampaignRunSummary(
        campaign_id=campaign_id,
        tasks_run=tasks_run,
        completed_tasks=completed_tasks,
        failed_tasks=failed_tasks,
        pending_tasks=pending_tasks,
        timed_out=timed_out,
        stopped_by_limit=max_tasks is not None and tasks_run >= max_tasks,
    )


def format_campaign_status(engine: Engine, config: Config, campaign_id: int) -> str:
    """Return a human-readable campaign status summary for an orchestrator."""
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        tasks = list_campaign_tasks(session, campaign_id)

    pending = [task for task in tasks if task.status == "pending"]
    running = [task for task in tasks if task.status == "running"]
    completed = [task for task in tasks if task.status == "completed"]
    failed = [task for task in tasks if task.status == "failed"]

    lines = [
        f"Campaign #{campaign.id} for {campaign.source_file}",
        f"Status: {campaign.status}",
        f"Orchestrator provider: {campaign.orchestrator_provider}",
        f"Worker policy: {campaign.worker_provider_policy}",
        (
            "Tasks: "
            f"{len(completed)} completed, {len(running)} running, "
            f"{len(pending)} pending, {len(failed)} failed"
        ),
    ]

    if campaign.claude_cooldown_until:
        lines.append(f"Claude cooldown until: {_ensure_utc(campaign.claude_cooldown_until).isoformat()}")
    if campaign.codex_cooldown_until:
        lines.append(f"Codex cooldown until: {_ensure_utc(campaign.codex_cooldown_until).isoformat()}")
    notes_path = _campaign_notes_path(campaign)
    if notes_path is not None:
        lines.append(f"Manager notes: {notes_path}")

    if running:
        lines.append("Running tasks:")
        for task in running[:5]:
            provider = _normalize_task_provider(campaign, task.provider)
            live = _load_running_task_live_status(task)
            line = (
                f"  #{task.id} {_task_label(task)} via {provider or 'default'} "
                f"(finalized best: {task.best_match_pct:.1f}%)"
            )
            if live is not None:
                line += f" [{live.detail}]"
            lines.append(line)

    if pending:
        lines.append("Next pending tasks:")
        for task in pending[:10]:
            lines.append(
                f"  #{task.id} {_task_label(task)} "
                f"(priority={task.priority}, provider={_normalize_task_provider(campaign, task.provider) or 'default'})"
            )

    if failed:
        lines.append("Recent failed tasks:")
        for task in failed[-5:]:
            error = task.error or task.termination_reason or "unknown"
            lines.append(f"  #{task.id} {_task_label(task)}: {error}")

    return "\n".join(lines)


def format_campaign_task_result(
    engine: Engine,
    config: Config,
    campaign_id: int,
    task_id: int,
) -> str:
    """Return a detailed result summary for one campaign task."""
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        task = get_campaign_task(session, task_id)
        if task is None or task.campaign_id != campaign_id:
            raise ValueError(f"Campaign task #{task_id} not found in campaign #{campaign_id}")

    lines = [
        f"Campaign task #{task.id}",
        f"Function/scope: {_task_label(task)}",
        f"Status: {task.status}",
        f"Provider: {_normalize_task_provider(campaign, task.provider) or 'default'}",
        f"Finalized best match: {task.best_match_pct:.1f}%",
        f"Termination: {task.termination_reason or '(none)'}",
    ]
    if task.status == "running":
        live = _load_running_task_live_status(task)
        if live is not None:
            lines.append(f"Live status: {live.detail}")
    if task.instructions:
        lines.append(f"Instructions: {task.instructions}")
    if task.worker_session_id:
        lines.append(f"Session: {task.worker_session_id}")
    if task.artifact_dir:
        lines.append(f"Artifacts: {task.artifact_dir}")
    if task.patch_path:
        lines.append(f"Patch: {task.patch_path}")
    if task.error:
        lines.append(f"Error: {task.error}")
    return "\n".join(lines)


def create_campaign_worker_task(
    engine: Engine,
    *,
    campaign_id: int,
    function_name: str,
    provider: str = "",
    instructions: str = "",
    priority: int | None = None,
    scope: str = "function",
) -> CampaignTask:
    """Create a new worker task for a function inside an existing campaign."""
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        function = session.exec(
            select(Function).where(
                Function.name == function_name,
                Function.source_file == campaign.source_file,
            )
        ).first()
        if function is None:
            raise ValueError(
                f"Function '{function_name}' not found in source file {campaign.source_file}"
            )
        task = create_campaign_task(
            session,
            campaign_id=campaign_id,
            source_file=campaign.source_file,
            function_id=function.id,
            function_name=function.name,
            provider=_normalize_task_provider(campaign, provider),
            scope=scope,
            priority=priority if priority is not None else 1000,
            instructions=instructions,
        )
        return task


def retry_campaign_task(
    engine: Engine,
    *,
    campaign_id: int,
    task_id: int,
    instructions: str = "",
    provider: str = "",
    priority: int | None = None,
) -> CampaignTask:
    """Create a follow-up task based on a previous task result."""
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        task = get_campaign_task(session, task_id)
        if task is None or task.campaign_id != campaign_id:
            raise ValueError(f"Campaign task #{task_id} not found in campaign #{campaign_id}")
        retry_instructions = instructions.strip()
        if task.instructions and retry_instructions:
            combined_instructions = f"{task.instructions}\n\nFollow-up guidance:\n{retry_instructions}"
        elif retry_instructions:
            combined_instructions = retry_instructions
        else:
            combined_instructions = task.instructions
        retry_task = create_campaign_task(
            session,
            campaign_id=campaign_id,
            source_file=task.source_file,
            function_id=task.function_id,
            function_name=task.function_name,
            provider=_normalize_task_provider(campaign, provider or task.provider),
            scope=task.scope,
            priority=priority if priority is not None else max(task.priority + 1, 1),
            instructions=combined_instructions,
        )
        return retry_task


def run_campaign_next_task_summary(
    engine: Engine,
    config: Config,
    *,
    campaign_id: int,
) -> str:
    """Return a concise summary of the next queued task for host-side dispatch.

    This tool is exposed to orchestrator agents running inside containers.
    They cannot safely execute worker launches directly because the real worker
    dispatch path is host-controlled. The host supervisor will dispatch pending
    tasks after the orchestrator session ends.
    """
    del config
    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        task = get_next_campaign_task(session, campaign_id)
        if task is None:
            return f"Campaign #{campaign.id} has no pending tasks."

    lines = [
        f"Queued campaign task #{task.id} ({_task_label(task)})",
        "Execution: host supervisor will dispatch this task after the current orchestrator pass",
        f"Status: {task.status}",
        f"Provider: {task.provider or campaign.worker_provider_policy}",
        f"Priority: {task.priority}",
    ]
    if task.instructions:
        lines.append(f"Instructions: {task.instructions}")
    if task.error:
        lines.append(f"Previous error: {task.error}")
    return "\n".join(lines)


def run_campaign_supervisor_loop(
    engine: Engine,
    config: Config,
    *,
    campaign_id: int,
    max_cycles: int | None = None,
    max_tasks_per_cycle: int | None = None,
) -> tuple[Campaign, CampaignSupervisorSummary]:
    """Alternate orchestrator planning with worker execution for one campaign.

    This is intentionally sequential today. It provides an unattended control
    loop before true parallel worker dispatch is implemented.
    """
    from decomp_agent.orchestrator.campaign_orchestrator import (
        run_campaign_orchestrator_loop,
    )

    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign #{campaign_id} not found")
        mark_campaign_running(session, campaign)
        session.refresh(campaign)
        started_at = _ensure_utc(campaign.started_at or datetime.now(timezone.utc))
        timeout_deadline = started_at + timedelta(hours=campaign.timeout_hours)

    cycles_run = 0
    orchestrator_sessions = 0
    tasks_run = 0
    timed_out = False
    stop_reason = "queue_drained"
    no_progress_cycles = 0
    previous_snapshot: tuple[int, int, int, int, int] | None = None

    while True:
        if max_cycles is not None and cycles_run >= max_cycles:
            stop_reason = "max_cycle_limit"
            break
        if datetime.now(timezone.utc) >= timeout_deadline:
            timed_out = True
            stop_reason = "timeout"
            break

        with Session(engine) as session:
            campaign = get_campaign(session, campaign_id)
            if campaign is None:
                raise ValueError(f"Campaign #{campaign_id} disappeared during supervisor loop")
            tasks = list_campaign_tasks(session, campaign_id)
        current_snapshot = _campaign_progress_snapshot(tasks)
        now = datetime.now(timezone.utc)
        pending_tasks = sum(1 for task in tasks if task.status == "pending")
        running_tasks = sum(1 for task in tasks if task.status == "running")
        eligible_pending_tasks = sum(
            1
            for task in tasks
            if task.status == "pending"
            and (task.next_eligible_at is None or _ensure_utc(task.next_eligible_at) <= now)
        )
        if pending_tasks == 0 and running_tasks == 0:
            stop_reason = "queue_drained"
            break

        orchestrator_provider = campaign.orchestrator_provider
        orchestrator_cooldown_until = _provider_cooldown_until(campaign, orchestrator_provider)
        orchestrator_available = (
            orchestrator_cooldown_until is None
            or _ensure_utc(orchestrator_cooldown_until) <= now
        )
        if running_tasks == 0 and eligible_pending_tasks > 0 and orchestrator_available:
            _campaign, orchestrator_summary = run_campaign_orchestrator_loop(
                engine,
                config,
                campaign_id=campaign_id,
                max_sessions=1,
            )
            orchestrator_sessions += orchestrator_summary.sessions_run

        dispatch_budget = max_tasks_per_cycle or max(config.campaign.max_active_workers, 1)
        _campaign, claimed_task_ids = _claim_campaign_tasks(
            engine,
            campaign_id=campaign_id,
            dispatch_budget=dispatch_budget,
        )
        if claimed_task_ids:
            with Session(engine) as session:
                claimed_tasks = []
                for task_id in claimed_task_ids:
                    task = get_campaign_task(session, task_id)
                    if task is not None:
                        claimed_tasks.append((task_id, _task_label(task)))
            tasks_run += len(claimed_task_ids)
            _append_supervisor_cycle_note(
                engine,
                campaign_id=campaign_id,
                cycle_index=cycles_run + 1,
                claimed_tasks=claimed_tasks,
                orchestrator_sessions=orchestrator_sessions,
                stop_reason="dispatching_tasks",
            )
            _write_supervisor_checkpoint(
                engine,
                campaign_id=campaign_id,
                cycles_run=cycles_run,
                orchestrator_sessions=orchestrator_sessions,
                tasks_run=tasks_run,
                timed_out=timed_out,
                stopped_by_limit=max_cycles is not None and (cycles_run + 1) >= max_cycles,
                stop_reason="dispatching_tasks",
                no_progress_cycles=no_progress_cycles,
            )
            if len(claimed_task_ids) == 1:
                _run_claimed_campaign_task(
                    engine,
                    config,
                    campaign_id=campaign_id,
                    task_id=claimed_task_ids[0],
                )
            else:
                with ThreadPoolExecutor(max_workers=len(claimed_task_ids)) as executor:
                    futures = [
                        executor.submit(
                            _run_claimed_campaign_task,
                            engine,
                            config,
                            campaign_id=campaign_id,
                            task_id=task_id,
                        )
                        for task_id in claimed_task_ids
                    ]
                    for future in futures:
                        future.result()

        if not claimed_task_ids and running_tasks == 0:
            _append_supervisor_cycle_note(
                engine,
                campaign_id=campaign_id,
                cycle_index=cycles_run + 1,
                claimed_tasks=[],
                orchestrator_sessions=orchestrator_sessions,
                stop_reason="waiting_for_next_eligible_task",
            )
            _write_supervisor_checkpoint(
                engine,
                campaign_id=campaign_id,
                cycles_run=cycles_run,
                orchestrator_sessions=orchestrator_sessions,
                tasks_run=tasks_run,
                timed_out=timed_out,
                stopped_by_limit=max_cycles is not None and (cycles_run + 1) >= max_cycles,
                stop_reason="waiting_for_next_eligible_task",
                no_progress_cycles=no_progress_cycles,
            )
            with Session(engine) as session:
                campaign = get_campaign(session, campaign_id)
                if campaign is None:
                    raise ValueError(f"Campaign #{campaign_id} disappeared during cooldown check")
                tasks = list_campaign_tasks(session, campaign_id)
            now = datetime.now(timezone.utc)
            candidate_times: list[datetime] = []
            for provider in VALID_PROVIDERS:
                cooldown_until = _provider_cooldown_until(campaign, provider)
                if cooldown_until is not None and _ensure_utc(cooldown_until) > now:
                    candidate_times.append(_ensure_utc(cooldown_until))
            for task in tasks:
                if task.status != "pending" or task.next_eligible_at is None:
                    continue
                eligible_at = _ensure_utc(task.next_eligible_at)
                if eligible_at > now:
                    candidate_times.append(eligible_at)
            if candidate_times:
                next_ready = min(candidate_times)
                sleep_seconds = min(max((next_ready - now).total_seconds(), 1.0), 300.0)
                stop_reason = "provider_cooldown"
                time.sleep(sleep_seconds)
                previous_snapshot = current_snapshot
                continue

        cycles_run += 1
        with Session(engine) as session:
            refreshed_tasks = list_campaign_tasks(session, campaign_id)
        next_snapshot = _campaign_progress_snapshot(refreshed_tasks)
        if next_snapshot == previous_snapshot == current_snapshot:
            no_progress_cycles += 1
        else:
            no_progress_cycles = 0
        previous_snapshot = next_snapshot

        if (
            config.campaign.max_no_progress_cycles > 0
            and no_progress_cycles >= config.campaign.max_no_progress_cycles
        ):
            stop_reason = "no_progress_limit"
            break

    with Session(engine) as session:
        campaign = get_campaign(session, campaign_id)
        if campaign is None:
            raise ValueError("Campaign disappeared during supervisor loop")
        tasks = list_campaign_tasks(session, campaign_id)
        pending_tasks = sum(1 for task in tasks if task.status == "pending")
        running_tasks = sum(1 for task in tasks if task.status == "running")
        failed_tasks = sum(1 for task in tasks if task.status == "failed")
        completed_tasks = sum(1 for task in tasks if task.status == "completed")

        if pending_tasks == 0 and running_tasks == 0:
            mark_campaign_completed(session, campaign)
            stop_reason = "queue_drained"
        elif (
            timed_out
            or (max_cycles is not None and cycles_run >= max_cycles)
            or stop_reason == "no_progress_limit"
        ):
            mark_campaign_stopped(session, campaign)
        session.refresh(campaign)

    summary = CampaignSupervisorSummary(
        campaign_id=campaign_id,
        cycles_run=cycles_run,
        orchestrator_sessions=orchestrator_sessions,
        tasks_run=tasks_run,
        completed_tasks=completed_tasks,
        failed_tasks=failed_tasks,
        pending_tasks=pending_tasks,
        running_tasks=running_tasks,
        timed_out=timed_out,
        stopped_by_limit=max_cycles is not None and cycles_run >= max_cycles,
        stop_reason=stop_reason,
        no_progress_cycles=no_progress_cycles,
    )
    summary_path = _write_supervisor_summary_artifact(campaign, summary)
    if summary_path:
        summary = CampaignSupervisorSummary(
            campaign_id=summary.campaign_id,
            cycles_run=summary.cycles_run,
            orchestrator_sessions=summary.orchestrator_sessions,
            tasks_run=summary.tasks_run,
            completed_tasks=summary.completed_tasks,
            failed_tasks=summary.failed_tasks,
            pending_tasks=summary.pending_tasks,
            running_tasks=summary.running_tasks,
            timed_out=summary.timed_out,
            stopped_by_limit=summary.stopped_by_limit,
            stop_reason=summary.stop_reason,
            no_progress_cycles=summary.no_progress_cycles,
            summary_path=summary_path,
        )
    return campaign, summary
