"""Build + verify tools: compile objects and check match status."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from decomp_agent.config import Config
from decomp_agent.tools.run import run_in_repo


@dataclass
class FunctionMatch:
    """Match result for a single function within an object."""

    name: str
    fuzzy_match_percent: float
    size: int

    @property
    def is_matched(self) -> bool:
        return self.fuzzy_match_percent == 100.0


@dataclass
class CompileResult:
    """Result of compiling and checking an object file."""

    object_name: str  # e.g. "melee/lb/lbcommand.c"
    success: bool  # True if compilation succeeded
    functions: list[FunctionMatch] = field(default_factory=list)
    error: str | None = None

    @property
    def all_matched(self) -> bool:
        return all(f.is_matched for f in self.functions)

    @property
    def match_percent(self) -> float:
        if not self.functions:
            return 0.0
        return sum(f.fuzzy_match_percent for f in self.functions) / len(
            self.functions
        )

    def get_function(self, name: str) -> FunctionMatch | None:
        for f in self.functions:
            if f.name == name:
                return f
        return None


def _object_to_build_target(object_name: str, config: Config) -> str:
    """Convert object name to ninja build target path.

    e.g. "melee/lb/lbcommand.c" -> "build/GALE01/src/melee/lb/lbcommand.o"
    """
    stem = object_name.rsplit(".", 1)[0]  # strip .c extension
    return f"{config.melee.build_dir}/{config.melee.version}/src/{stem}.o"


def _object_to_unit_name(object_name: str) -> str:
    """Convert object name to objdiff unit name.

    e.g. "melee/lb/lbcommand.c" -> "main/melee/lb/lbcommand"
    """
    stem = object_name.rsplit(".", 1)[0]
    return f"main/{stem}"


def compile_object(object_name: str, config: Config) -> CompileResult:
    """Compile a single object file using ninja.

    Args:
        object_name: Object name from configure.py, e.g. "melee/lb/lbcommand.c"
        config: Project configuration

    Returns:
        CompileResult with success status and any error message.
    """
    target = _object_to_build_target(object_name, config)

    try:
        result = run_in_repo(["ninja", target], config=config)
    except subprocess.TimeoutExpired:
        return CompileResult(
            object_name=object_name,
            success=False,
            error="Compilation timed out",
        )

    if result.returncode != 0:
        return CompileResult(
            object_name=object_name,
            success=False,
            error=result.stderr or result.stdout,
        )

    return CompileResult(object_name=object_name, success=True)


def check_match(object_name: str, config: Config) -> CompileResult:
    """Compile an object and check match status via the report.

    Builds the object, regenerates report.json, and reads per-function
    match data for this unit.
    """
    # First compile
    compile_result = compile_object(object_name, config)
    if not compile_result.success:
        return compile_result

    # Regenerate report
    report_rel = f"{config.melee.build_dir}/{config.melee.version}/report.json"
    try:
        result = run_in_repo(["ninja", report_rel], config=config)
    except subprocess.TimeoutExpired:
        compile_result.error = "Report generation timed out"
        return compile_result

    if result.returncode != 0:
        compile_result.error = f"Report generation failed: {result.stderr}"
        return compile_result

    # Parse report for this unit
    report_path = config.melee.report_path
    if not report_path.exists():
        compile_result.error = "report.json not found after generation"
        return compile_result

    with open(report_path) as f:
        report_data = json.load(f)

    unit_name = _object_to_unit_name(object_name)
    for unit in report_data.get("units", []):
        if unit["name"] == unit_name:
            for func_data in unit.get("functions", []):
                if func_data is None:
                    continue
                compile_result.functions.append(
                    FunctionMatch(
                        name=func_data["name"],
                        fuzzy_match_percent=float(
                            func_data.get("fuzzy_match_percent", 0)
                        ),
                        size=int(func_data.get("size", 0)),
                    )
                )
            break

    return compile_result


def get_diff(object_name: str, config: Config) -> str:
    """Get detailed assembly diff for an object using objdiff-cli.

    Returns the diff output as a string, showing instruction-level
    differences between the compiled and target objects.
    """
    unit_name = _object_to_unit_name(object_name)

    # objdiff-cli diff reads objdiff.json for target/base paths
    try:
        result = run_in_repo(
            ["objdiff-cli", "diff", "-u", unit_name], config=config
        )
    except subprocess.TimeoutExpired:
        return "Diff timed out"
    except FileNotFoundError:
        return "objdiff-cli not found"

    if result.returncode != 0:
        return f"Diff failed: {result.stderr or result.stdout}"

    return result.stdout


def get_function_diff(
    object_name: str, function_name: str, config: Config
) -> str:
    """Get assembly diff for a specific function.

    Tries to use objdiff-cli's function-level diff if available,
    falls back to full unit diff.
    """
    unit_name = _object_to_unit_name(object_name)

    try:
        result = run_in_repo(
            ["objdiff-cli", "diff", "-u", unit_name, "-s", function_name],
            config=config,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return get_diff(object_name, config)

    if result.returncode != 0:
        # Fall back to full diff
        return get_diff(object_name, config)

    return result.stdout
