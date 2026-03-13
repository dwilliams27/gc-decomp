"""Campaign control-plane helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    fail_campaign_task,
    get_campaign,
    get_campaign_task,
    get_next_campaign_task,
    list_campaign_tasks,
    mark_campaign_completed,
    mark_campaign_running,
    mark_campaign_stopped,
    mark_campaign_task_running,
    requeue_running_campaign_tasks,
    seed_campaign_function_tasks,
)

VALID_PROVIDERS = frozenset({"claude", "codex"})
VALID_WORKER_POLICIES = frozenset({"claude", "codex", "mixed"})


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
                "claude_code": config.claude_code.model_copy(update={"enabled": True}),
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
        function_id = task.function_id
        campaign_worker_provider_policy = campaign.worker_provider_policy
        campaign_orchestrator_provider = campaign.orchestrator_provider
        task_provider = task.provider
        task_id = task.id

    if function_id is None:
        raise ValueError(
            f"Campaign task #{task_id} has no function_id; non-function task execution "
            "is not implemented yet"
        )

    provider = (
        task_provider
        or (
            campaign_orchestrator_provider
            if campaign_worker_provider_policy == "mixed"
            else campaign_worker_provider_policy
        )
    )
    provider_config = _config_for_provider(config, provider)

    from decomp_agent.models.db import Function
    from decomp_agent.orchestrator.runner import run_function

    with Session(engine) as session:
        function = session.get(Function, function_id)
        if function is None:
            raise ValueError(f"Function #{function_id} not found for campaign task #{task_id}")

    try:
        result = run_function(
            function,
            provider_config,
            engine,
            worker_label=f"[campaign {campaign_id} task {task_id}]",
            warm_start=False,
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
        complete_campaign_task(session, refreshed_task, result)
        session.refresh(refreshed_campaign)
        session.refresh(refreshed_task)
        return refreshed_campaign, refreshed_task, result


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
        session.refresh(campaign)
        started_at = _ensure_utc(campaign.started_at or datetime.now(timezone.utc))
        timeout_deadline = started_at + timedelta(hours=campaign.timeout_hours)

    tasks_run = 0
    timed_out = False

    while True:
        if max_tasks is not None and tasks_run >= max_tasks:
            break

        if datetime.now(timezone.utc) >= timeout_deadline:
            timed_out = True
            break

        _campaign, task, _result = run_campaign_task_once(
            engine,
            config,
            campaign_id=campaign_id,
        )
        if task is None:
            break
        tasks_run += 1

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


def format_campaign_status(engine: Engine, campaign_id: int) -> str:
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

    if running:
        lines.append("Running tasks:")
        for task in running[:5]:
            lines.append(
                f"  #{task.id} {_task_label(task)} via {task.provider or 'default'}"
            )

    if pending:
        lines.append("Next pending tasks:")
        for task in pending[:10]:
            lines.append(
                f"  #{task.id} {_task_label(task)} "
                f"(priority={task.priority}, provider={task.provider or 'default'})"
            )

    if failed:
        lines.append("Recent failed tasks:")
        for task in failed[-5:]:
            error = task.error or task.termination_reason or "unknown"
            lines.append(f"  #{task.id} {_task_label(task)}: {error}")

    return "\n".join(lines)


def format_campaign_task_result(engine: Engine, campaign_id: int, task_id: int) -> str:
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
        f"Provider: {task.provider or campaign.worker_provider_policy}",
        f"Best match: {task.best_match_pct:.1f}%",
        f"Termination: {task.termination_reason or '(none)'}",
    ]
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
            provider=provider,
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
            provider=provider or task.provider,
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
    """Run one queued campaign task and return a concise summary string."""
    campaign, task, result = run_campaign_task_once(
        engine,
        config,
        campaign_id=campaign_id,
    )
    if task is None:
        return f"Campaign #{campaign.id} has no pending tasks."

    lines = [
        f"Ran campaign task #{task.id} ({_task_label(task)})",
        f"Status: {task.status}",
        f"Provider: {task.provider or campaign.worker_provider_policy}",
        f"Best match: {task.best_match_pct:.1f}%",
        f"Termination: {task.termination_reason or '(none)'}",
    ]
    if result and result.session_id:
        lines.append(f"Session: {result.session_id}")
    if task.artifact_dir:
        lines.append(f"Artifacts: {task.artifact_dir}")
    if task.error:
        lines.append(f"Error: {task.error}")
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

    while True:
        if max_cycles is not None and cycles_run >= max_cycles:
            break
        if datetime.now(timezone.utc) >= timeout_deadline:
            timed_out = True
            break

        with Session(engine) as session:
            tasks = list_campaign_tasks(session, campaign_id)
        pending_tasks = sum(1 for task in tasks if task.status == "pending")
        running_tasks = sum(1 for task in tasks if task.status == "running")
        if pending_tasks == 0 and running_tasks == 0:
            break

        if running_tasks == 0:
            _campaign, orchestrator_summary = run_campaign_orchestrator_loop(
                engine,
                config,
                campaign_id=campaign_id,
                max_sessions=1,
            )
            orchestrator_sessions += orchestrator_summary.sessions_run

        dispatch_budget = max_tasks_per_cycle or max(config.campaign.max_active_workers, 1)
        dispatched_this_cycle = 0
        while dispatched_this_cycle < dispatch_budget:
            if datetime.now(timezone.utc) >= timeout_deadline:
                timed_out = True
                break
            _campaign, task, _result = run_campaign_task_once(
                engine,
                config,
                campaign_id=campaign_id,
            )
            if task is None:
                break
            tasks_run += 1
            dispatched_this_cycle += 1

        cycles_run += 1
        if timed_out:
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
        elif timed_out or (max_cycles is not None and cycles_run >= max_cycles):
            mark_campaign_stopped(session, campaign)
        session.refresh(campaign)

    return campaign, CampaignSupervisorSummary(
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
    )
