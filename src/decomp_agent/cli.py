"""CLI entry point for decomp-agent."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from sqlmodel import Session, func, select

from decomp_agent.config import load_config
from decomp_agent.models.db import (
    Campaign,
    CampaignTask,
    Function,
    backup_database_files,
    check_database_integrity,
    get_engine,
    reset_database_files,
    sync_from_report,
)

console = Console()


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Path to config TOML file (default: config/default.toml)",
)
@click.option(
    "--log-level",
    default=None,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="Log level (default: INFO)",
)
@click.pass_context
def main(ctx: click.Context, config_path: Path | None, log_level: str | None) -> None:
    """Automated decompilation agent for Super Smash Bros. Melee."""
    from decomp_agent.logging import configure_logging

    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["log_level"] = log_level

    configure_logging(level=log_level or "INFO")


def _load(ctx: click.Context):
    """Load config and engine from click context."""
    config = load_config(ctx.obj.get("config_path"))
    engine = get_engine(config.orchestration.db_path)
    return config, engine


def _enable_headless_provider(
    config,
    *,
    claude_headless: bool,
    codex_headless: bool,
    isolated_worker: bool = False,
) -> None:
    """Enable exactly one headless provider, if requested."""
    if isolated_worker:
        codex_headless = True
    if claude_headless and codex_headless:
        raise click.ClickException(
            "Choose only one headless provider: --headless or --codex-headless"
        )
    if claude_headless:
        config.claude_code.enabled = True
        config.codex_code.enabled = False
    elif codex_headless:
        config.codex_code.enabled = True
        config.claude_code.enabled = False
    if isolated_worker:
        config.codex_code.isolated_worker_enabled = True


def _provider_choice(value: str | None, *, allow_mixed: bool = False) -> str | None:
    """Normalize campaign provider selections."""
    if value is None:
        return None
    normalized = value.lower()
    choices = {"claude", "codex"}
    if allow_mixed:
        choices.add("mixed")
    if normalized not in choices:
        allowed = ", ".join(sorted(choices))
        raise click.ClickException(f"Invalid provider '{value}'. Choose from: {allowed}")
    return normalized


@main.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Scan melee repo and populate DB from report.json."""
    config, engine = _load(ctx)

    console.print("Loading functions from melee repo...")
    from decomp_agent.melee.functions import get_candidates, get_functions

    functions = get_functions(config)
    candidates = get_candidates(functions)

    console.print(f"Found {len(functions):,} total functions, {len(candidates):,} candidates")

    with Session(engine) as session:
        inserted = sync_from_report(session, candidates)

    console.print(f"Synced to DB: {inserted:,} new functions inserted")
    console.print(f"Database: {config.orchestration.db_path}")


@main.group("db")
def db_group() -> None:
    """Inspect and reset the local SQLite database."""


@db_group.command("check")
@click.pass_context
def db_check(ctx: click.Context) -> None:
    """Run SQLite integrity_check on the configured database."""
    config = load_config(ctx.obj.get("config_path"))
    status = check_database_integrity(config.orchestration.db_path)
    console.print(f"Database: {config.orchestration.db_path}")
    if status == "ok":
        console.print("[green]integrity_check: ok[/green]")
    elif status == "missing":
        console.print("[yellow]integrity_check: missing[/yellow]")
    else:
        console.print(f"[red]integrity_check: {status}[/red]")
        raise SystemExit(1)


@db_group.command("reset")
@click.option(
    "--backup-dir",
    type=click.Path(path_type=Path),
    default=Path("db-backups"),
    show_default=True,
    help="Directory to store a backup of the old database before resetting",
)
@click.pass_context
def db_reset(ctx: click.Context, backup_dir: Path) -> None:
    """Back up the current DB, recreate it, and re-seed functions from report.json."""
    config = load_config(ctx.obj.get("config_path"))
    db_path = config.orchestration.db_path
    backup = backup_database_files(db_path, backup_root=backup_dir)
    reset_database_files(db_path)
    engine = get_engine(db_path)

    console.print(f"Reset database: {db_path}")
    if backup is not None:
        console.print(f"  Backup: {backup}")
    else:
        console.print("  Backup: (no existing DB files)")

    console.print("Reloading functions from melee repo...")
    from decomp_agent.melee.functions import get_candidates, get_functions

    functions = get_functions(config)
    candidates = get_candidates(functions)
    with Session(engine) as session:
        inserted = sync_from_report(session, candidates)

    console.print(f"  Inserted: {inserted:,} functions")
    console.print(f"  Integrity: {check_database_integrity(db_path)}")


