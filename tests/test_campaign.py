from __future__ import annotations

from unittest.mock import patch

from sqlmodel import Session, select

from decomp_agent.agent.loop import AgentResult
from decomp_agent.models.db import Campaign, CampaignTask, get_engine, sync_from_report
from decomp_agent.orchestrator.campaign import (
    build_campaign_spec,
    create_campaign_worker_task,
    format_campaign_status,
    format_campaign_task_result,
    run_campaign_next_task_summary,
    run_campaign_loop,
    run_campaign_supervisor_loop,
    run_campaign_task_once,
    retry_campaign_task,
    start_campaign,
)
from tests.fixtures.fake_repo import create_fake_repo


def test_build_campaign_spec_uses_overrides(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)

    spec = build_campaign_spec(
        config,
        source_file="melee/test/testfile.c",
        orchestrator_provider="codex",
        worker_provider_policy="mixed",
        max_active_workers=6,
        timeout_hours=12,
        allow_shared_fix_workers=True,
        allow_temporary_unmatched_regressions=True,
    )

    assert spec.orchestrator_provider == "codex"
    assert spec.worker_provider_policy == "mixed"
    assert spec.max_active_workers == 6
    assert spec.timeout_hours == 12
    assert spec.allow_shared_fix_workers is True
    assert spec.allow_temporary_unmatched_regressions is True


def test_build_campaign_spec_rejects_invalid_provider(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)

    try:
        build_campaign_spec(
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="bad-provider",
        )
    except ValueError as exc:
        assert "Invalid orchestrator provider" in str(exc)
    else:
        raise AssertionError("Expected invalid orchestrator provider to raise")


def test_start_campaign_creates_db_record(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="claude",
            worker_provider_policy="mixed",
            max_active_workers=5,
            timeout_hours=10,
            allow_shared_fix_workers=True,
            allow_temporary_unmatched_regressions=False,
        )
        loaded = session.get(Campaign, campaign.id)
        tasks = session.exec(
            select(CampaignTask).where(CampaignTask.campaign_id == campaign.id)
        ).all()

    assert loaded is not None
    assert loaded.source_file == "melee/test/testfile.c"
    assert loaded.status == "pending"
    assert loaded.orchestrator_provider == "claude"
    assert loaded.worker_provider_policy == "mixed"
    assert loaded.max_active_workers == 5
    assert loaded.timeout_hours == 10
    assert loaded.allow_shared_fix_workers is True
    assert loaded.allow_temporary_unmatched_regressions is False
    assert loaded.artifact_dir.endswith(f"campaign-{campaign.id}/artifacts")
    assert loaded.staging_worktree_path.endswith(f"campaign-{campaign.id}/staging-repo")
    assert len(tasks) == 3
    assert {task.function_name for task in tasks} == {
        "simple_init",
        "simple_add",
        "simple_loop",
    }


def test_run_campaign_task_once_completes_one_task(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-runner.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="codex",
            worker_provider_policy="mixed",
        )

    fake_result = AgentResult(
        matched=False,
        best_match_percent=88.5,
        termination_reason="model_stopped",
        session_id="worker-session-1",
        artifact_dir="/tmp/campaign-artifacts/task-1",
        patch_path="/tmp/campaign-artifacts/task-1/worker.patch",
    )

    with patch(
        "decomp_agent.orchestrator.runner.run_function",
        return_value=fake_result,
    ):
        _campaign, task, result = run_campaign_task_once(
            engine,
            config,
            campaign_id=campaign.id,  # type: ignore[arg-type]
        )

    assert task is not None
    assert result is not None
    assert task.status == "completed"
    assert task.best_match_pct == 88.5
    assert task.termination_reason == "model_stopped"
    assert task.worker_session_id == "worker-session-1"
    assert task.artifact_dir == "/tmp/campaign-artifacts/task-1"
    assert task.patch_path.endswith("worker.patch")


def test_run_campaign_loop_respects_task_limit(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-loop-limit.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="codex",
            worker_provider_policy="codex",
        )

    fake_result = AgentResult(
        matched=False,
        best_match_percent=91.0,
        termination_reason="model_stopped",
        session_id="loop-worker",
    )

    with patch("decomp_agent.orchestrator.runner.run_function", return_value=fake_result):
        refreshed_campaign, summary = run_campaign_loop(
            engine,
            config,
            campaign_id=campaign.id,  # type: ignore[arg-type]
            max_tasks=2,
        )

    assert refreshed_campaign.status == "stopped"
    assert summary.tasks_run == 2
    assert summary.completed_tasks == 2
    assert summary.pending_tasks == 1
    assert summary.stopped_by_limit is True
    assert summary.timed_out is False


