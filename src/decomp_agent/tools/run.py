"""Shared subprocess runner with docker support."""

from __future__ import annotations

import subprocess
from pathlib import Path

from decomp_agent.config import Config


def run_in_repo(
    args: list[str],
    config: Config,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run a command in the melee repo, optionally inside docker.

    When docker is enabled, runs via `docker exec -w <repo_path>` so
    the command executes in the correct working directory inside the
    container. The melee repo is expected to be mounted at the same
    path inside the container.

    When docker is disabled, runs directly on the host with cwd set
    to the melee repo.
    """
    if config.docker.enabled:
        docker_args = [
            "docker",
            "exec",
            "-w",
            str(config.melee.repo_path),
            config.docker.container_name,
        ] + args
        return subprocess.run(
            docker_args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    return subprocess.run(
        args,
        cwd=config.melee.repo_path,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