@main.command()
@click.argument("name")
@click.option("--max-tokens", default=None, type=int, help="Max tokens per attempt (e.g. 5000000)")
@click.option("--max-iterations", default=None, type=int, help="Max agent iterations")
@click.option("--warm-start", is_flag=True, default=False, help="Seed with best prior attempt code")
@click.option("--headless", is_flag=True, default=False, help="Use Claude Code headless mode (Max subscription)")
@click.option("--codex-headless", is_flag=True, default=False, help="Use Codex CLI headless mode (ChatGPT/Codex subscription)")
@click.option("--isolated-worker", is_flag=True, default=False, help="Run Codex headless inside an isolated worker worktree/container")
@click.pass_context
def run(
    ctx: click.Context,
    name: str,
    max_tokens: int | None,
    max_iterations: int | None,
    warm_start: bool,
    headless: bool,
    codex_headless: bool,
    isolated_worker: bool,
) -> None:
    """Run agent on a single function by name."""
    config, engine = _load(ctx)

    _enable_headless_provider(
        config,
        claude_headless=headless,
        codex_headless=codex_headless,
        isolated_worker=isolated_worker,
    )
    if max_tokens is not None:
        config.agent.max_tokens_per_attempt = max_tokens
    if max_iterations is not None:
        config.agent.max_iterations = max_iterations

    from decomp_agent.orchestrator.runner import run_function

    with Session(engine) as session:
        function = session.exec(
            select(Function).where(Function.name == name)
        ).first()

    if function is None:
        console.print(f"[red]Function '{name}' not found in DB. Run 'init' first.[/red]")
        raise SystemExit(1)

    console.print(f"Running agent on [bold]{name}[/bold] ({function.source_file})")
    result = run_function(function, config, engine, warm_start=warm_start)

    if result.matched:
        console.print(f"[green]MATCHED![/green] in {result.elapsed_seconds:.1f}s")
    else:
        console.print(
            f"[yellow]{result.termination_reason}[/yellow] "
            f"best={result.best_match_percent:.1f}% "
            f"iterations={result.iterations} tokens={result.total_tokens:,}"
        )
    if result.error:
        console.print(f"[red]Error: {result.error}[/red]")


@main.command("run-file")
@click.argument("source_file")
@click.option("--headless", is_flag=True, default=False, help="Use Claude Code headless mode (Max subscription)")
@click.option("--codex-headless", is_flag=True, default=False, help="Use Codex CLI headless mode (ChatGPT/Codex subscription)")
@click.option("--isolated-worker", is_flag=True, default=False, help="Run Codex headless inside an isolated worker worktree/container")
@click.pass_context
def run_file_cmd(
    ctx: click.Context,
    source_file: str,
    headless: bool,
    codex_headless: bool,
    isolated_worker: bool,
) -> None:
    """Run agent on all unmatched functions in a source file."""
    config, engine = _load(ctx)

    _enable_headless_provider(
        config,
        claude_headless=headless,
        codex_headless=codex_headless,
        isolated_worker=isolated_worker,
    )

    from decomp_agent.orchestrator.runner import run_file

    console.print(f"Running file-mode agent on [bold]{source_file}[/bold]")
    result = run_file(source_file, config, engine=engine)

    if result.newly_matched:
        console.print(f"[green]Matched {len(result.newly_matched)} function(s):[/green]")
        for name in result.newly_matched:
            console.print(f"  [green]✓[/green] {name}")
    else:
        console.print(f"[yellow]No new matches[/yellow] ({result.termination_reason})")

    # Show improvements
    for name, (before, after) in result.function_deltas.items():
        if after > before and name not in result.newly_matched:
            console.print(f"  [cyan]↑[/cyan] {name}: {before:.1f}% → {after:.1f}%")

    console.print(
        f"\nElapsed: {result.elapsed_seconds:.1f}s  "
        f"Tokens: {result.total_tokens:,}"
    )
    if result.error:
        console.print(f"[red]Error: {result.error}[/red]")


