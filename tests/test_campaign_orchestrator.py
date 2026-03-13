from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from sqlmodel import Session, select

from decomp_agent.models.db import Campaign, CampaignTask, get_engine, sync_from_report
from decomp_agent.orchestrator.campaign import start_campaign
from decomp_agent.orchestrator.campaign_orchestrator import (
    _campaign_orchestrator_lock,
    run_campaign_orchestrator_loop,
    run_campaign_orchestrator_once,
)
from tests.fixtures.fake_repo import create_fake_repo


def test_run_campaign_orchestrator_once_stores_claude_session_id(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.campaign.root_dir = tmp_path / "campaigns"
    engine = get_engine(tmp_path / "campaign-orchestrator.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="claude",
            worker_provider_policy="mixed",
        )
        campaign_id = campaign.id

    payload = {
        "session_id": "claude-orch-1",
        "num_turns": 4,
        "result": "Queued a retry worker.",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 10,
        },
        "subtype": "success",
    }

    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["docker", "exec"],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    ):
        refreshed_campaign, result = run_campaign_orchestrator_once(
            engine,
            config,
            campaign_id=campaign_id,  # type: ignore[arg-type]
        )

    assert refreshed_campaign.orchestrator_session_id == "claude-orch-1"
    assert result.session_id == "claude-orch-1"
    assert result.total_tokens == 150
    assert result.iterations == 4


def test_run_campaign_orchestrator_loop_respects_session_limit(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.campaign.root_dir = tmp_path / "campaigns"
    engine = get_engine(tmp_path / "campaign-orchestrator-loop.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="claude",
            worker_provider_policy="mixed",
        )
        campaign_id = campaign.id

    with patch(
        "decomp_agent.orchestrator.campaign_orchestrator.run_campaign_orchestrator_once"
    ) as mocked:
        def side_effect(engine_arg, config_arg, *, campaign_id):
            with Session(engine_arg) as session:
                task = session.exec(
                    select(CampaignTask)
                    .where(
                        CampaignTask.campaign_id == campaign_id,
                        CampaignTask.status == "pending",
                    )
                    .order_by(CampaignTask.id.asc())  # type: ignore[arg-type]
                ).first()
                assert task is not None
                task.status = "completed"
                session.add(task)
                session.commit()
                campaign = session.get(Campaign, campaign_id)
                assert campaign is not None
            return campaign, None
        mocked.side_effect = side_effect
        campaign, summary = run_campaign_orchestrator_loop(
            engine,
            config,
            campaign_id=campaign_id,  # type: ignore[arg-type]
            max_sessions=2,
        )

    assert campaign is not None
    assert summary.sessions_run == 2
    assert summary.stopped_by_limit is True
    assert summary.completed_tasks == 2


def test_campaign_orchestrator_lock_rejects_overlap(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.campaign.root_dir = tmp_path / "campaigns"
    engine = get_engine(tmp_path / "campaign-orchestrator-lock.db")

    with Session(engine) as session:
        from decomp_agent.melee.functions import get_candidates, get_functions

        sync_from_report(session, get_candidates(get_functions(config)))
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="claude",
            worker_provider_policy="mixed",
        )
        campaign_id = campaign.id

    with Session(engine) as session:
        campaign = session.get(Campaign, campaign_id)
        assert campaign is not None
        with _campaign_orchestrator_lock(campaign):
            try:
                run_campaign_orchestrator_once(
                    engine,
                    config,
                    campaign_id=campaign_id,  # type: ignore[arg-type]
                )
            except RuntimeError as exc:
                assert "active orchestrator session" in str(exc)
            else:
                raise AssertionError("Expected overlapping orchestrator run to fail")
