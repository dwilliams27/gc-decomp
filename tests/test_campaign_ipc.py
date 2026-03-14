from __future__ import annotations

import json
from pathlib import Path

from sqlmodel import Session

from decomp_agent.models.db import get_engine, sync_from_report
from decomp_agent.orchestrator.campaign import start_campaign
from decomp_agent.orchestrator.campaign_ipc import (
    ensure_campaign_ipc_dirs,
    process_pending_campaign_ipc_requests,
)
from tests.fixtures.fake_repo import create_fake_repo


def test_campaign_ipc_launch_worker_is_host_owned(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.campaign.root_dir = tmp_path / "campaigns"
    engine = get_engine(tmp_path / "campaign-ipc.db")

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

    ipc_root = tmp_path / "ipc"
    ensure_campaign_ipc_dirs(ipc_root)
    request_path = ipc_root / "requests" / "launch.json"
    request_path.write_text(
        json.dumps(
            {
                "request_id": "launch",
                "tool": "campaign_launch_worker",
                "payload": {
                    "campaign_id": campaign_id,
                    "function_name": "simple_add",
                    "provider": "claude",
                    "instructions": "Try the loop cleanup register allocation.",
                    "priority": 2000,
                    "scope": "function",
                },
            }
        ),
        encoding="utf-8",
    )

    processed = process_pending_campaign_ipc_requests(engine, config, root=ipc_root)
    assert processed == 1

    response_path = ipc_root / "responses" / "launch.json"
    response = json.loads(response_path.read_text(encoding="utf-8"))
    assert response["ok"] is True
    assert "Queued campaign task" in response["result"]

    with Session(engine) as session:
        from decomp_agent.models.db import list_campaign_tasks

        tasks = list_campaign_tasks(session, campaign_id)  # type: ignore[arg-type]
        assert any(task.function_name == "simple_add" for task in tasks)


def test_campaign_ipc_get_status_reads_host_db(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.campaign.root_dir = tmp_path / "campaigns"
    engine = get_engine(tmp_path / "campaign-ipc-status.db")

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

    ipc_root = tmp_path / "ipc"
    ensure_campaign_ipc_dirs(ipc_root)
    request_path = ipc_root / "requests" / "status.json"
    request_path.write_text(
        json.dumps(
            {
                "request_id": "status",
                "tool": "campaign_get_status",
                "payload": {"campaign_id": campaign_id},
            }
        ),
        encoding="utf-8",
    )

    processed = process_pending_campaign_ipc_requests(engine, config, root=ipc_root)
    assert processed == 1

    response_path = ipc_root / "responses" / "status.json"
    response = json.loads(response_path.read_text(encoding="utf-8"))
    assert response["ok"] is True
    assert f"Campaign #{campaign_id}" in response["result"]