@main.command()
@click.option("--limit", default=None, type=int, help="Max functions to attempt")
@click.option("--max-size", default=None, type=int, help="Max function size in bytes")
@click.option("--budget", default=None, type=float, help="Max dollar budget for the batch")
@click.option("--workers", default=None, type=int, help="Number of parallel workers")
@click.option("--strategy", default=None, type=click.Choice(["smallest_first", "best_match_first"]), help="Candidate selection strategy")
@click.option("--library", default=None, type=str, help="Filter to specific library (e.g. 'lb', 'ft')")
@click.option("--min-match", default=None, type=float, help="Minimum current match percentage")
@click.option("--max-match", default=None, type=float, help="Maximum current match percentage")
@click.option("--yes", "auto_approve", is_flag=True, default=False, help="Skip confirmation prompt")
@click.option("--log-file", default=None, type=click.Path(path_type=Path), help="Path for JSON-lines log file")
@click.option("--headless", is_flag=True, default=False, help="Use Claude Code headless mode (Max subscription)")
@click.option("--codex-headless", is_flag=True, default=False, help="Use Codex CLI headless mode (ChatGPT/Codex subscription)")
@click.option("--isolated-worker", is_flag=True, default=False, help="Run Codex headless inside an isolated worker worktree/container")
@click.option("--warm-start", is_flag=True, default=False, help="Seed with best prior attempt code")
@click.option("--file-mode", is_flag=True, default=False, help="Run in file-mode: one session per source file")
@click.pass_context
def batch(
    ctx: click.Context,
    limit: int | None,
    max_size: int | None,
    budget: float | None,
    workers: int | None,
    strategy: str | None,
    library: str | None,
    min_match: float | None,
    max_match: float | None,
    auto_approve: bool,
    log_file: Path | None,
    headless: bool,
    codex_headless: bool,
    isolated_worker: bool,
    warm_start: bool,
    file_mode: bool,
) -> None:
    """Run agent on candidates in batch mode."""
    if log_file is not None:
        from decomp_agent.logging import configure_logging

        configure_logging(level=ctx.obj.get("log_level") or "INFO", log_file=log_file)

    config, engine = _load(ctx)

    _enable_headless_provider(
        config,
        claude_headless=headless,
        codex_headless=codex_headless,
        isolated_worker=isolated_worker,
    )

    from decomp_agent.orchestrator.batch import run_batch

    effective_limit = limit if limit is not None else config.orchestration.batch_size
    effective_max_size = max_size if max_size is not None else config.orchestration.max_function_size
    effective_workers = workers if workers is not None else config.orchestration.default_workers
    effective_budget = budget if budget is not None else config.orchestration.default_budget
    effective_strategy = strategy or "smallest_first"

    if file_mode:
        mode_label = "file-mode"
    elif config.codex_code.enabled:
        mode_label = "codex-isolated" if config.codex_code.isolated_worker_enabled else "codex-headless"
    elif config.claude_code.enabled:
        mode_label = "claude-headless"
    else:
        mode_label = "function-mode"
    console.print(
        f"Starting batch run (limit={effective_limit}, max_size={effective_max_size}, "
        f"workers={effective_workers}, budget={effective_budget}, mode={mode_label})"
    )

    result = run_batch(
        config,
        engine,
        limit=effective_limit,
        max_size=effective_max_size,
        workers=effective_workers,
        budget=effective_budget,
        strategy=effective_strategy,
        library=library,
        min_match=min_match,
        max_match=max_match,
        auto_approve=auto_approve,
        warm_start=warm_start,
        file_mode=file_mode,
    )

    console.print(f"\n[bold]Batch complete:[/bold]")
    console.print(f"  Attempted: {result.attempted}")
    console.print(f"  Matched:   {result.matched}")
    console.print(f"  Failed:    {result.failed}")
    console.print(f"  Tokens:    {result.total_tokens:,}")
    console.print(f"  Cost:      ${result.total_cost:.4f}")
    console.print(f"  Elapsed:   {result.elapsed:.1f}s")


@main.group("campaign")
def campaign_group() -> None:
    """Manage long-running file campaigns."""


