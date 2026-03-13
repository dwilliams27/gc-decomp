"""Host-side preparation for isolated Codex worker containers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from decomp_agent.config import Config
from decomp_agent.orchestrator.worktree import (
    WorktreeSpec,
    create_git_worktree,
    remove_git_worktree,
    slugify_worker_token,
)


@dataclass
class WorkerSpec:
    worker_id: str
    function_name: str | None
    source_file: str
    root_dir: Path
    output_dir: Path
    codex_home_dir: Path
    container_name: str
    melee_worktree: WorktreeSpec
    decomp_config_path: Path
    auth_seed_path: Path | None


def render_worker_container_config(config: Config, *, repo_path: Path) -> str:
    """Render a worker-local decomp config for use inside the container."""
    lines = [
        "[melee]",
        f'repo_path = "{repo_path}"',
        f'version = "{config.melee.version}"',
        f'build_dir = "{config.melee.build_dir}"',
        "",
        "[agent]",
        f'model = "{config.agent.model}"',
        f"max_iterations = {config.agent.max_iterations}",
        f"max_tokens_per_attempt = {config.agent.max_tokens_per_attempt}",
        f"max_ctx_chars = {config.agent.max_ctx_chars}",
        "",
        "[docker]",
        "enabled = false",
        "",
        "[claude_code]",
        "enabled = false",
        "",
        "[codex_code]",
        "enabled = false",
        "",
        "[ghidra]",
        f"enabled = {'true' if config.ghidra.enabled else 'false'}",
        "",
    ]
    return "\n".join(lines)


def build_worker_container_run_args(
    spec: WorkerSpec,
    config: Config,
) -> list[str]:
    """Build the docker run command for a single isolated Codex worker."""
    args = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        spec.container_name,
        "--platform",
        "linux/amd64",
        "--entrypoint",
        "/app/worker-entrypoint.sh",
        "-e",
        f"DECOMP_CONFIG={spec.decomp_config_path}",
        "-e",
        f"CODEX_MODEL={config.agent.model}",
    ]

    if config.codex_code.http_proxy:
        args.extend(["-e", f"HTTP_PROXY={config.codex_code.http_proxy}"])
    if config.codex_code.https_proxy:
        args.extend(["-e", f"HTTPS_PROXY={config.codex_code.https_proxy}"])
    claude_oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if claude_oauth_token:
        args.extend(["-e", f"CLAUDE_CODE_OAUTH_TOKEN={claude_oauth_token}"])
    if spec.auth_seed_path is not None:
        args.extend([
            "-e",
            "CODEX_AUTH_SEED=/seed/codex/auth.json",
            "-v",
            f"{spec.auth_seed_path}:/seed/codex/auth.json:ro",
        ])

    args.extend([
        "-v",
        f"{spec.melee_worktree.worktree_path}:{spec.melee_worktree.worktree_path}:rw",
        "-v",
        f"{spec.codex_home_dir}:/home/decomp/.codex:rw",
        "-v",
        f"{spec.output_dir}:{spec.output_dir}:rw",
        "-v",
        f"{spec.decomp_config_path}:{spec.decomp_config_path}:ro",
        config.codex_code.image,
        "sleep",
        "infinity",
    ])
    return args


def wait_for_worker_container(
    spec: WorkerSpec,
    *,
    timeout_seconds: float = 15.0,
) -> None:
    """Wait for a launched worker container to reach the running state."""
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        proc = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}",
                spec.container_name,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip() == "true":
            return
        stderr = proc.stderr.strip()
        stdout = proc.stdout.strip()
        if stderr or stdout:
            last_error = stderr or stdout
        time.sleep(0.25)
    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(
        f"Timed out waiting for worker container {spec.container_name} to start{detail}"
    )


def _reset_worker_root(
    repo_root: Path,
    root_dir: Path,
    worktree_path: Path,
) -> None:
    """Remove stale worker state so a worker id can be reused safely."""
    if worktree_path.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    if root_dir.exists():
        shutil.rmtree(root_dir, ignore_errors=True)


def create_worker_spec(
    config: Config,
    *,
    source_file: str,
    function_name: str | None = None,
) -> WorkerSpec:
    """Create directories, worktree, and config for one isolated worker."""
    token_parts = [source_file]
    if function_name:
        token_parts.append(function_name)
    worker_id = slugify_worker_token("-".join(token_parts))

    root_dir = config.codex_code.worker_root / worker_id
    output_dir = root_dir / "output"
    codex_home_dir = root_dir / "codex-home"
    worktree_path = root_dir / "repo"
    config_dir = root_dir / "config"
    decomp_config_path = config_dir / "container.toml"

    _reset_worker_root(config.melee.repo_path, root_dir, worktree_path)
    root_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    codex_home_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    worktree = create_git_worktree(config.melee.repo_path, worktree_path)
    decomp_config_path.write_text(
        render_worker_container_config(config, repo_path=worktree_path),
        encoding="utf-8",
    )

    auth_seed = config.codex_code.auth_file
    if auth_seed is None:
        default_auth = Path.home() / ".codex" / "auth.json"
        auth_seed = default_auth if default_auth.exists() else None

    spec = WorkerSpec(
        worker_id=worker_id,
        function_name=function_name,
        source_file=source_file,
        root_dir=root_dir,
        output_dir=output_dir,
        codex_home_dir=codex_home_dir,
        container_name=f"codex-worker-{worker_id}",
        melee_worktree=worktree,
        decomp_config_path=decomp_config_path,
        auth_seed_path=auth_seed,
    )
    (output_dir / "worker-spec.json").write_text(
        json.dumps(
            {
                "worker_id": worker_id,
                "function_name": function_name,
                "source_file": source_file,
                "container_name": spec.container_name,
                "repo_path": str(worktree_path),
                "codex_home_dir": str(codex_home_dir),
                "decomp_config_path": str(decomp_config_path),
                "auth_seed_path": str(auth_seed) if auth_seed else None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return spec


def cleanup_worker_spec(spec: WorkerSpec) -> None:
    """Remove the worker worktree and local directories."""
    remove_git_worktree(spec.melee_worktree)
    shutil.rmtree(spec.root_dir, ignore_errors=True)
