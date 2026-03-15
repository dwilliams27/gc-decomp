from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch

from sqlmodel import Session, select

from decomp_agent.agent.loop import AgentResult
from decomp_agent.models.db import (
    Campaign,
    CampaignEvent,
    CampaignTask,
    Function,
    get_engine,
    record_campaign_task_progress,
    seed_campaign_function_tasks,
    sync_from_report,
)
from decomp_agent.orchestrator.campaign import (
    _compute_rate_limit_cooldown,
    _should_reset_no_progress,
    append_campaign_function_memory,
    append_campaign_note,
    build_campaign_spec,
    create_campaign_worker_task,
    format_campaign_status,
    format_campaign_task_result,
    get_campaign_function_memory,
    get_campaign_notes,
    get_campaign_scratchpad,
    run_campaign_next_task_summary,
    run_campaign_loop,
    run_campaign_supervisor_loop,
    run_campaign_task_once,
    retry_campaign_task,
    start_campaign,
    write_campaign_scratchpad,
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


def test_seed_campaign_function_tasks_prioritizes_low_match_then_low_attempts(tmp_path):
    engine = get_engine(tmp_path / "seed-order.db")

    with Session(engine) as session:
        session.add_all(
            [
                Function(
                    name="low_match_fresh",
                    address=0x1000,
                    size=24,
                    source_file="melee/test/testfile.c",
                    library="melee",
                    initial_match_pct=5.0,
                    current_match_pct=5.0,
                    attempts=0,
                ),
                Function(
                    name="low_match_many_attempts",
                    address=0x1010,
                    size=16,
                    source_file="melee/test/testfile.c",
                    library="melee",
                    initial_match_pct=5.0,
                    current_match_pct=5.0,
                    attempts=4,
                ),
                Function(
                    name="high_match",
                    address=0x1020,
                    size=8,
                    source_file="melee/test/testfile.c",
                    library="melee",
                    initial_match_pct=95.0,
                    current_match_pct=95.0,
                    attempts=0,
                ),
            ]
        )
        session.commit()

        created = seed_campaign_function_tasks(
            session,
            campaign_id=1,
            source_file="melee/test/testfile.c",
        )
        tasks = session.exec(
            select(CampaignTask)
            .where(CampaignTask.campaign_id == 1)
            .order_by(CampaignTask.priority.desc(), CampaignTask.id.asc())
        ).all()

    assert created == 3
    assert [task.function_name for task in tasks] == [
        "low_match_fresh",
        "low_match_many_attempts",
        "high_match",
    ]


def test_start_campaign_clears_stale_artifacts_for_reused_id(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign.db")
    stale_note = config.campaign.root_dir / "campaign-1" / "artifacts" / "manager-notes.md"
    stale_note.parent.mkdir(parents=True, exist_ok=True)
    stale_note.write_text("stale\n", encoding="utf-8")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
        )

    assert campaign.id == 1
    assert not stale_note.exists()


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


def test_run_campaign_task_once_uses_warm_start_for_existing_code(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-warm-start.db")

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
    )
    captured: dict[str, object] = {}

    def fake_run_function(
        function,
        config,
        engine,
        *,
        worker_label="",
        warm_start=False,
        progress_callback=None,
    ):
        del function, config, engine, worker_label, progress_callback
        captured["warm_start"] = warm_start
        return fake_result

    with patch(
        "decomp_agent.orchestrator.runner.run_function",
        side_effect=fake_run_function,
    ):
        run_campaign_task_once(
            engine,
            config,
            campaign_id=campaign.id,  # type: ignore[arg-type]
        )

    assert captured["warm_start"] is True


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


def test_run_campaign_loop_uses_multiple_worker_slots(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.campaign.max_active_workers = 2
    engine = get_engine(tmp_path / "campaign-loop-parallel.db")

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
    state_lock = threading.Lock()
    active = 0
    max_seen = 0
    gate = threading.Event()

    def fake_run_function(*args, **kwargs):
        nonlocal active, max_seen
        del args, kwargs
        with state_lock:
            active += 1
            max_seen = max(max_seen, active)
            if active >= 2:
                gate.set()
        gate.wait(timeout=1.0)
        time.sleep(0.05)
        with state_lock:
            active -= 1
        return fake_result

    with patch("decomp_agent.orchestrator.runner.run_function", side_effect=fake_run_function):
        refreshed_campaign, summary = run_campaign_loop(
            engine,
            config,
            campaign_id=campaign.id,  # type: ignore[arg-type]
            max_tasks=2,
        )

    assert refreshed_campaign.status == "stopped"
    assert summary.tasks_run == 2
    assert max_seen == 2


def test_compute_rate_limit_cooldown_uses_fixed_claude_window(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)

    cooldown = _compute_rate_limit_cooldown(
        config,
        provider="claude",
        error="usage limit reached",
        retry_count=0,
        now=datetime(2026, 3, 13, 4, 12, tzinfo=timezone.utc),
    )

    assert cooldown == timedelta(hours=2, minutes=52)


def test_campaign_notes_are_persisted_in_artifacts(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-notes.db")

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

    path = append_campaign_note(
        engine,
        campaign.id,  # type: ignore[arg-type]
        "Tried foo. Suspect header mismatch. Next cycle should retry with tighter guidance.",
    )
    notes = get_campaign_notes(engine, campaign.id)  # type: ignore[arg-type]
    status = format_campaign_status(engine, config, campaign.id)  # type: ignore[arg-type]

    assert path.endswith("manager-notes.md")
    assert "Suspect header mismatch" in notes
    assert "Manager notes:" in status


def test_campaign_notes_normalize_escaped_newlines(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-notes-format.db")

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

    append_campaign_note(
        engine,
        campaign.id,  # type: ignore[arg-type]
        "Header\\n\\nBody line",
    )
    notes = get_campaign_notes(engine, campaign.id)  # type: ignore[arg-type]
    assert "Header\n\nBody line" in notes


def test_campaign_scratchpad_and_function_memory_persist(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-memory.db")

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

    scratchpad_path = write_campaign_scratchpad(
        engine,
        campaign.id,  # type: ignore[arg-type]
        "# Scratchpad\n\nTrack file-level strategy.",
    )
    memory_path = append_campaign_function_memory(
        engine,
        campaign.id,  # type: ignore[arg-type]
        "simple_add",
        "Tried variable reorder. Next: split expression.",
    )

    assert scratchpad_path.endswith("manager-scratchpad.md")
    assert memory_path.endswith("function-memory/simple_add.md")
    assert "Track file-level strategy." in get_campaign_scratchpad(engine, campaign.id)  # type: ignore[arg-type]
    assert "split expression" in get_campaign_function_memory(
        engine,
        campaign.id,  # type: ignore[arg-type]
        "simple_add",
    )


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


def test_run_campaign_task_once_defers_rate_limited_task(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-rate-limit.db")

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
        campaign_id = campaign.id

    fake_result = AgentResult(
        matched=False,
        best_match_percent=80.0,
        termination_reason="rate_limited",
        error="usage limit reached",
    )

    with patch("decomp_agent.orchestrator.runner.run_function", return_value=fake_result):
        _campaign, task, _result = run_campaign_task_once(
            engine,
            config,
            campaign_id=campaign_id,  # type: ignore[arg-type]
        )

    assert task is not None
    assert task.status == "pending"
    assert task.termination_reason == "rate_limited"
    assert task.rate_limit_count == 1
    assert task.next_eligible_at is not None

    with Session(engine) as session:
        refreshed_campaign = session.get(Campaign, campaign_id)
        assert refreshed_campaign is not None
        assert refreshed_campaign.claude_cooldown_until is not None


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

    status_text = format_campaign_status(engine, config, campaign_id)  # type: ignore[arg-type]
    result_text = format_campaign_task_result(
        engine,
        config,
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


def test_create_campaign_worker_task_normalizes_blank_provider_to_campaign_default(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    engine = get_engine(tmp_path / "campaign-provider-normalize.db")

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
        campaign_id = campaign.id

    task = create_campaign_worker_task(
        engine,
        campaign_id=campaign_id,  # type: ignore[arg-type]
        function_name="simple_add",
        provider="",
        instructions="Use the default provider",
        priority=50,
    )

    assert task.provider == "claude"


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


def test_format_campaign_status_includes_live_running_details(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.campaign.root_dir = tmp_path / "campaigns"
    config.claude_code.worker_root = tmp_path / "claude-workers"
    engine = get_engine(tmp_path / "campaign-live.db")

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
        task = session.exec(
            select(CampaignTask)
            .where(CampaignTask.campaign_id == campaign.id)
            .order_by(CampaignTask.id.asc())  # type: ignore[arg-type]
        ).first()
        assert task is not None
        task.status = "running"
        session.add(task)
        session.commit()
        record_campaign_task_progress(
            session,
            task,
            observed_match_pct=60.0,
            detail="compile_and_check: observed 60.0%",
        )
        campaign_id = campaign.id
        task_id = task.id

    status = format_campaign_status(engine, config, campaign_id)  # type: ignore[arg-type]
    assert "live best seen: 60.0%" in status

    task_text = format_campaign_task_result(
        engine,
        config,
        campaign_id,  # type: ignore[arg-type]
        task_id,  # type: ignore[arg-type]
    )
    assert "Live status: live best seen: 60.0%" in task_text


def test_live_status_uses_host_progress_not_transcripts(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.campaign.root_dir = tmp_path / "campaigns"
    config.claude_code.worker_root = tmp_path / "claude-workers"
    engine = get_engine(tmp_path / "campaign-live-false-positive.db")

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
        task = session.exec(
            select(CampaignTask)
            .where(CampaignTask.campaign_id == campaign.id)
            .order_by(CampaignTask.id.asc())  # type: ignore[arg-type]
        ).first()
        assert task is not None
        task.status = "running"
        session.add(task)
        session.commit()
        record_campaign_task_progress(
            session,
            task,
            observed_match_pct=60.0,
            detail="compile_and_check: observed 60.0%",
        )
        campaign_id = campaign.id
        task_id = task.id

    status = format_campaign_status(engine, config, campaign_id)  # type: ignore[arg-type]
    assert "live best seen: 60.0%" in status
    assert "100.0%" not in status

    task_text = format_campaign_task_result(
        engine,
        config,
        campaign_id,  # type: ignore[arg-type]
        task_id,  # type: ignore[arg-type]
    )
    assert "Live status: live best seen: 60.0%" in task_text
    assert "Live status: live best seen: 100.0%" not in task_text


def test_starting_baseline_progress_is_not_emitted_as_match_improved(tmp_path):
    engine = get_engine(tmp_path / "campaign-baseline-progress.db")

    with Session(engine) as session:
        task = CampaignTask(
            campaign_id=1,
            source_file="melee/test/testfile.c",
            function_name="target_fn",
            status="running",
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        record_campaign_task_progress(
            session,
            task,
            observed_match_pct=80.8,
            detail="starting baseline 80.8%",
            allow_improvement_event=False,
        )

        events = session.exec(
            select(CampaignEvent)
            .where(CampaignEvent.campaign_id == 1)
            .order_by(CampaignEvent.id.asc())
        ).all()
        session.refresh(task)

    assert task.live_best_match_pct == 80.8
    assert len(events) == 1
    assert events[0].event_type == "progress"


def test_complete_campaign_task_emits_worker_failed_for_agent_crash(tmp_path):
    engine = get_engine(tmp_path / "campaign-worker-failed.db")

    with Session(engine) as session:
        task = CampaignTask(
            campaign_id=1,
            source_file="melee/test/testfile.c",
            function_name="target_fn",
            status="running",
        )
        session.add(task)
        session.commit()
        session.refresh(task)

        from decomp_agent.models.db import complete_campaign_task

        complete_campaign_task(
            session,
            task,
            AgentResult(
                matched=False,
                best_match_percent=12.5,
                termination_reason="agent_crash",
                error="boom",
            ),
        )

        events = session.exec(
            select(CampaignEvent)
            .where(CampaignEvent.campaign_id == 1)
            .order_by(CampaignEvent.id.asc())
        ).all()
        session.refresh(task)

    assert task.status == "failed"
    assert events[-1].event_type == "worker_failed"


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

    summary = run_campaign_next_task_summary(
        engine,
        config,
        campaign_id=campaign.id,  # type: ignore[arg-type]
    )

    assert "Queued campaign task #" in summary
    assert "host supervisor will dispatch" in summary
    assert "Provider: codex" in summary


def test_run_campaign_supervisor_loop_stops_after_no_progress(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.campaign.max_no_progress_cycles = 2
    config.campaign.orchestrator_poll_seconds = 0
    config.campaign.manager_wake_cooldown_seconds = 0
    engine = get_engine(tmp_path / "campaign-no-progress.db")

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

    with (
        patch(
            "decomp_agent.orchestrator.campaign.requeue_running_campaign_tasks",
            return_value=0,
        ),
        patch(
            "decomp_agent.orchestrator.campaign_orchestrator.run_campaign_orchestrator_once",
            return_value=(campaign, AgentResult(termination_reason="model_stopped")),
        ),
        patch(
            "decomp_agent.orchestrator.campaign._claim_campaign_tasks",
            return_value=(campaign, []),
        ),
    ):
        refreshed_campaign, summary = run_campaign_supervisor_loop(
            engine,
            config,
            campaign_id=campaign_id,  # type: ignore[arg-type]
        )

    assert refreshed_campaign.status == "stopped"
    assert summary.stop_reason == "no_progress_limit"
    assert summary.no_progress_cycles == 2
    assert summary.summary_path.endswith("supervisor-summary.json")

    payload = json.loads(open(summary.summary_path, encoding="utf-8").read())
    assert payload["stop_reason"] == "no_progress_limit"
    assert payload["campaign_status"] == "stopped"


def test_run_campaign_supervisor_loop_does_not_stop_for_recent_running_activity(tmp_path):
    running_task = CampaignTask(campaign_id=1, source_file="melee/test/testfile.c", status="running")
    assert _should_reset_no_progress(tasks=[running_task], active_futures={}, new_events=[]) is True
    assert _should_reset_no_progress(tasks=[], active_futures={object(): 1}, new_events=[]) is True
    assert _should_reset_no_progress(tasks=[], active_futures={}, new_events=[object()]) is True
    assert _should_reset_no_progress(tasks=[], active_futures={}, new_events=[]) is False


def test_run_campaign_supervisor_loop_writes_summary_on_completion(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.campaign.orchestrator_poll_seconds = 0
    engine = get_engine(tmp_path / "campaign-summary.db")

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
        for task in session.exec(
            select(CampaignTask).where(CampaignTask.campaign_id == campaign.id)
        ).all():
            task.status = "completed"
            session.add(task)
        session.commit()

    refreshed_campaign, summary = run_campaign_supervisor_loop(
        engine,
        config,
        campaign_id=campaign_id,  # type: ignore[arg-type]
    )

    assert refreshed_campaign.status == "completed"
    assert summary.stop_reason == "queue_drained"
    assert summary.summary_path.endswith("supervisor-summary.json")


def test_run_campaign_supervisor_loop_runs_orchestrator_and_tasks(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.campaign.orchestrator_poll_seconds = 0
    config.campaign.manager_wake_cooldown_seconds = 0
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
            "decomp_agent.orchestrator.campaign_orchestrator.run_campaign_orchestrator_once"
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
        mocked_orchestrator.return_value = (
            campaign,
            AgentResult(termination_reason="model_stopped"),
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


def test_run_campaign_supervisor_loop_writes_cycle_artifacts_before_task_execution(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.campaign.orchestrator_poll_seconds = 0
    config.campaign.manager_wake_cooldown_seconds = 0
    engine = get_engine(tmp_path / "campaign-cycle-artifacts.db")

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
        artifact_dir = campaign.artifact_dir

    def _fake_run_claimed(*args, **kwargs):
        del args, kwargs
        notes_path = Path(artifact_dir) / "manager-notes.md"
        summary_path = Path(artifact_dir) / "supervisor-summary.json"
        assert notes_path.exists()
        assert summary_path.exists()
        assert "Host dispatched tasks:" in notes_path.read_text(encoding="utf-8")
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        assert payload["stop_reason"] == "dispatching_tasks"
        with Session(engine) as session:
            refreshed_campaign = session.get(Campaign, campaign_id)
            task = session.exec(
                select(CampaignTask)
                .where(CampaignTask.campaign_id == campaign_id)
                .order_by(CampaignTask.priority.desc(), CampaignTask.id.asc())
            ).first()
            assert refreshed_campaign is not None
            assert task is not None
        return (
            refreshed_campaign,
            task,
            AgentResult(
                matched=False,
                best_match_percent=91.0,
                termination_reason="model_stopped",
            ),
        )

    with (
        patch(
            "decomp_agent.orchestrator.campaign_orchestrator.run_campaign_orchestrator_once",
            return_value=(campaign, AgentResult(termination_reason="model_stopped")),
        ),
        patch(
            "decomp_agent.orchestrator.campaign._run_claimed_campaign_task",
            side_effect=_fake_run_claimed,
        ),
    ):
        refreshed_campaign, summary = run_campaign_supervisor_loop(
            engine,
            config,
            campaign_id=campaign_id,  # type: ignore[arg-type]
            max_cycles=1,
            max_tasks_per_cycle=1,
        )

    assert refreshed_campaign.status == "stopped"
    assert summary.tasks_run == 1