@campaign_group.command("start")
@click.argument("source_file")
@click.option(
    "--orchestrator-provider",
    type=click.Choice(["claude", "codex"], case_sensitive=False),
    default=None,
    help="Provider for the orchestrator agent",
)
@click.option(
    "--worker-provider-policy",
    type=click.Choice(["claude", "codex", "mixed"], case_sensitive=False),
    default=None,
    help="Provider policy for worker agents",
)
@click.option("--max-active-workers", type=int, default=None, help="Max concurrent workers")
@click.option("--timeout-hours", type=int, default=None, help="Campaign wall-clock timeout")
@click.option("--allow-shared-fix-workers", is_flag=True, default=False, help="Allow workers to make broader shared-file fixes")
@click.option("--allow-temporary-unmatched-regressions", is_flag=True, default=False, help="Allow temporary regressions in unmatched functions when net file progress improves")
@click.pass_context
def campaign_start(
    ctx: click.Context,
    source_file: str,
    orchestrator_provider: str | None,
    worker_provider_policy: str | None,
    max_active_workers: int | None,
    timeout_hours: int | None,
    allow_shared_fix_workers: bool,
    allow_temporary_unmatched_regressions: bool,
) -> None:
    """Create a new campaign record for one source file."""
    config, engine = _load(ctx)
    src_path = config.melee.resolve_source_path(source_file)
    if not src_path.exists():
        console.print(
            f"[red]Source file '{source_file}' not found at {src_path}.[/red]"
        )
        raise SystemExit(1)

    from decomp_agent.orchestrator.campaign import start_campaign

    with Session(engine) as session:
        campaign = start_campaign(
            session,
            config,
            source_file=source_file,
            orchestrator_provider=_provider_choice(orchestrator_provider),
            worker_provider_policy=_provider_choice(
                worker_provider_policy,
                allow_mixed=True,
            ),
            max_active_workers=max_active_workers,
            timeout_hours=timeout_hours,
            allow_shared_fix_workers=allow_shared_fix_workers or None,
            allow_temporary_unmatched_regressions=(
                allow_temporary_unmatched_regressions or None
            ),
        )
        task_count = len(
            session.exec(
                select(CampaignTask).where(CampaignTask.campaign_id == campaign.id)
            ).all()
        )

    console.print(
        f"Started campaign [bold]#{campaign.id}[/bold] for {campaign.source_file}"
    )
    console.print(f"  Orchestrator: {campaign.orchestrator_provider}")
    console.print(f"  Workers:      {campaign.worker_provider_policy}")
    console.print(f"  Max workers:  {campaign.max_active_workers}")
    console.print(f"  Timeout:      {campaign.timeout_hours}h")
    console.print(f"  Status:       {campaign.status}")
    console.print(f"  Tasks:        {task_count}")
    console.print(f"  Artifacts:    {campaign.artifact_dir}")


@campaign_group.command("show")
@click.argument("campaign_id", type=int)
@click.pass_context
def campaign_show(ctx: click.Context, campaign_id: int) -> None:
    """Show one campaign and its queued tasks."""
    _config, engine = _load(ctx)

    with Session(engine) as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            console.print(f"[red]Campaign #{campaign_id} not found.[/red]")
            raise SystemExit(1)
        tasks = session.exec(
            select(CampaignTask)
            .where(CampaignTask.campaign_id == campaign_id)
            .order_by(CampaignTask.priority.desc(), CampaignTask.id.asc())  # type: ignore[arg-type]
        ).all()

    console.print(f"Campaign [bold]#{campaign.id}[/bold]")
    console.print(f"  Source file:   {campaign.source_file}")
    console.print(f"  Status:        {campaign.status}")
    console.print(f"  Orchestrator:  {campaign.orchestrator_provider}")
    console.print(f"  Workers:       {campaign.worker_provider_policy}")
    console.print(f"  Max workers:   {campaign.max_active_workers}")
    console.print(f"  Timeout:       {campaign.timeout_hours}h")
    console.print(f"  Artifact dir:  {campaign.artifact_dir}")
    console.print(f"  Staging repo:  {campaign.staging_worktree_path}")

    table = Table(title="Campaign Tasks")
    table.add_column("ID", justify="right")
    table.add_column("Status")
    table.add_column("Scope")
    table.add_column("Provider")
    table.add_column("Priority", justify="right")
    table.add_column("Function")

    for task in tasks[:25]:
        table.add_row(
            str(task.id),
            task.status,
            task.scope,
            task.provider or "unassigned",
            str(task.priority),
            task.function_name or "(file task)",
        )
    console.print(table)


