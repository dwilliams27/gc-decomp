from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner
from sqlmodel import Session

from decomp_agent.models.db import Campaign, CampaignTask, get_engine, sync_from_report
from decomp_agent.orchestrator.campaign import start_campaign
from tests.fixtures.fake_repo import create_fake_repo


def _seed_functions(config, engine) -> None:
    from decomp_agent.melee.functions import get_candidates, get_functions

    with Session(engine) as session:
        sync_from_report(session, get_candidates(get_functions(config)))


def test_campaign_launch_starts_processes_and_writes_manifest(tmp_path):
    from decomp_agent.cli import main

    _repo_path, config = create_fake_repo(tmp_path)
    config.orchestration.db_path = tmp_path / "campaign-launch.db"
    config.campaign.root_dir = tmp_path / "campaigns"
    engine = get_engine(config.orchestration.db_path)
    _seed_functions(config, engine)

    launched: list[tuple[list[str], str]] = []

    def fake_launch(command, *, log_path):
        launched.append((command, str(log_path)))
        return SimpleNamespace(pid=1000 + len(launched))

    with (
        patch("decomp_agent.cli.load_config", return_value=config),
        patch("decomp_agent.cli._launch_campaign_process", side_effect=fake_launch),
    ):
        runner = CliRunner()
        result = runner.invoke(main, ["campaign", "launch", "melee/test/testfile.c"])

    assert result.exit_code == 0
    assert "Launched campaign #1" in result.output
    assert len(launched) == 2
    assert launched[0][0][-3:] == ["campaign", "orchestrate", "1"]
    assert launched[1][0][-3:] == ["campaign", "run", "1"]

    manifest_path = config.campaign.root_dir / "campaign-1" / "artifacts" / "campaign-processes.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["orchestrator"]["pid"] == 1001
    assert payload["worker"]["pid"] == 1002


def test_campaign_stop_stops_processes_and_marks_campaign_stopped(tmp_path):
    from decomp_agent.cli import main

    _repo_path, config = create_fake_repo(tmp_path)
    config.orchestration.db_path = tmp_path / "campaign-stop.db"
    config.campaign.root_dir = tmp_path / "campaigns"
    engine = get_engine(config.orchestration.db_path)
    _seed_functions(config, engine)

    with Session(engine) as session:
        campaign = start_campaign(
            session,
            config,
            source_file="melee/test/testfile.c",
            orchestrator_provider="claude",
            worker_provider_policy="claude",
        )
        assert campaign.id == 1

    with Session(engine) as session:
        task = session.get(CampaignTask, 1)
        assert task is not None
        task.status = "running"
        session.add(task)
        session.commit()
        campaign = session.get(Campaign, 1)
        assert campaign is not None
        manifest_path = config.campaign.root_dir / "campaign-1" / "artifacts" / "campaign-processes.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "campaign_id": 1,
                    "orchestrator": {"pid": 111},
                    "worker": {"pid": 222},
                }
            ),
            encoding="utf-8",
        )

    with (
        patch("decomp_agent.cli.load_config", return_value=config),
        patch("decomp_agent.cli._stop_pid", return_value=True) as stop_pid,
        patch(
            "decomp_agent.cli._stop_campaign_worker_containers",
            return_value=["claude-worker-melee-test-testfile.c-simple_add"],
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(main, ["campaign", "stop", "1"])

    assert result.exit_code == 0
    assert "Stopped campaign #1" in result.output
    assert stop_pid.call_count == 2

    with Session(engine) as session:
        campaign = session.get(Campaign, 1)
        task = session.get(CampaignTask, 1)
        assert campaign is not None
        assert task is not None
        assert campaign.status == "stopped"
        assert task.status == "stopped"
