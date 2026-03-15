from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from decomp_agent.config import Config, MeleeConfig
from decomp_agent.orchestrator.headless_context import (
    build_campaign_orchestrator_prompt,
    build_headless_task_prompt,
    load_campaign_orchestrator_system_prompt,
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


def test_load_campaign_orchestrator_system_prompt_is_manager_focused():
    prompt = load_campaign_orchestrator_system_prompt()
    assert "manage worker agents" in prompt
    assert "campaign MCP tools" in prompt
    assert "Do not use write_function" in prompt
    assert "Maintain explicit written notes" in prompt
    assert "persistent manager session" in prompt
    assert "scratchpad" in prompt


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
    assert "DO NOT rewrite the function" in prompt


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


def test_build_campaign_orchestrator_prompt_mentions_campaign_tools(tmp_path):
    config = _make_config(tmp_path)

    with patch(
        "decomp_agent.tools.build.check_match",
        return_value=CompileResult(
            object_name="melee/test/testfile.c",
            success=True,
            functions=[],
        ),
    ):
        prompt = build_campaign_orchestrator_prompt(
            7,
            "melee/test/testfile.c",
            config,
            resumed=True,
            wake_reason="worker_terminal_event",
            wake_summary="Recent significant events:\n- worker_completed foo",
        )

    assert "campaign #7" in prompt
    assert "no true internet browsing access" in prompt
    assert "campaign_get_status" in prompt
    assert "campaign_get_scratchpad" in prompt
    assert "campaign_get_function_memory" in prompt
    assert "campaign_get_notes" in prompt
    assert "campaign_write_note" in prompt
    assert "campaign_run_next_task" in prompt
    assert "campaign_launch_worker" in prompt
    assert "persistent campaign-management session" in prompt
    assert "Wake reason: worker_terminal_event" in prompt
    assert "bias strongly toward low-match or unattempted functions" in prompt
    assert "do not let the campaign spend most of its time on the same 95%+ functions" in prompt