@campaign_group.command("run-once")
@click.argument("campaign_id", type=int)
@click.pass_context
def campaign_run_once(ctx: click.Context, campaign_id: int) -> None:
    """Run one queued campaign task through the existing provider pipeline."""
    config, engine = _load(ctx)

    from decomp_agent.orchestrator.campaign import run_campaign_task_once

    campaign, task, result = run_campaign_task_once(
        engine,
        config,
        campaign_id=campaign_id,
    )

    if task is None:
        console.print(f"[yellow]Campaign #{campaign.id} has no pending tasks.[/yellow]")
        return

    console.print(
        f"Ran campaign task [bold]#{task.id}[/bold] "
        f"({task.function_name or task.scope}) via "
        f"{task.provider or campaign.worker_provider_policy}"
    )
    console.print(f"  Status:       {task.status}")
    console.print(f"  Reason:       {task.termination_reason or '(none)'}")
    console.print(f"  Best match:   {task.best_match_pct:.1f}%")
    if result and result.session_id:
        console.print(f"  Session:      {result.session_id}")
    if task.artifact_dir:
        console.print(f"  Artifacts:    {task.artifact_dir}")


@campaign_group.command("run")
@click.argument("campaign_id", type=int)
@click.option("--max-tasks", type=int, default=None, help="Stop after running this many tasks")
@click.pass_context
def campaign_run(ctx: click.Context, campaign_id: int, max_tasks: int | None) -> None:
    """Run campaign tasks until the queue is empty, timeout hits, or a limit is reached."""
    config, engine = _load(ctx)

    from decomp_agent.orchestrator.campaign import run_campaign_loop

    campaign, summary = run_campaign_loop(
        engine,
        config,
        campaign_id=campaign_id,
        max_tasks=max_tasks,
    )

    console.print(f"Campaign [bold]#{campaign.id}[/bold] run summary")
    console.print(f"  Status:         {campaign.status}")
    console.print(f"  Tasks run:      {summary.tasks_run}")
    console.print(f"  Completed:      {summary.completed_tasks}")
    console.print(f"  Failed:         {summary.failed_tasks}")
    console.print(f"  Pending:        {summary.pending_tasks}")
    console.print(f"  Timed out:      {'yes' if summary.timed_out else 'no'}")
    if summary.stopped_by_limit:
        console.print("  Stop reason:    max task limit reached")


@campaign_group.command("orchestrate-once")
@click.argument("campaign_id", type=int)
@click.pass_context
def campaign_orchestrate_once(ctx: click.Context, campaign_id: int) -> None:
    """Run one orchestrator session for a campaign using its configured provider."""
    config, engine = _load(ctx)

    from decomp_agent.orchestrator.campaign_orchestrator import (
        run_campaign_orchestrator_once,
    )

    campaign, result = run_campaign_orchestrator_once(
        engine,
        config,
        campaign_id=campaign_id,
    )

    console.print(
        f"Ran orchestrator for campaign [bold]#{campaign.id}[/bold] "
        f"via {campaign.orchestrator_provider}"
    )
    console.print(f"  Session:       {result.session_id or '(none)'}")
    console.print(f"  Reason:        {result.termination_reason or '(none)'}")
    console.print(f"  Iterations:    {result.iterations}")
    console.print(f"  Tokens:        {result.total_tokens:,}")
    console.print(f"  Elapsed:       {result.elapsed_seconds:.1f}s")
    if result.error:
        console.print(f"  Error:         {result.error}")