def test_run_campaign_loop_marks_completed_when_queue_drained(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-loop-complete.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="claude",
            worker_provider_policy="claude",
        )

    fake_result = AgentResult(
        matched=False,
        best_match_percent=87.0,
        termination_reason="model_stopped",
    )

    with patch("decomp_agent.orchestrator.runner.run_function", return_value=fake_result):
        refreshed_campaign, summary = run_campaign_loop(
            engine,
            config,
            campaign_id=campaign.id,  # type: ignore[arg-type]
        )

    assert refreshed_campaign.status == "completed"
    assert summary.tasks_run == 3
    assert summary.completed_tasks == 3
    assert summary.pending_tasks == 0
    assert summary.stopped_by_limit is False


def test_run_campaign_task_once_requeues_stale_running_task(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-requeue.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="codex",
            worker_provider_policy="codex",
        )
        campaign_id = campaign.id
        stuck_task = session.exec(
            select(CampaignTask)
            .where(CampaignTask.campaign_id == campaign.id)
            .order_by(CampaignTask.id.asc())  # type: ignore[arg-type]
        ).first()
        assert stuck_task is not None
        stuck_task_id = stuck_task.id
        stuck_task.status = "running"
        session.add(stuck_task)
        session.commit()

    fake_result = AgentResult(
        matched=False,
        best_match_percent=80.0,
        termination_reason="model_stopped",
    )

    with patch("decomp_agent.orchestrator.runner.run_function", return_value=fake_result):
        _campaign, task, _result = run_campaign_task_once(
            engine,
            config,
            campaign_id=campaign_id,  # type: ignore[arg-type]
        )

    assert task is not None
    assert task.id == stuck_task_id
    assert task.status == "completed"


def test_run_campaign_task_once_marks_failed_on_exception(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-fail.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="codex",
            worker_provider_policy="codex",
        )

    with patch(
        "decomp_agent.orchestrator.runner.run_function",
        side_effect=RuntimeError("worker exploded"),
    ):
        try:
            run_campaign_task_once(
                engine,
                config,
                campaign_id=campaign.id,  # type: ignore[arg-type]
            )
        except RuntimeError as exc:
            assert str(exc) == "worker exploded"
        else:
            raise AssertionError("Expected run_campaign_task_once to re-raise worker failure")

    with Session(engine) as session:
        failed_task = session.exec(
            select(CampaignTask)
            .where(CampaignTask.campaign_id == campaign.id)
            .order_by(CampaignTask.id.asc())  # type: ignore[arg-type]
        ).first()

    assert failed_task is not None
    assert failed_task.status == "failed"
    assert failed_task.termination_reason == "worker_error"
    assert failed_task.error == "worker exploded"


def test_campaign_status_and_result_formatters(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-format.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="codex",
            worker_provider_policy="codex",
        )
        task = session.exec(
            select(CampaignTask)
            .where(CampaignTask.campaign_id == campaign.id)
            .order_by(CampaignTask.id.asc())  # type: ignore[arg-type]
        ).first()
        assert task is not None
        task.status = "completed"
        task.best_match_pct = 93.5
        task.termination_reason = "model_stopped"
        task.instructions = "Try a header fix next."
        task.worker_session_id = "sess-123"
        task.artifact_dir = "/tmp/campaign-artifacts/task-1"
        task.patch_path = "/tmp/campaign-artifacts/task-1/worker.patch"
        session.add(task)
        session.commit()
        campaign_id = campaign.id
        task_id = task.id

    status_text = format_campaign_status(engine, campaign_id)  # type: ignore[arg-type]
    result_text = format_campaign_task_result(
        engine,
        campaign_id,  # type: ignore[arg-type]
        task_id,  # type: ignore[arg-type]
    )

    assert "Campaign #" in status_text
    assert "completed" in status_text
    assert "Campaign task #" in result_text
    assert "93.5%" in result_text
    assert "Try a header fix next." in result_text
    assert "sess-123" in result_text


