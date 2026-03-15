from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from decomp_agent.config import Config, MeleeConfig
from decomp_agent.orchestrator.headless import (
    _reap_stale_claude_shared_lock,
    _resolve_claude_worker_budget,
    _run_claude_stream,
    run_headless,
)
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
        mcp_config_path=tmp_path / "worker-root" / "config" / "mcp.json",
        auth_seed_path=None,
    )

    matched = CompileResult(
        object_name="melee/test/testfile.c",
        success=True,
        functions=[FunctionMatch(name="target_fn", fuzzy_match_percent=100.0, size=8)],
    )
    exec_commands: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="container-id\n", stderr="")
        if cmd[:2] == ["docker", "stop"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected subprocess command: {cmd}")

    fake_proc = type(
        "FakeProc",
        (),
        {"returncode": 0, "stderr": None},
    )()

    with (
        patch("decomp_agent.orchestrator.headless.create_worker_spec", return_value=fake_spec),
        patch("decomp_agent.orchestrator.headless.write_worker_artifact_manifest"),
        patch("decomp_agent.orchestrator.headless.build_worker_container_run_args", return_value=["docker", "run"]),
        patch("decomp_agent.orchestrator.headless.wait_for_worker_container"),
        patch("decomp_agent.orchestrator.headless.prepare_worker_repo_in_container") as prepare_repo,
        patch("decomp_agent.orchestrator.headless.validate_worker_tools_in_container"),
        patch("decomp_agent.orchestrator.headless.export_worker_patch", return_value=tmp_path / "worker.patch"),
        patch("decomp_agent.orchestrator.headless.write_worker_result"),
        patch(
            "decomp_agent.orchestrator.headless.archive_worker_artifacts",
            return_value=fake_spec.output_dir,
        ),
        patch("decomp_agent.orchestrator.headless.cleanup_worker_spec"),
        patch(
            "decomp_agent.orchestrator.headless._run_claude_stream",
            side_effect=lambda cmd, **kwargs: (
                exec_commands.append(cmd) or fake_proc,
                {
                    "type": "result",
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                    "result": "confirmed MATCH",
                    "session_id": "claude-session",
                    "num_turns": 4,
                },
                "",
                100.0,
            ),
        ),
        patch("decomp_agent.tools.build.check_match", return_value=matched),
        patch("decomp_agent.orchestrator.headless._read_final_code"),
        patch("decomp_agent.orchestrator.headless.subprocess.run", side_effect=fake_run),
    ):
        result = run_headless("target_fn", "melee/test/testfile.c", config)

    assert result.matched is False
    assert result.termination_reason == "isolated_patch_ready"
    assert result.best_match_percent == 100.0
    assert result.patch_path.endswith("worker.patch")
    assert result.artifact_dir == str(fake_spec.output_dir / "output")
    assert str(fake_spec.mcp_config_path) in exec_commands[0][-1]
    prepare_repo.assert_called_once_with(fake_spec)


def test_reap_stale_claude_shared_lock_removes_dead_pid(tmp_path):
    lock_path = tmp_path / "claude.lock"
    lock_path.write_text("999999\n", encoding="utf-8")

    assert _reap_stale_claude_shared_lock(lock_path) is True
    assert not lock_path.exists()


def test_reap_stale_claude_shared_lock_keeps_live_pid(tmp_path):
    lock_path = tmp_path / "claude.lock"
    lock_path.write_text(str(os.getpid()), encoding="utf-8")

    assert _reap_stale_claude_shared_lock(lock_path) is False
    assert lock_path.exists()


def test_resolve_claude_worker_budget_uses_file_mode_settings(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "configure.py").write_text("print('stub')", encoding="utf-8")
    config = Config(melee=MeleeConfig(repo_path=repo))
    config.claude_code.max_turns = 50
    config.claude_code.timeout_seconds = 3600
    config.claude_code.file_mode_max_turns = 150
    config.claude_code.file_mode_timeout_seconds = 7200

    turns, timeout = _resolve_claude_worker_budget(
        config,
        file_mode=True,
        prior_best_code=None,
        prior_match_pct=0.0,
    )

    assert turns == 150
    assert timeout == 7200


def test_resolve_claude_worker_budget_uses_near_match_settings(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "configure.py").write_text("print('stub')", encoding="utf-8")
    config = Config(melee=MeleeConfig(repo_path=repo))
    config.claude_code.max_turns = 50
    config.claude_code.timeout_seconds = 3600
    config.claude_code.warm_start_turns = 80
    config.claude_code.near_match_turns = 150
    config.claude_code.warm_start_threshold_pct = 80.0
    config.claude_code.near_match_threshold_pct = 95.0
    config.claude_code.warm_start_timeout_seconds = 3600
    config.claude_code.near_match_timeout_seconds = 5400

    turns, timeout = _resolve_claude_worker_budget(
        config,
        file_mode=False,
        prior_best_code="int x = 0;",
        prior_match_pct=97.2,
    )

    assert turns == 150
    assert timeout == 5400


def test_isolated_claude_recovers_best_match_from_transcript(tmp_path):
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
        mcp_config_path=tmp_path / "worker-root" / "config" / "mcp.json",
        auth_seed_path=None,
    )
    transcript = (
        fake_spec.agent_home_dir
        / "projects"
        / "-"
        / "session.jsonl"
    )
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-03-15T00:00:00Z", "type": "assistant"}),
                json.dumps(
                    {
                        "timestamp": "2026-03-15T00:00:01Z",
                        "type": "user",
                        "toolUseResult": (
                            "Compilation successful.\n"
                            "  other_func: MATCH (size: 8)\n"
                            "  target_fn: 66.0% (97% structural — wrong instructions)\n"
                        ),
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    unmatched = CompileResult(
        object_name="melee/test/testfile.c",
        success=True,
        functions=[FunctionMatch(name="target_fn", fuzzy_match_percent=13.0, size=8)],
    )

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] == ["docker", "run"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="container-id\n", stderr="")
        if cmd[:2] == ["docker", "stop"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"Unexpected subprocess command: {cmd}")

    fake_proc = type(
        "FakeProc",
        (),
        {"returncode": 0, "stderr": None},
    )()

    with (
        patch("decomp_agent.orchestrator.headless.create_worker_spec", return_value=fake_spec),
        patch("decomp_agent.orchestrator.headless.write_worker_artifact_manifest"),
        patch("decomp_agent.orchestrator.headless.build_worker_container_run_args", return_value=["docker", "run"]),
        patch("decomp_agent.orchestrator.headless.wait_for_worker_container"),
        patch("decomp_agent.orchestrator.headless.prepare_worker_repo_in_container"),
        patch("decomp_agent.orchestrator.headless.validate_worker_tools_in_container"),
        patch("decomp_agent.orchestrator.headless.export_worker_patch", return_value=tmp_path / "worker.patch"),
        patch("decomp_agent.orchestrator.headless.write_worker_result"),
        patch(
            "decomp_agent.orchestrator.headless.archive_worker_artifacts",
            return_value=fake_spec.output_dir,
        ),
        patch("decomp_agent.orchestrator.headless.cleanup_worker_spec"),
        patch(
            "decomp_agent.orchestrator.headless._run_claude_stream",
            return_value=(
                fake_proc,
                {
                    "type": "result",
                    "usage": {"input_tokens": 10, "output_tokens": 20},
                    "result": "Stopped after iteration",
                    "session_id": "claude-session",
                    "num_turns": 4,
                },
                "",
                66.0,
            ),
        ),
        patch("decomp_agent.tools.build.check_match", return_value=unmatched),
        patch("decomp_agent.orchestrator.headless._read_final_code"),
        patch("decomp_agent.orchestrator.headless.subprocess.run", side_effect=fake_run),
    ):
        result = run_headless("target_fn", "melee/test/testfile.c", config)

    assert result.best_match_percent == 66.0


def test_run_claude_stream_reports_progress_from_user_tool_result():
    stdout = io.StringIO(
        "\n".join(
            [
                json.dumps({"type": "tool_use", "name": "mcp__decomp-tools__write_function"}),
                json.dumps(
                    {
                        "type": "user",
                        "toolUseResult": (
                            "Compilation successful.\n"
                            "  other_func: MATCH (size: 8)\n"
                            "  target_fn: 66.0% (97% structural — wrong instructions)\n"
                        ),
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "usage": {"input_tokens": 1, "output_tokens": 2},
                        "result": "done",
                        "session_id": "sess-1",
                        "num_turns": 2,
                    }
                ),
            ]
        )
        + "\n"
    )

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = stdout
            self.stderr = io.StringIO("")
            self.returncode = 0

        def wait(self, timeout=None):
            del timeout
            self.returncode = 0
            return 0

    seen: list[tuple[float | None, str]] = []

    with patch("decomp_agent.orchestrator.headless.subprocess.Popen", return_value=FakeProc()):
        _proc, output, _stdout, best = _run_claude_stream(
            ["claude", "-p", "stub"],
            timeout=30,
            function_name="target_fn",
            progress_callback=lambda pct, detail: seen.append((pct, detail)),
        )

    assert output is not None
    assert best == 66.0
    assert any(pct == 66.0 for pct, _detail in seen)