@campaign_group.command("orchestrate")
@click.argument("campaign_id", type=int)
@click.option("--max-sessions", type=int, default=None, help="Stop after this many orchestrator sessions")
@click.pass_context
def campaign_orchestrate(
    ctx: click.Context,
    campaign_id: int,
    max_sessions: int | None,
) -> None:
    """Run orchestrator sessions until work is dispatched, timeout hits, or a limit is reached."""
    config, engine = _load(ctx)

    from decomp_agent.orchestrator.campaign_orchestrator import (
        run_campaign_orchestrator_loop,
    )

    campaign, summary = run_campaign_orchestrator_loop(
        engine,
        config,
        campaign_id=campaign_id,
        max_sessions=max_sessions,
    )

    console.print(f"Campaign [bold]#{campaign.id}[/bold] orchestrator summary")
    console.print(f"  Provider:      {campaign.orchestrator_provider}")
    console.print(f"  Sessions:      {summary.sessions_run}")
    console.print(f"  Completed:     {summary.completed_tasks}")
    console.print(f"  Failed:        {summary.failed_tasks}")
    console.print(f"  Running:       {summary.running_tasks}")
    console.print(f"  Pending:       {summary.pending_tasks}")
    console.print(f"  Timed out:     {'yes' if summary.timed_out else 'no'}")
    if summary.stopped_by_limit:
        console.print("  Stop reason:   max session limit reached")


@campaign_group.command("supervise")
@click.argument("campaign_id", type=int)
@click.option("--max-cycles", type=int, default=None, help="Stop after this many supervisor cycles")
@click.option("--max-tasks-per-cycle", type=int, default=None, help="Max worker tasks to dispatch per supervisor cycle")
@click.pass_context
def campaign_supervise(
    ctx: click.Context,
    campaign_id: int,
    max_cycles: int | None,
    max_tasks_per_cycle: int | None,
) -> None:
    """Alternate orchestrator planning and worker execution for a campaign."""
    config, engine = _load(ctx)

    from decomp_agent.orchestrator.campaign import run_campaign_supervisor_loop

    campaign, summary = run_campaign_supervisor_loop(
        engine,
        config,
        campaign_id=campaign_id,
        max_cycles=max_cycles,
        max_tasks_per_cycle=max_tasks_per_cycle,
    )

    console.print(f"Campaign [bold]#{campaign.id}[/bold] supervisor summary")
    console.print(f"  Status:          {campaign.status}")
    console.print(f"  Cycles:          {summary.cycles_run}")
    console.print(f"  Orchestrator:    {summary.orchestrator_sessions}")
    console.print(f"  Tasks run:       {summary.tasks_run}")
    console.print(f"  Completed:       {summary.completed_tasks}")
    console.print(f"  Failed:          {summary.failed_tasks}")
    console.print(f"  Running:         {summary.running_tasks}")
    console.print(f"  Pending:         {summary.pending_tasks}")
    console.print(f"  Timed out:       {'yes' if summary.timed_out else 'no'}")
    console.print(f"  Stop reason:     {summary.stop_reason}")
    console.print(f"  No progress:     {summary.no_progress_cycles}")
    if summary.summary_path:
        console.print(f"  Summary file:    {summary.summary_path}")


@campaign_group.command("list")
@click.pass_context
def campaign_list(ctx: click.Context) -> None:
    """List existing campaigns."""
    _config, engine = _load(ctx)

    with Session(engine) as session:
        campaigns = session.exec(
            select(Campaign).order_by(Campaign.id.desc())  # type: ignore[arg-type]
        ).all()

    if not campaigns:
        console.print("[yellow]No campaigns found.[/yellow]")
        return

    table = Table(title="Campaigns")
    table.add_column("ID", justify="right")
    table.add_column("Source File")
    table.add_column("Status")
    table.add_column("Orchestrator")
    table.add_column("Workers")
    table.add_column("Max Workers", justify="right")

    for campaign in campaigns:
        table.add_row(
            str(campaign.id),
            campaign.source_file,
            campaign.status,
            campaign.orchestrator_provider,
            campaign.worker_provider_policy,
            str(campaign.max_active_workers),
        )
    console.print(table)


@main.command()
@click.option("--host", default="127.0.0.1", help="Bind address")
@click.option("--port", default=8000, type=int, help="Bind port")
@click.pass_context
def serve(ctx: click.Context, host: str, port: int) -> None:
    """Start the web UI server."""
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Web dependencies not installed. Run:[/red]\n"
            "  pip install 'decomp-agent[web]'"
        )
        raise SystemExit(1)

    from decomp_agent.web.app import create_app

    config_path = ctx.obj.get("config_path")
    app = create_app(config_path)

    console.print(f"Starting decomp-agent web UI at [bold]http://{host}:{port}[/bold]")
    uvicorn.run(app, host=host, port=port, log_level="info")