def test_create_and_retry_campaign_worker_task(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-retry-task.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="codex",
            worker_provider_policy="mixed",
        )
        campaign_id = campaign.id

    first_task = create_campaign_worker_task(
        engine,
        campaign_id=campaign_id,  # type: ignore[arg-type]
        function_name="simple_add",
        provider="claude",
        instructions="Try changing variable ordering.",
        priority=77,
    )
    retry_task = retry_campaign_task(
        engine,
        campaign_id=campaign_id,  # type: ignore[arg-type]
        task_id=first_task.id,  # type: ignore[arg-type]
        instructions="Focus on stack temp lifetimes.",
        provider="codex",
    )

    assert first_task.function_name == "simple_add"
    assert first_task.provider == "claude"
    assert first_task.priority == 77
    assert retry_task.function_name == "simple_add"
    assert retry_task.provider == "codex"
    assert "Try changing variable ordering." in retry_task.instructions
    assert "Focus on stack temp lifetimes." in retry_task.instructions
    assert retry_task.priority == 78


def test_create_campaign_worker_task_deduplicates_pending_task(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-dedupe.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="codex",
            worker_provider_policy="mixed",
        )
        campaign_id = campaign.id

    first_task = create_campaign_worker_task(
        engine,
        campaign_id=campaign_id,  # type: ignore[arg-type]
        function_name="simple_add",
        provider="claude",
        instructions="First attempt",
        priority=77,
    )
    second_task = create_campaign_worker_task(
        engine,
        campaign_id=campaign_id,  # type: ignore[arg-type]
        function_name="simple_add",
        provider="claude",
        instructions="Second attempt should dedupe",
        priority=99,
    )

    assert second_task.id == first_task.id
    assert second_task.priority == 77


def test_retry_campaign_task_deduplicates_existing_pending_retry(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-retry-dedupe.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="codex",
            worker_provider_policy="mixed",
        )
        campaign_id = campaign.id

    first_task = create_campaign_worker_task(
        engine,
        campaign_id=campaign_id,  # type: ignore[arg-type]
        function_name="simple_add",
        provider="claude",
        instructions="First attempt",
        priority=77,
    )
    retry_one = retry_campaign_task(
        engine,
        campaign_id=campaign_id,  # type: ignore[arg-type]
        task_id=first_task.id,  # type: ignore[arg-type]
        provider="codex",
        instructions="Retry this with a new angle",
    )
    retry_two = retry_campaign_task(
        engine,
        campaign_id=campaign_id,  # type: ignore[arg-type]
        task_id=first_task.id,  # type: ignore[arg-type]
        provider="codex",
        instructions="Retry this again",
    )

    assert retry_two.id == retry_one.id


def test_run_campaign_next_task_summary_reports_result(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-next-task.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="codex",
            worker_provider_policy="codex",
        )

    fake_result = AgentResult(
        matched=False,
        best_match_percent=89.0,
        termination_reason="model_stopped",
        session_id="summary-session",
        artifact_dir="/tmp/worker-artifacts/task-1",
    )

    with patch("decomp_agent.orchestrator.runner.run_function", return_value=fake_result):
        summary = run_campaign_next_task_summary(
            engine,
            config,
            campaign_id=campaign.id,  # type: ignore[arg-type]
        )

    assert "Ran campaign task #" in summary
    assert "Best match: 89.0%" in summary
    assert "summary-session" in summary
    assert "/tmp/worker-artifacts/task-1" in summary


def test_run_campaign_supervisor_loop_runs_orchestrator_and_tasks(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-supervisor.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="codex",
            worker_provider_policy="codex",
        )

    with (
        patch(
            "decomp_agent.orchestrator.campaign_orchestrator.run_campaign_orchestrator_loop"
        ) as mocked_orchestrator,
        patch(
            "decomp_agent.orchestrator.runner.run_function",
            return_value=AgentResult(
                matched=False,
                best_match_percent=91.0,
                termination_reason="model_stopped",
            ),
        ),
    ):
        from decomp_agent.orchestrator.campaign_orchestrator import (
            CampaignOrchestratorSummary,
        )

        mocked_orchestrator.return_value = (
            campaign,
            CampaignOrchestratorSummary(
                campaign_id=campaign.id,  # type: ignore[arg-type]
                sessions_run=1,
                pending_tasks=3,
                running_tasks=0,
                completed_tasks=0,
                failed_tasks=0,
                timed_out=False,
                stopped_by_limit=True,
            ),
        )
        refreshed_campaign, summary = run_campaign_supervisor_loop(
            engine,
            config,
            campaign_id=campaign.id,  # type: ignore[arg-type]
            max_cycles=1,
            max_tasks_per_cycle=2,
        )

    assert refreshed_campaign.status == "stopped"
    assert summary.cycles_run == 1
    assert summary.orchestrator_sessions == 1
    assert summary.tasks_run == 2
    assert summary.completed_tasks == 2
    assert summary.pending_tasks == 1
    assert summary.stopped_by_limit is True
