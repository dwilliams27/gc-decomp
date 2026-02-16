"""Extract target assembly and run m2c to produce initial C decompilation."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from decomp_agent.config import Config
from decomp_agent.tools.run import run_in_repo


@dataclass
class M2CResult:
    """Result of running m2c on a function."""

    function_name: str
    c_code: str | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.c_code is not None


def _source_to_asm_path(source_file: str, config: Config) -> Path:
    """Map a source file name to its dtk-generated assembly file.

    e.g. "melee/lb/lbcommand.c" -> "<repo>/build/GALE01/asm/melee/lb/lbcommand.s"
    """
    stem = source_file.rsplit(".", 1)[0]
    return (
        config.melee.repo_path
        / config.melee.build_dir
        / config.melee.version
        / "asm"
        / f"{stem}.s"
    )


def _source_to_obj_path(source_file: str, config: Config) -> Path:
    """Map a source file name to its dtk-extracted target object file.

    e.g. "melee/lb/lbcommand.c" -> "<repo>/build/GALE01/obj/melee/lb/lbcommand.o"
    """
    stem = source_file.rsplit(".", 1)[0]
    return (
        config.melee.repo_path
        / config.melee.build_dir
        / config.melee.version
        / "obj"
        / f"{stem}.o"
    )


def _ctx_file_path(config: Config) -> Path:
    """Path to the m2c context file."""
    return config.melee.repo_path / "build" / "ctx.c"


# Pattern to match function labels in dtk-generated assembly.
# dtk uses: .global func_name / func_name: or .fn func_name
_FUNC_LABEL_RE = re.compile(r"^(\w+):\s*$")
_GLOBAL_RE = re.compile(r"^\s*\.global\s+(\w+)")
_ENDFN_RE = re.compile(r"^\s*\.(?:endfn|endobj|size)\s+(\w+)")


def extract_function_asm(asm_content: str, function_name: str) -> str | None:
    """Extract a single function's assembly from a dtk-generated .s file.

    Returns the assembly text for the function, or None if not found.
    """
    lines = asm_content.splitlines()
    in_function = False
    func_lines: list[str] = []
    found_global = False

    for line in lines:
        # Check for .global declaration
        global_match = _GLOBAL_RE.match(line)
        if global_match and global_match.group(1) == function_name:
            found_global = True

        # Check for function label
        label_match = _FUNC_LABEL_RE.match(line)
        if label_match and label_match.group(1) == function_name:
            in_function = True
            func_lines.append(line)
            continue

        if in_function:
            # Check for end-of-function markers
            endfn_match = _ENDFN_RE.match(line)
            if endfn_match and endfn_match.group(1) == function_name:
                func_lines.append(line)
                break

            # A new .global or function label ends the current function
            if label_match and label_match.group(1) != function_name:
                break
            new_global = _GLOBAL_RE.match(line)
            if new_global and new_global.group(1) != function_name:
                # Next function's .global — we're done
                break

            func_lines.append(line)

    if not func_lines:
        return None
    return "\n".join(func_lines) + "\n"


def _ensure_asm_exists(source_file: str, config: Config) -> Path:
    """Ensure the dtk-generated .s file exists, building it if needed.

    Raises RuntimeError if the assembly file cannot be obtained.
    """
    asm_path = _source_to_asm_path(source_file, config)

    if asm_path.exists():
        return asm_path

    # Build it via ninja
    asm_rel = str(asm_path.relative_to(config.melee.repo_path))
    result = run_in_repo(["ninja", asm_rel], config=config, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to build assembly for {source_file}: "
            f"{result.stderr or result.stdout}"
        )
    if not asm_path.exists():
        raise RuntimeError(
            f"ninja succeeded but assembly file not found at {asm_path}"
        )
    return asm_path


def get_target_assembly(
    function_name: str,
    source_file: str,
    config: Config,
) -> str | None:
    """Get the target PowerPC assembly for a function.

    Reads the dtk-generated .s file for the translation unit and
    extracts the specific function's assembly.

    Args:
        function_name: Name of the function
        source_file: Object name e.g. "melee/lb/lbcommand.c"
        config: Project configuration

    Returns:
        Assembly text for the function, or None if the function
        is not found in the assembly file.

    Raises:
        RuntimeError: If the assembly file cannot be built.
    """
    asm_path = _ensure_asm_exists(source_file, config)
    asm_content = asm_path.read_text(encoding="utf-8", errors="replace")
    return extract_function_asm(asm_content, function_name)


def get_full_asm(source_file: str, config: Config) -> str:
    """Get the full assembly file for a translation unit.

    Returns the entire .s file content.

    Raises:
        RuntimeError: If the assembly file cannot be built.
    """
    asm_path = _ensure_asm_exists(source_file, config)
    return asm_path.read_text(encoding="utf-8", errors="replace")


def generate_m2c_context(config: Config) -> None:
    """Generate the m2c context file (build/ctx.c) via m2ctx.py.

    Raises:
        FileNotFoundError: If m2ctx.py script is not found.
        RuntimeError: If context generation fails.
    """
    m2ctx_script = config.melee.repo_path / "tools" / "m2ctx" / "m2ctx.py"
    if not m2ctx_script.exists():
        raise FileNotFoundError(
            f"m2ctx.py not found at {m2ctx_script}. "
            f"Is the melee repo set up correctly?"
        )

    result = run_in_repo(
        ["python3", str(m2ctx_script), "--quiet", "--preprocessor"],
        config=config,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"m2ctx.py failed: {result.stderr or result.stdout}"
        )


def run_m2c(
    function_name: str,
    source_file: str,
    config: Config,
    *,
    regenerate_ctx: bool = False,
) -> M2CResult:
    """Run m2c on a function's target assembly to produce C code.

    This mimics the melee repo's tools/decomp.py workflow:
    1. Locate the .s file for the translation unit
    2. Optionally regenerate m2c context (ctx.c)
    3. Run m2c with matching-focused flags

    Args:
        function_name: Name of the function to decompile
        source_file: Object name e.g. "melee/lb/lbcommand.c"
        config: Project configuration
        regenerate_ctx: If True, regenerate ctx.c before running m2c
    """
    # Ensure asm file exists (raises RuntimeError on failure)
    asm_path = _ensure_asm_exists(source_file, config)

    # Optionally regenerate m2c context (raises on failure)
    if regenerate_ctx:
        generate_m2c_context(config)

    # Build m2c command
    ctx_path = _ctx_file_path(config)
    m2c_args = [
        "python3", "-m", "m2c.main",
        "--knr", "--pointer", "left",
        "--target", "ppc-mwcc-c",
    ]

    # Add context if available
    if ctx_path.exists():
        m2c_args.extend(["--context", str(ctx_path)])

    # Add function name and asm file
    m2c_args.extend(["--function", function_name, str(asm_path)])

    try:
        result = run_in_repo(m2c_args, config=config, timeout=60)
    except subprocess.TimeoutExpired:
        return M2CResult(
            function_name=function_name,
            error="m2c timed out",
        )
    except FileNotFoundError:
        return M2CResult(
            function_name=function_name,
            error="python3 not found",
        )

    if result.returncode != 0:
        return M2CResult(
            function_name=function_name,
            error=result.stderr or result.stdout or "m2c failed with no output",
        )

    c_code = result.stdout.strip()
    if not c_code:
        return M2CResult(
            function_name=function_name,
            error="m2c produced empty output",
        )

    return M2CResult(function_name=function_name, c_code=c_code)
