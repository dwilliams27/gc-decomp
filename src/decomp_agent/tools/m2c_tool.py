"""Extract target assembly and run m2c to produce initial C decompilation."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
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


def _split_config_path(config: Config) -> Path:
    """Path to the dtk split completion target."""
    return (
        config.melee.repo_path
        / config.melee.build_dir
        / config.melee.version
        / "config.json"
    )


# Pattern to match function labels in dtk-generated assembly.
# dtk uses: .fn func_name, global  /  .endfn func_name
# Also supports legacy: .global func_name / func_name:
_FUNC_LABEL_RE = re.compile(r"^(\w+):\s*$")
_FN_DIRECTIVE_RE = re.compile(r"^\.fn\s+(\w+)")
_GLOBAL_RE = re.compile(r"^\s*\.global\s+(\w+)")
_ENDFN_RE = re.compile(r"^\s*\.(?:endfn|endobj|size)\s+(\w+)")


def extract_function_asm(asm_content: str, function_name: str) -> str | None:
    """Extract a single function's assembly from a dtk-generated .s file.

    Returns the assembly text for the function, or None if not found.
    Handles both `.fn name, global` directives and plain `name:` labels.
    """
    lines = asm_content.splitlines()
    in_function = False
    func_lines: list[str] = []

    for line in lines:
        if not in_function:
            # Check for .fn directive: ".fn func_name, global"
            fn_match = _FN_DIRECTIVE_RE.match(line)
            if fn_match and fn_match.group(1) == function_name:
                in_function = True
                func_lines.append(line)
                continue

            # Check for plain label: "func_name:"
            label_match = _FUNC_LABEL_RE.match(line)
            if label_match and label_match.group(1) == function_name:
                in_function = True
                func_lines.append(line)
                continue
        else:
            # Check for end-of-function markers
            endfn_match = _ENDFN_RE.match(line)
            if endfn_match and endfn_match.group(1) == function_name:
                func_lines.append(line)
                break

            # A new .fn directive starts a different function — we're done
            fn_match = _FN_DIRECTIVE_RE.match(line)
            if fn_match and fn_match.group(1) != function_name:
                break

            # A new .global for a different function — we're done
            global_match = _GLOBAL_RE.match(line)
            if global_match and global_match.group(1) != function_name:
                break

            # A new plain label starts a different function — we're done
            label_match = _FUNC_LABEL_RE.match(line)
            if label_match and label_match.group(1) != function_name:
                break

            func_lines.append(line)

    if not func_lines:
        return None
    return "\n".join(func_lines) + "\n"


def _ensure_target_split_outputs(source_file: str, config: Config) -> None:
    """Ensure target split outputs exist for a translation unit.

    Modern melee builds materialize the per-TU asm/object outputs as side
    effects of the split/config target rather than as first-class Ninja targets.
    Build the split target once if neither the asm nor object output exists yet.
    """
    asm_path = _source_to_asm_path(source_file, config)
    obj_path = _source_to_obj_path(source_file, config)

    if asm_path.exists() or obj_path.exists():
        return

    split_target = _split_config_path(config)
    split_rel = str(split_target.relative_to(config.melee.repo_path))
    result = run_in_repo(["ninja", split_rel], config=config, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to prepare target outputs for {source_file}: "
            f"{result.stderr or result.stdout}"
        )
    if not asm_path.exists() and not obj_path.exists():
        raise RuntimeError(
            f"ninja succeeded but target outputs were not found for {source_file}"
        )


def _load_target_asm_text(source_file: str, config: Config) -> str:
    """Load target assembly for a translation unit.

    Prefer dtk's split .s file when present. If the TU does not have a materialized
    asm file, fall back to disassembling the target object.
    """
    _ensure_target_split_outputs(source_file, config)
    asm_path = _source_to_asm_path(source_file, config)
    if asm_path.exists():
        return asm_path.read_text(encoding="utf-8", errors="replace")

    obj_path = _source_to_obj_path(source_file, config)
    if not obj_path.exists():
        raise RuntimeError(f"Target object not found for {source_file}: {obj_path}")

    from decomp_agent.tools.disasm import disassemble_object

    return disassemble_object(obj_path, config)


def _materialize_target_asm_file(source_file: str, config: Config) -> tuple[Path, bool]:
    """Return a filesystem path to target asm for m2c.

    If the TU split asm file exists, use it directly. Otherwise disassemble the
    target object and spill it to a temporary file for m2c consumption.
    """
    _ensure_target_split_outputs(source_file, config)
    asm_path = _source_to_asm_path(source_file, config)
    if asm_path.exists():
        return asm_path, False

    asm_text = _load_target_asm_text(source_file, config)
    tmp = tempfile.NamedTemporaryFile(
        suffix=f"_{Path(source_file).stem}.s",
        delete=False,
        mode="w",
        encoding="utf-8",
    )
    try:
        tmp.write(asm_text)
        tmp.flush()
    finally:
        tmp.close()
    return Path(tmp.name), True


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
    asm_content = _load_target_asm_text(source_file, config)
    return extract_function_asm(asm_content, function_name)


def get_full_asm(source_file: str, config: Config) -> str:
    """Get the full assembly file for a translation unit.

    Returns the entire .s file content.

    Raises:
        RuntimeError: If the assembly file cannot be built.
    """
    return _load_target_asm_text(source_file, config)


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

    # m2ctx.py runs the C preprocessor, which needs the build toolchain.
    # This needs to run in Docker if enabled.
    result = run_in_repo(
        ["python3", str(m2ctx_script), "--quiet", "--preprocessor"],
        config=config,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"m2ctx.py failed: {result.stderr or result.stdout}"
        )


# Supported optional m2c flags that the agent can request.
# Maps flag names to their m2c CLI representations.
_SUPPORTED_M2C_FLAGS: dict[str, list[str]] = {
    "no_casts": ["--no-casts"],
    "stack_structs": ["--stack-structs"],
    "globals_none": ["--globals", "none"],
    "globals_all": ["--globals", "all"],
    "void": ["--void"],
    "no_andor": ["--no-andor"],
    "no_switches": ["--no-switches"],
    "no_unk_inference": ["--no-unk-inference"],
}


def _build_extra_flags(
    flags: list[str] | None,
    union_fields: list[str] | None,
) -> list[str]:
    """Convert agent-friendly flag names to m2c CLI arguments.

    Args:
        flags: List of flag names from _SUPPORTED_M2C_FLAGS
        union_fields: List of "StructName:field_name" for --union-field

    Returns:
        List of CLI arguments to append to the m2c command.

    Raises:
        ValueError: If an unsupported flag name is provided.
    """
    extra: list[str] = []
    for flag in flags or []:
        if flag not in _SUPPORTED_M2C_FLAGS:
            raise ValueError(
                f"Unsupported m2c flag '{flag}'. "
                f"Supported flags: {sorted(_SUPPORTED_M2C_FLAGS.keys())}"
            )
        extra.extend(_SUPPORTED_M2C_FLAGS[flag])

    for uf in union_fields or []:
        if ":" not in uf:
            raise ValueError(
                f"Invalid --union-field format '{uf}'. "
                f"Expected 'StructName:field_name'."
            )
        extra.extend(["--union-field", uf])

    return extra


def run_m2c(
    function_name: str,
    source_file: str,
    config: Config,
    *,
    regenerate_ctx: bool = False,
    flags: list[str] | None = None,
    union_fields: list[str] | None = None,
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
        flags: Optional list of flag names (e.g. ["no_casts", "stack_structs"])
        union_fields: Optional list of "StructName:field_name" for --union-field
    """
    # Ensure target asm is available (raises RuntimeError on failure)
    asm_path, cleanup_asm = _materialize_target_asm_file(source_file, config)

    # Optionally regenerate m2c context (raises on failure)
    if regenerate_ctx:
        generate_m2c_context(config)

    # Build extra flags from agent-friendly names
    extra_flags = _build_extra_flags(flags, union_fields)

    # Build m2c command
    ctx_path = _ctx_file_path(config)
    m2c_executable = shutil.which("m2c")
    if not m2c_executable:
        if cleanup_asm:
            asm_path.unlink(missing_ok=True)
        return M2CResult(
            function_name=function_name,
            error="m2c not found on PATH",
        )

    m2c_args = [
        m2c_executable,
        "--knr", "--pointer", "left",
        "--target", "ppc-mwcc-c",
    ]

    # Add context if available
    if ctx_path.exists():
        m2c_args.extend(["--context", str(ctx_path)])

    # Add optional flags before function/file args
    m2c_args.extend(extra_flags)

    # Add function name and asm file
    m2c_args.extend(["--function", function_name, str(asm_path)])

    # Run m2c on the host, not in Docker — m2c is a pure Python tool
    # that just reads the .s file and doesn't need the build toolchain.
    try:
        result = subprocess.run(
            m2c_args,
            cwd=config.melee.repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return M2CResult(
            function_name=function_name,
            error="m2c timed out",
        )
    except FileNotFoundError:
        return M2CResult(
            function_name=function_name,
            error="m2c executable not found",
        )
    finally:
        if cleanup_asm:
            asm_path.unlink(missing_ok=True)

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
