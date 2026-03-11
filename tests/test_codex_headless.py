from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from decomp_agent.agent.loop import AgentResult
from decomp_agent.config import Config, MeleeConfig
from decomp_agent.orchestrator.codex_headless import (
    _parse_codex_result,
    _parse_jsonl_events,
    run_codex_headless,
)
from decomp_agent.orchestrator.worker_launcher import WorkerSpec
from decomp_agent.orchestrator.worktree import WorktreeSpec


def test_parse_jsonl_events_skips_non_json_lines():
    output = "\n".join([
        "thread 'main' panicked at somewhere",
        '{"type":"thread.started","thread_id":"abc123"}',
        '{"type":"turn.started"}',
        "plain text noise",
        '{"type":"turn.failed","error":{"message":"network down"}}',
    ])

    events = _parse_jsonl_events(output)

    assert [event["type"] for event in events] == [
        "thread.started",
        "turn.started",
        "turn.failed",
    ]


def test_parse_codex_result_extracts_session_and_failure():
    stdout = "\n".join([
        '{"type":"thread.started","thread_id":"thread-1"}',
        '{"type":"turn.started"}',
        '{"type":"turn.failed","error":{"message":"stream disconnected"}}',
    ])
    result = AgentResult(model="codex-code-headless")

    reason, detail = _parse_codex_result(stdout, "", result)

    assert result.session_id == "thread-1"
    assert result.iterations == 1
    assert reason == "api_error"
    assert detail == "stream disconnected"


def test_parse_codex_result_detects_rate_limit():
    stdout = "\n".join([
        '{"type":"thread.started","thread_id":"thread-2"}',
        '{"type":"turn.started"}',
        '{"type":"error","message":"429 rate limit reached"}',
    ])
    result = AgentResult(model="codex-code-headless")

    reason, detail = _parse_codex_result(stdout, "", result)

    assert reason == "rate_limited"
    assert "429 rate limit" in detail


def test_isolated_codex_match_becomes_patch_ready(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "configure.py").write_text("print('stub')", encoding="utf-8")
    config = Config(melee=MeleeConfig(repo_path=repo))
    config.codex_code.enabled = True
    config.codex_code.isolated_worker_enabled = True
    config.agent.model = "gpt-5.4"

    fake_spec = WorkerSpec(
        worker_id="worker-1",
        function_name="target_fn",
        source_file="melee/test/testfile.c",
        root_dir=tmp_path / "worker-root",
        output_dir=tmp_path / "worker-root" / "output",
        codex_home_dir=tmp_path / "worker-root" / "codex-home",
        container_name="codex-worker-1",
        melee_worktree=WorktreeSpec(repo_root=repo, worktree_path=tmp_path / "worker-root" / "repo"),
        decomp_config_path=tmp_path / "worker-root" / "config" / "container.toml",
        auth_seed_path=None,
    )

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_post_run_check(*, result, **kwargs):
        result.matched = True
        result.best_match_percent = 100.0
        result.termination_reason = "matched"

    with (
        patch(
            "decomp_agent.orchestrator.codex_headless._run_isolated_worker",
            return_value=(_Proc(), config, AgentResult(model="codex-code-headless"), fake_spec),
        ),
        patch(
            "decomp_agent.orchestrator.codex_headless._post_run_check",
            side_effect=fake_post_run_check,
        ),
        patch("decomp_agent.orchestrator.codex_headless._read_final_code"),
        patch("decomp_agent.orchestrator.codex_headless.write_worker_result"),
    ):
        result = run_codex_headless(
            "target_fn",
            "melee/test/testfile.c",
            config,
        )

    assert result.matched is False
    assert result.termination_reason == "isolated_patch_ready"
    assert result.best_match_percent == 100.0
    assert "Primary checkout was not modified" in (result.error or "")
