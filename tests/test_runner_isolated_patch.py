from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from sqlmodel import Session, select

from decomp_agent.agent.loop import AgentResult
from decomp_agent.models.db import Attempt, Function, get_engine, sync_from_report
from decomp_agent.melee.functions import FunctionInfo
from decomp_agent.melee.project import ObjectStatus
from decomp_agent.orchestrator.runner import run_function
from decomp_agent.tools.build import CompileResult, FunctionMatch
from tests.fixtures.fake_repo import create_fake_repo
from tests.test_worker_launcher import _init_git_repo


def _seed_db(engine, config) -> list[Function]:
    report_data = {
        "units": [
            {
                "name": "main/melee/test/testfile",
                "functions": [
                    {
                        "name": "simple_init",
                        "size": 40,
                        "fuzzy_match_percent": 55.0,
                        "metadata": {"virtual_address": hex(0x800A0000)},
                    },
                    {
                        "name": "simple_add",
                        "size": 8,
                        "fuzzy_match_percent": 60.0,
                        "metadata": {"virtual_address": hex(0x800A0040)},
                    },
                    {
                        "name": "simple_loop",
                        "size": 48,
                        "fuzzy_match_percent": 50.0,
                        "metadata": {"virtual_address": hex(0x800A0080)},
                    },
                ],
            }
        ]
    }
    infos = []
    for unit in report_data["units"]:
        for func_data in unit["functions"]:
            infos.append(
                FunctionInfo(
                    name=func_data["name"],
                    address=int(func_data["metadata"]["virtual_address"], 0),
                    size=func_data["size"],
                    fuzzy_match_percent=func_data["fuzzy_match_percent"],
                    unit_name=unit["name"].removeprefix("main/"),
                    source_file="melee/test/testfile.c",
                    object_status=ObjectStatus.NON_MATCHING,
                    library="test (Library)",
                )
            )
    with Session(engine) as session:
        sync_from_report(session, infos)
        return list(session.exec(select(Function)).all())


def test_run_function_promotes_isolated_patch(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.codex_code.enabled = True
    config.codex_code.isolated_worker_enabled = True

    engine = get_engine(":memory:")
    functions = _seed_db(engine, config)
    target_func = next(f for f in functions if f.name == "simple_add")

    src_path = repo_path / "src" / "melee" / "test" / "testfile.c"
    original_source = src_path.read_text(encoding="utf-8")
    updated_source = original_source.replace("return a + b;", "return a - b;")
    src_path.write_text(updated_source, encoding="utf-8")
    patch_text = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        cwd=repo_path,
    ).stdout
    patch_path = tmp_path / "worker.patch"
    patch_path.write_text(patch_text, encoding="utf-8")
    src_path.write_text(original_source, encoding="utf-8")

    baseline = CompileResult(
        object_name="melee/test/testfile.c",
        success=True,
        functions=[
            FunctionMatch(name="simple_init", fuzzy_match_percent=55.0, size=40),
            FunctionMatch(name="simple_add", fuzzy_match_percent=60.0, size=8),
            FunctionMatch(name="simple_loop", fuzzy_match_percent=50.0, size=48),
        ],
    )
    matched = CompileResult(
        object_name="melee/test/testfile.c",
        success=True,
        functions=[
            FunctionMatch(name="simple_init", fuzzy_match_percent=55.0, size=40),
            FunctionMatch(name="simple_add", fuzzy_match_percent=100.0, size=8),
            FunctionMatch(name="simple_loop", fuzzy_match_percent=50.0, size=48),
        ],
    )
    check_results = [baseline, matched, matched]

    with (
        patch(
            "decomp_agent.orchestrator.codex_headless.run_codex_headless",
            return_value=AgentResult(
                best_match_percent=100.0,
                termination_reason="isolated_patch_ready",
                model="codex-code-headless",
                patch_path=str(patch_path),
                artifact_dir=str(tmp_path),
            ),
        ),
        patch(
            "decomp_agent.orchestrator.runner.check_match",
            side_effect=lambda *args, **kwargs: check_results.pop(0),
        ),
        patch("decomp_agent.orchestrator.runner._auto_commit_match"),
    ):
        result = run_function(target_func, config, engine)

    assert result.matched is True
    assert result.termination_reason == "matched"
    assert "return a - b;" in src_path.read_text(encoding="utf-8")

    with Session(engine) as session:
        loaded = session.exec(select(Function).where(Function.name == "simple_add")).one()
        attempt = session.exec(select(Attempt).where(Attempt.function_id == loaded.id)).one()

    assert loaded.status == "matched"
    assert attempt.patch_path == str(patch_path)


