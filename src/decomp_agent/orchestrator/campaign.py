"""Campaign control-plane helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session

from decomp_agent.agent.loop import AgentResult
from decomp_agent.config import Config
from decomp_agent.models.db import (
    Campaign,
    CampaignTask,
    create_campaign,
    complete_campaign_task,
    fail_campaign_task,
    get_campaign,
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
