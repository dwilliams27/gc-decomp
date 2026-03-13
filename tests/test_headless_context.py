from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from decomp_agent.config import Config, MeleeConfig
from decomp_agent.orchestrator.headless_context import (
    build_headless_task_prompt,
    load_headless_system_prompt,
)
from decomp_agent.tools.build import CompileResult, FunctionMatch


def _make_config(tmp_path: Path) -> Config:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "configure.py").write_text("print('stub')", encoding="utf-8")
    return Config(melee=MeleeConfig(repo_path=repo))


def test_load_headless_system_prompt_contains_tool_guidance():
    prompt = load_headless_system_prompt()
    assert "write_function" in prompt
    assert "mark_complete" in prompt


def test_build_headless_task_prompt_file_mode_includes_status(tmp_path):
    config = _make_config(tmp_path)
    compile_result = CompileResult(
        object_name="melee/test/testfile.c",
        success=True,
        functions=[
            FunctionMatch(name="foo", fuzzy_match_percent=100.0, size=8),
            FunctionMatch(name="bar", fuzzy_match_percent=75.0, size=12),
        ],
    )

    with patch(
        "decomp_agent.tools.build.check_match",
        return_value=compile_result,
    ):
        prompt = build_headless_task_prompt(
            None,
            "melee/test/testfile.c",
            config,
        )

    assert "Current status:" in prompt
    assert "foo: MATCH" in prompt
    assert "bar: 75.0%" in prompt


def test_build_headless_task_prompt_warm_start_includes_diff(tmp_path):
    config = _make_config(tmp_path)

    with (
        patch(
            "decomp_agent.tools.disasm.get_function_diff",
            return_value="diff body",
        ),
        patch(
            "decomp_agent.orchestrator.headless_context.build_prefetched_m2c_block",
            return_value="\n\nm2c seed",
        ),
    ):
        prompt = build_headless_task_prompt(
            "target_fn",
            "melee/test/testfile.c",
            config,
            prior_best_code="void target_fn(void) {}",
            prior_match_pct=82.5,
        )

    assert "82.5% match" in prompt
    assert "Current diff (target vs compiled)" in prompt
    assert "diff body" in prompt
    assert "VERY close" in prompt
    assert "Be relentless" in prompt


def test_build_headless_task_prompt_cold_start_includes_relentless_guidance(tmp_path):
    config = _make_config(tmp_path)

    with patch(
        "decomp_agent.orchestrator.headless_context.build_prefetched_m2c_block",
        return_value="\n\nm2c seed",
    ):
        prompt = build_headless_task_prompt(
            "target_fn",
            "melee/test/testfile.c",
            config,
        )

    assert "Be relentless" in prompt
    assert "many turns" in prompt
    assert "Do not stop after a first draft" in prompt
