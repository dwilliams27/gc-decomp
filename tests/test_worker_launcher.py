from __future__ import annotations

import subprocess
from pathlib import Path

from decomp_agent.orchestrator.worker_launcher import (
    build_worker_container_run_args,
    cleanup_worker_spec,
    create_worker_spec,
    render_worker_container_config,
    wait_for_worker_container,
)
from tests.fixtures.fake_repo import create_fake_repo


def _init_git_repo(repo_path: Path) -> None:
    subprocess.run(["git", "init"], check=True, cwd=repo_path, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "add", "."], check=True, cwd=repo_path, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        check=True,
        cwd=repo_path,
        capture_output=True,
        text=True,
    )


def test_render_worker_container_config_uses_worktree_repo_path(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    rendered = render_worker_container_config(
        config,
        repo_path=repo_path / "worker-repo",
    )

    assert f'repo_path = "{repo_path / "worker-repo"}"' in rendered
    assert 'model = "gpt-5.2-codex"' in rendered
    assert "[docker]" in rendered
    assert "enabled = false" in rendered


def test_create_worker_spec_creates_worktree_and_metadata(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.codex_code.worker_root = tmp_path / "workers"
    config.codex_code.auth_file = tmp_path / "auth.json"
    config.codex_code.auth_file.write_text("{}", encoding="utf-8")

    spec = create_worker_spec(
        config,
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    try:
        assert spec.melee_worktree.worktree_path.exists()
        assert (spec.output_dir / "worker-spec.json").exists()
        assert spec.decomp_config_path.exists()
        assert spec.auth_seed_path == config.codex_code.auth_file
        copied_source = spec.melee_worktree.worktree_path / "src" / "melee" / "test" / "testfile.c"
        assert copied_source.exists()
    finally:
        cleanup_worker_spec(spec)

    assert not spec.root_dir.exists()


def test_build_worker_container_run_args_includes_mounts_and_env(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.codex_code.worker_root = tmp_path / "workers"
    config.codex_code.auth_file = tmp_path / "auth.json"
    config.codex_code.auth_file.write_text("{}", encoding="utf-8")
    config.codex_code.image = "decomp-agent-worker:test"
    config.codex_code.http_proxy = "http://proxy:3128"
    config.codex_code.https_proxy = "http://proxy:3128"

    spec = create_worker_spec(
        config,
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    try:
        import os
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "test-token"
        args = build_worker_container_run_args(spec, config)
    finally:
        import os
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        cleanup_worker_spec(spec)

    joined = " ".join(str(arg) for arg in args)
    assert args[:4] == ["docker", "run", "-d", "--rm"]
    assert "/app/worker-entrypoint.sh" in args
    assert "CLAUDE_CODE_OAUTH_TOKEN=test-token" in joined
    assert "HTTP_PROXY=http://proxy:3128" in joined
    assert "HTTPS_PROXY=http://proxy:3128" in joined
    assert str(spec.melee_worktree.worktree_path) in joined
    assert str(spec.decomp_config_path) in joined
    assert "decomp-agent-worker:test" in args


def test_wait_for_worker_container_polls_until_running(tmp_path, monkeypatch):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.codex_code.worker_root = tmp_path / "workers"

    spec = create_worker_spec(
        config,
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )

    calls = {"count": 0}

    def fake_run(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            return subprocess.CompletedProcess(args[0], 0, stdout="false\n", stderr="")
        return subprocess.CompletedProcess(args[0], 0, stdout="true\n", stderr="")

    try:
        monkeypatch.setattr(subprocess, "run", fake_run)
        wait_for_worker_container(spec, timeout_seconds=1.0)
        assert calls["count"] == 3
    finally:
        cleanup_worker_spec(spec)


def test_create_worker_spec_reuses_existing_worker_root(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.codex_code.worker_root = tmp_path / "workers"

    first = create_worker_spec(
        config,
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    stale_marker = first.root_dir / "stale.txt"
    stale_marker.write_text("stale", encoding="utf-8")

    second = create_worker_spec(
        config,
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    try:
        assert second.root_dir == first.root_dir
        assert second.melee_worktree.worktree_path.exists()
        assert not stale_marker.exists()
    finally:
        cleanup_worker_spec(second)