def test_run_function_does_not_revert_main_source_for_unmatched_isolated_worker(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    _init_git_repo(repo_path)
    config.codex_code.enabled = True
    config.codex_code.isolated_worker_enabled = True

    engine = get_engine(":memory:")
    functions = _seed_db(engine, config)
    target_func = next(f for f in functions if f.name == "simple_add")

    src_path = repo_path / "src" / "melee" / "test" / "testfile.c"

    baseline = CompileResult(
        object_name="melee/test/testfile.c",
        success=True,
        functions=[
            FunctionMatch(name="simple_init", fuzzy_match_percent=55.0, size=40),
            FunctionMatch(name="simple_add", fuzzy_match_percent=60.0, size=8),
            FunctionMatch(name="simple_loop", fuzzy_match_percent=50.0, size=48),
        ],
    )

    def fake_isolated_run(*args, **kwargs):
        current = src_path.read_text(encoding="utf-8")
        src_path.write_text(
            current.replace("return a + b;", "return a + b + 1;"),
            encoding="utf-8",
        )
        return AgentResult(
            matched=False,
            best_match_percent=88.0,
            termination_reason="model_stopped",
            model="codex-code-headless",
        )

    with (
        patch(
            "decomp_agent.orchestrator.codex_headless.run_codex_headless",
            side_effect=fake_isolated_run,
        ),
        patch(
            "decomp_agent.orchestrator.runner.check_match",
            return_value=baseline,
        ),
        patch("decomp_agent.orchestrator.runner._auto_commit_match"),
    ):
        result = run_function(target_func, config, engine)

    assert result.matched is False
    assert "return a + b + 1;" in src_path.read_text(encoding="utf-8")


def test_run_function_retries_transient_baseline_failure(tmp_path):
    _repo_path, config = create_fake_repo(tmp_path)
    config.claude_code.enabled = True
    config.campaign.baseline_compile_retries = 1

    engine = get_engine(":memory:")
    functions = _seed_db(engine, config)
    target_func = next(f for f in functions if f.name == "simple_add")

    failed_baseline = CompileResult(
        object_name="melee/test/testfile.c",
        success=False,
        error="transient depfile race",
    )
    recovered_baseline = CompileResult(
        object_name="melee/test/testfile.c",
        success=True,
        functions=[
            FunctionMatch(name="simple_init", fuzzy_match_percent=55.0, size=40),
            FunctionMatch(name="simple_add", fuzzy_match_percent=60.0, size=8),
            FunctionMatch(name="simple_loop", fuzzy_match_percent=50.0, size=48),
        ],
    )

    with (
        patch(
            "decomp_agent.orchestrator.headless.run_headless",
            return_value=AgentResult(
                matched=False,
                best_match_percent=77.0,
                termination_reason="model_stopped",
            ),
        ),
        patch(
            "decomp_agent.orchestrator.runner.check_match",
            side_effect=[failed_baseline, recovered_baseline],
        ) as mocked_check_match,
        patch("decomp_agent.orchestrator.runner._auto_commit_match"),
    ):
        result = run_function(target_func, config, engine)

    assert result.best_match_percent == 77.0
    assert mocked_check_match.call_count == 2


def test_run_function_warm_starts_from_current_source_when_no_attempt_exists(tmp_path):
    repo_path, config = create_fake_repo(tmp_path)
    config.claude_code.enabled = True

    engine = get_engine(":memory:")
    functions = _seed_db(engine, config)
    target_func = next(f for f in functions if f.name == "simple_add")

    baseline = CompileResult(
        object_name="melee/test/testfile.c",
        success=True,
        functions=[
            FunctionMatch(name="simple_init", fuzzy_match_percent=55.0, size=40),
            FunctionMatch(name="simple_add", fuzzy_match_percent=60.0, size=8),
            FunctionMatch(name="simple_loop", fuzzy_match_percent=50.0, size=48),
        ],
    )
    captured: dict[str, object] = {}

    def fake_headless(function_name, source_file, config, **kwargs):
        del function_name, source_file, config
        captured["prior_best_code"] = kwargs.get("prior_best_code")
        captured["prior_match_pct"] = kwargs.get("prior_match_pct")
        return AgentResult(
            matched=False,
            best_match_percent=77.0,
            termination_reason="model_stopped",
        )

    with (
        patch(
            "decomp_agent.orchestrator.headless.run_headless",
            side_effect=fake_headless,
        ),
        patch(
            "decomp_agent.orchestrator.runner.check_match",
            return_value=baseline,
        ),
        patch("decomp_agent.orchestrator.runner._auto_commit_match"),
    ):
        run_function(target_func, config, engine, warm_start=True)

    assert "return a + b;" in str(captured["prior_best_code"])
    assert captured["prior_match_pct"] == 60.0