@main.command()
@click.argument("function_name")
@click.argument("source_file")
@click.option("--timeout", default=1800, type=int, help="Max seconds to run (default 1800)")
@click.option("-j", "--workers", default=8, type=int, help="Parallel permuter workers (default 8)")
@click.option("--apply", is_flag=True, default=False, help="Apply best result to source file")
@click.pass_context
def permuter(
    ctx: click.Context,
    function_name: str,
    source_file: str,
    timeout: int,
    workers: int,
    apply: bool,
) -> None:
    """Run decomp-permuter on a function to search for matching permutations."""
    config, _engine = _load(ctx)

    from decomp_agent.tools.permuter import run_permuter

    console.print(
        f"Running permuter on [bold]{function_name}[/bold] in {source_file} "
        f"(timeout={timeout}s, workers={workers})"
    )

    result = run_permuter(
        function_name, source_file, config,
        timeout=timeout, workers=workers,
    )

    if result.error:
        console.print(f"[red]Error: {result.error}[/red]")
    if result.best_score is not None:
        console.print(f"Best score: [bold]{result.best_score}[/bold]")
    console.print(f"Iterations: {result.iterations}")

    if result.success:
        console.print("[green]Perfect match found![/green]")
    elif result.improved:
        console.print(f"[cyan]Improved code found (score {result.best_score})[/cyan]")

    if result.best_code and apply:
        from decomp_agent.tools.source import (
            read_source_file,
            replace_function,
            write_source_file,
        )

        src_path = config.melee.resolve_source_path(source_file)
        source = read_source_file(src_path)

        # Extract only the target function from the permuter's preprocessed output
        from decomp_agent.tools.source import get_function_source

        new_func = get_function_source(result.best_code, function_name)
        if new_func is None:
            console.print("[red]Could not extract function from permuter output[/red]")
            return

        updated = replace_function(source, function_name, new_func)
        if updated is None:
            console.print(f"[red]Could not find {function_name} in source file[/red]")
            return

        write_source_file(src_path, updated)
        console.print(f"[green]Applied best code to {source_file}[/green]")

        # Verify by compiling
        from decomp_agent.tools.build import check_match

        match_result = check_match(source_file, config)
        if match_result.success:
            func = match_result.get_function(function_name)
            if func:
                status_str = "MATCH" if func.is_matched else f"{func.fuzzy_match_percent:.1f}%"
                console.print(f"  {function_name}: {status_str}")
        else:
            console.print(f"[red]Compilation failed after apply: {match_result.error}[/red]")
            # Revert
            write_source_file(src_path, source)
            console.print("[yellow]Reverted to previous code[/yellow]")
    elif result.best_code:
        console.print("\nBest code (use --apply to write it):")
        console.print(result.best_code[:2000])


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show progress summary."""
    config, engine = _load(ctx)

    with Session(engine) as session:
        total = session.exec(select(func.count(Function.id))).one()
        if total == 0:
            console.print("[yellow]No functions in DB. Run 'init' first.[/yellow]")
            return

        # Counts by status
        status_counts: dict[str, int] = {}
        rows = session.exec(
            select(Function.status, func.count(Function.id)).group_by(Function.status)
        ).all()
        for status_val, count in rows:
            status_counts[status_val] = count

        from decomp_agent.models.db import Attempt, Run, get_total_cost, get_total_tokens

        total_tokens = get_total_tokens(session)
        total_cost = get_total_cost(session)
        total_attempts = session.exec(select(func.count(Attempt.id))).one()
        total_runs = session.exec(select(func.count(Run.id))).one()

    table = Table(title="Decompilation Progress")
    table.add_column("Status", style="bold")
    table.add_column("Count", justify="right")

    for s in ["pending", "in_progress", "matched", "failed", "skipped"]:
        count = status_counts.get(s, 0)
        style = {
            "matched": "green",
            "failed": "red",
            "in_progress": "cyan",
            "pending": "yellow",
            "skipped": "dim",
        }.get(s, "")
        table.add_row(s, f"{count:,}", style=style)

    table.add_row("TOTAL", f"{total:,}", style="bold")
    console.print(table)

    console.print(f"\nTotal runs:     {total_runs:,}")
    console.print(f"Total attempts: {total_attempts:,}")
    console.print(f"Total tokens:   {total_tokens:,}")
    console.print(f"Total cost:     ${total_cost:.4f}")
