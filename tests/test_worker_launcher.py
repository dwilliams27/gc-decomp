from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from decomp_agent.orchestrator.worker_launcher import (
    build_worker_container_run_args,
    cleanup_worker_spec,
    create_worker_spec,
    prepare_worker_repo_in_container,
    render_worker_container_config,
    render_worker_mcp_config,
    validate_worker_tools_in_container,
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


def test_render_worker_mcp_config_uses_worker_decomp_config(tmp_path):
    decomp_config_path = tmp_path / "worker" / "container.toml"
    rendered = render_worker_mcp_config(decomp_config_path=decomp_config_path)

    assert str(decomp_config_path) in rendered
    assert "decomp-tools" in rendered


def test_create_worker_spec_creates_worktree_and_metadata(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.codex_code.worker_root = tmp_path / "workers"
    config.codex_code.auth_file = tmp_path / "auth.json"
    config.codex_code.auth_file.write_text("{}", encoding="utf-8")

    spec = create_worker_spec(
        config,
        provider="codex",
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    try:
        assert spec.melee_worktree.worktree_path.exists()
        assert (spec.output_dir / "worker-spec.json").exists()
        assert spec.decomp_config_path.exists()
        assert spec.mcp_config_path is None
        assert spec.auth_seed_path == config.codex_code.auth_file
        copied_source = spec.melee_worktree.worktree_path / "src" / "melee" / "test" / "testfile.c"
        assert copied_source.exists()
    finally:
        cleanup_worker_spec(spec)

    assert not spec.root_dir.exists()


def test_build_worker_container_run_args_includes_mounts_and_env(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    (repo_path / "orig" / "GALE01" / "sys").mkdir(parents=True, exist_ok=True)
    (repo_path / "orig" / "GALE01" / "sys" / "main.dol").write_bytes(b"dol")
    config.codex_code.worker_root = tmp_path / "workers"
    config.codex_code.auth_file = tmp_path / "auth.json"
    config.codex_code.auth_file.write_text("{}", encoding="utf-8")
    config.codex_code.image = "decomp-agent-worker:test"
    config.codex_code.http_proxy = "http://proxy:3128"
    config.codex_code.https_proxy = "http://proxy:3128"

    spec = create_worker_spec(
        config,
        provider="codex",
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
    assert "PYTHONPATH=/workspace/gc-decomp/src" in joined
    assert str(spec.melee_worktree.worktree_path) in joined
    assert f"{repo_path / 'orig'}:{spec.melee_worktree.worktree_path / 'orig'}:ro" in joined
    assert str(spec.decomp_config_path) in joined
    assert "/workspace/gc-decomp" in joined
    assert "decomp-agent-worker:test" in args


def test_build_worker_container_run_args_reads_claude_token_from_repo_dotenv(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.claude_code.worker_root = tmp_path / "claude-workers"
    config.claude_code.image = "decomp-agent-worker:test"
    repo_dotenv = Path(__file__).parents[1] / ".env"
    original_dotenv = repo_dotenv.read_text(encoding="utf-8") if repo_dotenv.exists() else None
    repo_dotenv.write_text(
        "CLAUDE_CODE_OAUTH_TOKEN=dotenv-token\n",
        encoding="utf-8",
    )

    spec = create_worker_spec(
        config,
        provider="claude",
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    try:
        import os
        original = os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        args = build_worker_container_run_args(spec, config)
    finally:
        if original is not None:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = original
        cleanup_worker_spec(spec)
        if original_dotenv is None:
            repo_dotenv.unlink(missing_ok=True)
        else:
            repo_dotenv.write_text(original_dotenv, encoding="utf-8")

    assert "CLAUDE_CODE_OAUTH_TOKEN=dotenv-token" in " ".join(str(arg) for arg in args)


def test_wait_for_worker_container_polls_until_running(tmp_path, monkeypatch):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.codex_code.worker_root = tmp_path / "workers"

    spec = create_worker_spec(
        config,
        provider="codex",
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
        provider="codex",
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    stale_marker = first.root_dir / "stale.txt"
    stale_marker.write_text("stale", encoding="utf-8")

    second = create_worker_spec(
        config,
        provider="codex",
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    try:
        assert second.root_dir == first.root_dir
        assert second.melee_worktree.worktree_path.exists()
        assert not stale_marker.exists()
    finally:
        cleanup_worker_spec(second)


def test_create_worker_spec_recovers_missing_but_registered_worktree(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.claude_code.worker_root = tmp_path / "claude-workers"

    first = create_worker_spec(
        config,
        provider="claude",
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    registered_path = first.melee_worktree.worktree_path
    assert registered_path.exists()

    shutil.rmtree(registered_path, ignore_errors=True)
    assert not registered_path.exists()

    second = create_worker_spec(
        config,
        provider="claude",
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    try:
        assert second.melee_worktree.worktree_path.exists()
        listed = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            check=True,
            cwd=repo_path,
            capture_output=True,
            text=True,
        ).stdout
        assert str(second.melee_worktree.worktree_path) in listed
    finally:
        cleanup_worker_spec(second)


def test_build_worker_container_run_args_mounts_private_claude_home(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.claude_code.worker_root = tmp_path / "claude-workers"
    config.claude_code.image = "decomp-agent-worker:test"

    spec = create_worker_spec(
        config,
        provider="claude",
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    try:
        args = build_worker_container_run_args(spec, config)
        assert spec.mcp_config_path is not None
        assert spec.mcp_config_path.exists()
    finally:
        cleanup_worker_spec(spec)

    joined = " ".join(str(arg) for arg in args)
    assert spec.provider == "claude"
    assert spec.container_name.startswith("claude-worker-")
    assert f"{spec.agent_home_dir}:/home/decomp/.claude:rw" in joined
    assert f"{spec.mcp_config_path}:{spec.mcp_config_path}:ro" in joined
    assert "decomp-agent-worker:test" in joined


def test_prepare_worker_repo_in_container_regenerates_build_files(tmp_path, monkeypatch):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.claude_code.worker_root = tmp_path / "claude-workers"

    spec = create_worker_spec(
        config,
        provider="claude",
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )

    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    try:
        monkeypatch.setattr(subprocess, "run", fake_run)
        prepare_worker_repo_in_container(spec)
    finally:
        cleanup_worker_spec(spec)

    assert calls
    assert calls[0][:3] == ["docker", "exec", spec.container_name]
    joined = " ".join(calls[0])
    assert "rm -f build.ninja objdiff.json .ninja_log .ninja_deps" in joined
    assert "python configure.py" in joined


def test_validate_worker_tools_in_container_accepts_expected_m2c(tmp_path, monkeypatch):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.claude_code.worker_root = tmp_path / "claude-workers"

    spec = create_worker_spec(
        config,
        provider="claude",
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )

    try:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
        )
        validate_worker_tools_in_container(spec)
    finally:
        cleanup_worker_spec(spec)


def test_validate_worker_tools_in_container_rejects_missing_m2c_main(tmp_path, monkeypatch):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.claude_code.worker_root = tmp_path / "claude-workers"

    spec = create_worker_spec(
        config,
        provider="claude",
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )

    try:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0],
                1,
                "",
                "missing m2c.main",
            ),
        )
        try:
            validate_worker_tools_in_container(spec)
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "missing m2c.main" in str(exc)
    finally:
        cleanup_worker_spec(spec)
