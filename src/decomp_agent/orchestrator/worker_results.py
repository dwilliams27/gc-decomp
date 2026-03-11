"""Helpers for collecting and loading isolated worker artifacts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from decomp_agent.agent.loop import AgentResult
from decomp_agent.orchestrator.worker_launcher import WorkerSpec


@dataclass
class WorkerArtifacts:
    result_json: Path
    patch_file: Path
    metadata_file: Path


def worker_artifact_paths(spec: WorkerSpec) -> WorkerArtifacts:
    """Return the standard artifact paths for a worker."""
    return WorkerArtifacts(
        result_json=spec.output_dir / "result.json",
        patch_file=spec.output_dir / "worker.patch",
        metadata_file=spec.output_dir / "artifacts.json",
    )


def export_worker_patch(spec: WorkerSpec) -> Path:
    """Export a patch for all changes in the worker worktree."""
    paths = worker_artifact_paths(spec)
    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        cwd=spec.melee_worktree.worktree_path,
    )
    paths.patch_file.write_text(diff.stdout, encoding="utf-8")
    return paths.patch_file


def write_worker_result(
    spec: WorkerSpec,
    result: AgentResult,
    *,
    extra: dict | None = None,
) -> Path:
    """Persist a minimal result payload for host-side ingestion."""
    paths = worker_artifact_paths(spec)
    payload = {
        "matched": result.matched,
        "best_match_percent": result.best_match_percent,
        "iterations": result.iterations,
        "total_tokens": result.total_tokens,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cached_tokens": result.cached_tokens,
        "elapsed_seconds": result.elapsed_seconds,
        "final_code": result.final_code,
        "error": result.error,
        "termination_reason": result.termination_reason,
        "model": result.model,
        "reasoning_effort": result.reasoning_effort,
        "warm_start": result.warm_start,
        "session_id": result.session_id,
        "artifact_dir": result.artifact_dir,
        "patch_path": result.patch_path,
        "file_mode": result.file_mode,
        "newly_matched": result.newly_matched,
        "function_deltas": result.function_deltas,
    }
    if extra:
        payload["extra"] = extra
    paths.result_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return paths.result_json


def load_worker_result(result_path: Path) -> AgentResult:
    """Load an AgentResult from a worker result payload."""
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    return AgentResult(
        matched=payload.get("matched", False),
        best_match_percent=payload.get("best_match_percent", 0.0),
        iterations=payload.get("iterations", 0),
        total_tokens=payload.get("total_tokens", 0),
        input_tokens=payload.get("input_tokens", 0),
        output_tokens=payload.get("output_tokens", 0),
        cached_tokens=payload.get("cached_tokens", 0),
        elapsed_seconds=payload.get("elapsed_seconds", 0.0),
        final_code=payload.get("final_code"),
        error=payload.get("error"),
        termination_reason=payload.get("termination_reason", ""),
        model=payload.get("model", ""),
        reasoning_effort=payload.get("reasoning_effort", ""),
        warm_start=payload.get("warm_start", False),
        session_id=payload.get("session_id", ""),
        artifact_dir=payload.get("artifact_dir", ""),
        patch_path=payload.get("patch_path", ""),
        file_mode=payload.get("file_mode", False),
        newly_matched=payload.get("newly_matched", []),
        function_deltas=payload.get("function_deltas", {}),
    )


def write_worker_artifact_manifest(spec: WorkerSpec) -> Path:
    """Write a small manifest describing the standard worker artifact paths."""
    paths = worker_artifact_paths(spec)
    payload = {
        "result_json": str(paths.result_json),
        "patch_file": str(paths.patch_file),
        "worker_id": spec.worker_id,
        "source_file": spec.source_file,
        "function_name": spec.function_name,
    }
    paths.metadata_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return paths.metadata_file
