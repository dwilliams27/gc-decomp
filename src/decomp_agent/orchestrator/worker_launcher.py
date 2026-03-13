"""Host-side preparation for isolated worker containers."""

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


def _load_dotenv_value(repo_root: Path, key: str) -> str | None:
    """Load a single key from the repo-root .env file if present."""
    env_path = repo_root / ".env"
    if not env_path.is_file():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        return value.strip().strip("'").strip('"')
    return None


@dataclass
class WorkerSpec:
    provider: str
    worker_id: str
    function_name: str | None
    source_file: str
    root_dir: Path
    output_dir: Path
    agent_home_dir: Path
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
    """Build the docker run command for a single isolated worker."""
    repo_root = Path(__file__).parents[3]
    workspace_repo_root = Path("/workspace/gc-decomp")
    image = config.codex_code.image if spec.provider == "codex" else config.claude_code.image
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
        "-e",
        f"PYTHONPATH={workspace_repo_root / 'src'}",
    ]

    if config.codex_code.http_proxy:
        args.extend(["-e", f"HTTP_PROXY={config.codex_code.http_proxy}"])
    if config.codex_code.https_proxy:
        args.extend(["-e", f"HTTPS_PROXY={config.codex_code.https_proxy}"])
    claude_oauth_token = (
        os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        or _load_dotenv_value(repo_root, "CLAUDE_CODE_OAUTH_TOKEN")
    )
    if claude_oauth_token:
        args.extend(["-e", f"CLAUDE_CODE_OAUTH_TOKEN={claude_oauth_token}"])
    if spec.auth_seed_path is not None:
        args.extend([
            "-e",
            "CODEX_AUTH_SEED=/seed/codex/auth.json",
            "-v",
            f"{spec.auth_seed_path}:/seed/codex/auth.json:ro",
        ])

    agent_home_mount = "/home/decomp/.codex" if spec.provider == "codex" else "/home/decomp/.claude"

    args.extend([
        "-v",
        f"{spec.melee_worktree.worktree_path}:{spec.melee_worktree.worktree_path}:rw",
        "-v",
        f"{spec.agent_home_dir}:{agent_home_mount}:rw",
        "-v",
        f"{spec.output_dir}:{spec.output_dir}:rw",
        "-v",
        f"{spec.decomp_config_path}:{spec.decomp_config_path}:ro",
        "-v",
        f"{repo_root}:{workspace_repo_root}:rw",
        image,
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
    provider: str = "codex",
    source_file: str,
    function_name: str | None = None,
) -> WorkerSpec:
    """Create directories, worktree, and config for one isolated worker."""
    token_parts = [source_file]
    if function_name:
        token_parts.append(function_name)
    worker_id = slugify_worker_token("-".join(token_parts))

    if provider == "codex":
        worker_root = config.codex_code.worker_root
    elif provider == "claude":
        worker_root = config.claude_code.worker_root
    else:
        raise ValueError(f"Unsupported worker provider '{provider}'")

    root_dir = worker_root / worker_id
    output_dir = root_dir / "output"
    agent_home_dir = root_dir / "agent-home"
    worktree_path = root_dir / "repo"
    config_dir = root_dir / "config"
    decomp_config_path = config_dir / "container.toml"

    _reset_worker_root(config.melee.repo_path, root_dir, worktree_path)
    root_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    agent_home_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    worktree = create_git_worktree(config.melee.repo_path, worktree_path)
    decomp_config_path.write_text(
        render_worker_container_config(config, repo_path=worktree_path),
        encoding="utf-8",
    )

    auth_seed: Path | None = None
    if provider == "codex":
        auth_seed = config.codex_code.auth_file
        if auth_seed is None:
            default_auth = Path.home() / ".codex" / "auth.json"
            auth_seed = default_auth if default_auth.exists() else None

    spec = WorkerSpec(
        provider=provider,
        worker_id=worker_id,
        function_name=function_name,
        source_file=source_file,
        root_dir=root_dir,
        output_dir=output_dir,
        agent_home_dir=agent_home_dir,
        container_name=f"{provider}-worker-{worker_id}",
        melee_worktree=worktree,
        decomp_config_path=decomp_config_path,
        auth_seed_path=auth_seed,
    )
    (output_dir / "worker-spec.json").write_text(
        json.dumps(
            {
                "provider": provider,
                "worker_id": worker_id,
                "function_name": function_name,
                "source_file": source_file,
                "container_name": spec.container_name,
                "repo_path": str(worktree_path),
                "agent_home_dir": str(agent_home_dir),
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
