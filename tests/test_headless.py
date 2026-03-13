from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from decomp_agent.config import Config, MeleeConfig
from decomp_agent.orchestrator.headless import run_headless
from decomp_agent.orchestrator.worker_launcher import WorkerSpec
from decomp_agent.orchestrator.worktree import WorktreeSpec
from decomp_agent.tools.build import CompileResult, FunctionMatch


def test_isolated_claude_match_becomes_patch_ready(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "configure.py").write_text("print('stub')", encoding="utf-8")
    config = Config(melee=MeleeConfig(repo_path=repo))
    config.claude_code.enabled = True
    config.claude_code.isolated_worker_enabled = True

    fake_spec = WorkerSpec(
        provider="claude",
        worker_id="worker-1",
        function_name="target_fn",
        source_file="melee/test/testfile.c",
        root_dir=tmp_path / "worker-root",
        output_dir=tmp_path / "worker-root" / "output",
        agent_home_dir=tmp_path / "worker-root" / "agent-home",
        container_name="claude-worker-1",
        melee_worktree=WorktreeSpec(
            repo_root=repo,
            worktree_path=tmp_path / "worker-root" / "repo",
        ),
        decomp_config_path=tmp_path / "worker-root" / "config" / "container.toml",
        auth_seed_path=None,
    )

    matched = CompileResult(
        object_name="melee/test/testfile.c",
        success=True,
        functions=[FunctionMatch(name="target_fn", fuzzy_match_percent=100.0, size=8)],
    )

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="container-id\n", stderr="")
        if cmd[:2] == ["docker", "exec"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=(
                    '{"usage":{"input_tokens":10,"output_tokens":20},'
                    '"result":"confirmed MATCH","session_id":"claude-session",'
                    '"num_turns":4}'
                ),
                stderr="",
            )
        if cmd[:2] == ["docker", "stop"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected subprocess command: {cmd}")

    with (
        patch("decomp_agent.orchestrator.headless.create_worker_spec", return_value=fake_spec),
        patch("decomp_agent.orchestrator.headless.write_worker_artifact_manifest"),
        patch("decomp_agent.orchestrator.headless.build_worker_container_run_args", return_value=["docker", "run"]),
        patch("decomp_agent.orchestrator.headless.wait_for_worker_container"),
        patch("decomp_agent.orchestrator.headless.export_worker_patch", return_value=tmp_path / "worker.patch"),
        patch("decomp_agent.orchestrator.headless.write_worker_result"),
        patch("decomp_agent.tools.build.check_match", return_value=matched),
        patch("decomp_agent.orchestrator.headless._read_final_code"),
        patch("decomp_agent.orchestrator.headless.subprocess.run", side_effect=fake_run),
    ):
        result = run_headless("target_fn", "melee/test/testfile.c", config)

    assert result.matched is False
    assert result.termination_reason == "isolated_patch_ready"
    assert result.best_match_percent == 100.0
    assert result.patch_path.endswith("worker.patch")
    assert result.artifact_dir == str(fake_spec.output_dir)
