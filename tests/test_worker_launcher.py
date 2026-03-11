from __future__ import annotations

import subprocess
from pathlib import Path

from decomp_agent.orchestrator.worker_launcher import (
    build_worker_container_run_args,
    cleanup_worker_spec,
    create_worker_spec,
    render_worker_container_config,
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
        args = build_worker_container_run_args(spec, config)
    finally:
        cleanup_worker_spec(spec)

    joined = " ".join(str(arg) for arg in args)
    assert args[:4] == ["docker", "run", "-d", "--rm"]
    assert "HTTP_PROXY=http://proxy:3128" in joined
    assert "HTTPS_PROXY=http://proxy:3128" in joined
    assert str(spec.melee_worktree.worktree_path) in joined
    assert str(spec.decomp_config_path) in joined
    assert "decomp-agent-worker:test" in args
