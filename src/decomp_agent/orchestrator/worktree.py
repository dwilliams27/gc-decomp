"""Helpers for managing isolated git worktrees for workers."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


def slugify_worker_token(value: str) -> str:
    """Convert a function/file identifier into a filesystem-safe token."""
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return token[:80] or "worker"


@dataclass
class WorktreeSpec:
    repo_root: Path
    worktree_path: Path


def create_git_worktree(repo_root: Path, worktree_path: Path) -> WorktreeSpec:
    """Create a detached worktree at HEAD."""
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree_path), "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    return WorktreeSpec(repo_root=repo_root, worktree_path=worktree_path)


def remove_git_worktree(spec: WorktreeSpec) -> None:
    """Remove a detached worktree and clean up any leftover directory."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(spec.worktree_path)],
        check=True,
        capture_output=True,
        text=True,
        cwd=spec.repo_root,
    )
    if spec.worktree_path.exists():
        shutil.rmtree(spec.worktree_path, ignore_errors=True)
