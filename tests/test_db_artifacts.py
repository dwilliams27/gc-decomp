from __future__ import annotations

from sqlmodel import Session, select

from decomp_agent.agent.loop import AgentResult
from decomp_agent.models.db import Attempt, Function, get_engine, record_attempt


def test_record_attempt_persists_artifact_paths():
    engine = get_engine(":memory:")

    with Session(engine) as session:
        function = Function(
            name="test_func",
            address=0x80000000,
            size=16,
            source_file="melee/test/testfile.c",
            library="test",
            initial_match_pct=0.0,
            current_match_pct=0.0,
        )
        session.add(function)
        session.commit()
        session.refresh(function)

        result = AgentResult(
            best_match_percent=100.0,
            model="codex-code-headless",
            termination_reason="isolated_patch_ready",
            artifact_dir="/tmp/decomp-codex-workers/worker-1/output",
            patch_path="/tmp/decomp-codex-workers/worker-1/output/worker.patch",
        )
        record_attempt(session, function, result, cost=0.0)

        attempt = session.exec(select(Attempt)).one()

    assert attempt.artifact_dir.endswith("/output")
    assert attempt.patch_path.endswith("/worker.patch")
