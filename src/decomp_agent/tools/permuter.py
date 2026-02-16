"""Run decomp-permuter to find matching code permutations for near-matches."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

from decomp_agent.config import Config
from decomp_agent.tools.m2c_tool import get_target_assembly
from decomp_agent.tools.source import get_function_source, read_source_file


@dataclass
class PermuterResult:
    """Result of running decomp-permuter on a function."""

    function_name: str
    best_score: int | None = None
    best_code: str | None = None
    iterations: int = 0
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.best_score is not None and self.best_score == 0

    @property
    def improved(self) -> bool:
        return self.best_code is not None


def _find_permuter() -> Path | None:
    """Find the decomp-permuter installation.

    Checks common locations and PATH.
    """
    # Check if permuter.py is on PATH
    permuter = shutil.which("permuter.py")
    if permuter:
        return Path(permuter)

    # Check common locations
    common_paths = [
        Path.home() / "decomp-permuter" / "permuter.py",
        Path.home() / "tools" / "decomp-permuter" / "permuter.py",
    ]
    for p in common_paths:
        if p.exists():
            return p

    return None


def _build_compile_script(source_file: str, config: Config) -> str:
    """Generate a compile.sh script for the permuter.

    The permuter needs a script that compiles the C code and produces
    an object file for comparison.
    """
    from decomp_agent.tools.build import _object_to_build_target

    build_target = _object_to_build_target(source_file, config)
    repo = config.melee.repo_path

    if config.docker.enabled:
        container = config.docker.container_name
        return f"""#!/bin/bash
INPUT_FILE="$1"
cp "$INPUT_FILE" "{repo}/src/{source_file}"
docker exec -w "{repo}" {container} ninja {build_target}
"""
    return f"""#!/bin/bash
INPUT_FILE="$1"
cp "$INPUT_FILE" "{repo}/src/{source_file}"
cd "{repo}" && ninja {build_target}
"""


def run_permuter(
    function_name: str,
    source_file: str,
    config: Config,
    *,
    timeout: int = 300,
    max_iterations: int = 2000,
) -> PermuterResult:
    """Run decomp-permuter on a function to find matching permutations.

    Sets up a permuter scratch directory with the current C code and
    target assembly, then runs the permuter.

    Args:
        function_name: Name of the function to permute
        source_file: Object name e.g. "melee/lb/lbcommand.c"
        config: Project configuration
        timeout: Maximum seconds to run the permuter
        max_iterations: Maximum permutation iterations
    """
    permuter_path = _find_permuter()
    if permuter_path is None:
        return PermuterResult(
            function_name=function_name,
            error="decomp-permuter not found. Install from "
            "https://github.com/simonlindholm/decomp-permuter",
        )

    # Get current C code for the function
    src_path = config.melee.repo_path / "src" / source_file
    if not src_path.exists():
        return PermuterResult(
            function_name=function_name,
            error=f"Source file not found: {src_path}",
        )

    source = read_source_file(src_path)
    func_code = get_function_source(source, function_name)
    if func_code is None:
        return PermuterResult(
            function_name=function_name,
            error=f"Function {function_name} not found in {source_file}",
        )

    # Get target assembly
    target_asm = get_target_assembly(function_name, source_file, config)
    if target_asm is None:
        return PermuterResult(
            function_name=function_name,
            error="Could not get target assembly",
        )

    # Create temp directory with permuter files
    with tempfile.TemporaryDirectory(prefix="permuter_") as tmpdir:
        work_dir = Path(tmpdir)

        # Write base.c (current C code)
        (work_dir / "base.c").write_text(func_code, encoding="utf-8")

        # Write target.s (target assembly)
        (work_dir / "target.s").write_text(target_asm, encoding="utf-8")

        # Write compile.sh
        compile_script = _build_compile_script(source_file, config)
        compile_sh = work_dir / "compile.sh"
        compile_sh.write_text(compile_script, encoding="utf-8")
        compile_sh.chmod(0o755)

        # Run the permuter
        try:
            result = subprocess.run(
                [
                    "python3",
                    str(permuter_path),
                    str(work_dir),
                    "--iterations",
                    str(max_iterations),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return PermuterResult(
                function_name=function_name,
                error=f"Permuter timed out after {timeout}s",
            )
        except FileNotFoundError:
            return PermuterResult(
                function_name=function_name,
                error="python3 not found",
            )

        # Parse output for best result
        output = result.stdout + result.stderr
        best_code = None
        best_score = None
        iterations = 0

        # The permuter outputs lines like: "iteration N, score M"
        for line in output.splitlines():
            if "iteration" in line.lower():
                try:
                    parts = line.split(",")
                    for part in parts:
                        part = part.strip()
                        if part.lower().startswith("iteration"):
                            iterations = int(part.split()[-1])
                        elif "score" in part.lower():
                            best_score = int(part.split()[-1])
                except (ValueError, IndexError):
                    log.warning("Failed to parse permuter output line: %s", line)

        # Check for output file with best permutation
        best_file = work_dir / "output" / "best.c"
        if best_file.exists():
            best_code = best_file.read_text(encoding="utf-8")

        return PermuterResult(
            function_name=function_name,
            best_score=best_score,
            best_code=best_code,
            iterations=iterations,
            error=result.stderr if result.returncode != 0 else None,
        )
