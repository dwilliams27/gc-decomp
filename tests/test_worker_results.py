from __future__ import annotations

import subprocess

from decomp_agent.agent.loop import AgentResult
from decomp_agent.orchestrator.worker_launcher import (
    cleanup_worker_spec,
    create_worker_spec,
)
from decomp_agent.orchestrator.worker_results import (
    export_worker_patch,
    load_worker_result,
    worker_artifact_paths,
    write_worker_artifact_manifest,
    write_worker_result,
)
from tests.test_worker_launcher import _init_git_repo
from tests.fixtures.fake_repo import create_fake_repo


def test_write_and_load_worker_result(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.codex_code.worker_root = tmp_path / "workers"

    spec = create_worker_spec(
        config,
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    try:
        result = AgentResult(
            matched=True,
            best_match_percent=100.0,
            iterations=4,
            total_tokens=1234,
            input_tokens=800,
            output_tokens=300,
            cached_tokens=134,
            elapsed_seconds=12.5,
            final_code="s32 simple_add(s32 a, s32 b) { return a + b; }",
            termination_reason="matched",
            model="codex-code-headless",
            session_id="thread-1",
        )
        result_path = write_worker_result(spec, result, extra={"note": "ok"})
        loaded = load_worker_result(result_path)
    finally:
        cleanup_worker_spec(spec)

    assert loaded.matched is True
    assert loaded.best_match_percent == 100.0
    assert loaded.model == "codex-code-headless"
    assert loaded.session_id == "thread-1"


def test_export_worker_patch_writes_diff(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.codex_code.worker_root = tmp_path / "workers"

    spec = create_worker_spec(
        config,
        source_file="melee/test/testfile.c",
        function_name="simple_add",
    )
    try:
        source_path = spec.melee_worktree.worktree_path / "src" / "melee" / "test" / "testfile.c"
        source_text = source_path.read_text(encoding="utf-8")
        source_path.write_text(source_text.replace("return a + b;", "return a - b;"), encoding="utf-8")

        patch_path = export_worker_patch(spec)
        patch_text = patch_path.read_text(encoding="utf-8")
    finally:
        cleanup_worker_spec(spec)

    assert patch_path.name == "worker.patch"
    assert "return a - b;" in patch_text
    assert "diff --git" in patch_text


def test_write_worker_artifact_manifest(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.codex_code.worker_root = tmp_path / "workers"

    spec = create_worker_spec(
        config,
        source_file="melee/test/testfile.c",
        function_name=None,
    )
    try:
        manifest_path = write_worker_artifact_manifest(spec)
        paths = worker_artifact_paths(spec)
    finally:
        cleanup_worker_spec(spec)

    assert manifest_path == paths.metadata_file
    assert manifest_path.name == "artifacts.json"
