"""Disassembly and comparison tools using dtk elf disasm.

Provides reliable, non-interactive disassembly and function-level comparison
as a replacement for the broken objdiff-cli (which requires a TTY).
"""

from __future__ import annotations

import difflib
import re
import tempfile
from pathlib import Path

from decomp_agent.config import Config
from decomp_agent.tools.build import CompileResult, FunctionMatch, compile_object
from decomp_agent.tools.m2c_tool import _source_to_obj_path, extract_function_asm
from decomp_agent.tools.run import run_in_repo

# Pattern to parse dtk asm instruction lines:
# /* 80169574 000093F0  7C 08 02 A6 */	mflr r0
_INSTRUCTION_RE = re.compile(
    r"/\*\s+[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+((?:[0-9A-Fa-f]{2}\s?)+)\*/\s+(.*)"
)

# Pattern to match .fn directives
_FN_DIRECTIVE_RE = re.compile(r"^\.fn\s+(\w+)")
_ENDFN_RE = re.compile(r"^\s*\.endfn\s+(\w+)")


def disassemble_object(obj_path: Path, config: Config) -> str:
    """Disassemble an object file using dtk elf disasm.

    Args:
        obj_path: Path to the .o file (relative to repo root or absolute).
        config: Project configuration.

    Returns:
        The disassembled assembly text.

    Raises:
        RuntimeError: If dtk fails or output is missing.
    """
    with tempfile.NamedTemporaryFile(suffix=".s", delete=False) as tmp:
        output_path = Path(tmp.name)

    dtk_path = f"{config.melee.build_dir}/tools/dtk"

    # Make obj_path relative to repo if it's absolute
    if obj_path.is_absolute():
        try:
            obj_rel = str(obj_path.relative_to(config.melee.repo_path))
        except ValueError:
            obj_rel = str(obj_path)
    else:
        obj_rel = str(obj_path)

    result = run_in_repo(
        [dtk_path, "elf", "disasm", obj_rel, str(output_path)],
        config=config,
        timeout=30,
    )

    if result.returncode != 0:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"dtk elf disasm failed for {obj_path}: "
            f"{result.stderr or result.stdout}"
        )

    if not output_path.exists():
        raise RuntimeError(
            f"dtk succeeded but output file not found at {output_path}"
        )

    try:
        asm_text = output_path.read_text(encoding="utf-8", errors="replace")
    finally:
        output_path.unlink(missing_ok=True)

    return asm_text


def extract_all_functions(asm_text: str) -> dict[str, str]:
    """Parse .fn/.endfn directives to extract all functions from disassembly.

    Returns:
        Dict mapping function_name -> assembly text (including .fn/.endfn lines).
    """
    functions: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in asm_text.splitlines():
        fn_match = _FN_DIRECTIVE_RE.match(line)
        if fn_match:
            # Save previous function if any
            if current_name is not None:
                functions[current_name] = "\n".join(current_lines) + "\n"
            current_name = fn_match.group(1)
            current_lines = [line]
            continue

        endfn_match = _ENDFN_RE.match(line)
        if endfn_match and current_name is not None:
            current_lines.append(line)
            functions[current_name] = "\n".join(current_lines) + "\n"
            current_name = None
            current_lines = []
            continue

        if current_name is not None:
            current_lines.append(line)

    # Handle case where last function has no .endfn
    if current_name is not None:
        functions[current_name] = "\n".join(current_lines) + "\n"

    return functions


def parse_instruction(line: str) -> tuple[str, str] | None:
    """Parse a dtk asm instruction line into (hex_bytes, normalized_instruction).

    Example:
        "/* 80169574 000093F0  7C 08 02 A6 */\\tmflr r0"
        -> ("7C 08 02 A6", "mflr r0")

    Returns None for non-instruction lines (.fn, .endfn, comments, blank, etc).
    """
    m = _INSTRUCTION_RE.match(line.strip())
    if m is None:
        return None
    hex_bytes = m.group(1).strip()
    instruction = m.group(2).strip()
    return (hex_bytes, instruction)


def compute_function_match(target_asm: str, compiled_asm: str) -> FunctionMatch:
    """Compare target and compiled assembly to compute match percentage.

    - Exact match: all hex bytes match → 100.0%
    - Fuzzy match: SequenceMatcher on individual machine code bytes → ratio * 100

    Uses byte-level comparison (not instruction-string-level) so that a single
    register difference (e.g. r5 vs r6) only penalizes the 1-2 bytes that
    encode the register, not the entire instruction.

    Args:
        target_asm: Target function assembly text.
        compiled_asm: Compiled function assembly text.

    Returns:
        FunctionMatch with name="", fuzzy_match_percent, and size.
    """
    target_bytes = []
    for line in target_asm.splitlines():
        parsed = parse_instruction(line)
        if parsed:
            target_bytes.append(parsed[0])

    compiled_bytes = []
    for line in compiled_asm.splitlines():
        parsed = parse_instruction(line)
        if parsed:
            compiled_bytes.append(parsed[0])

    size = len(target_bytes) * 4

    if not target_bytes and not compiled_bytes:
        return FunctionMatch(name="", fuzzy_match_percent=100.0, size=0)

    if not target_bytes or not compiled_bytes:
        return FunctionMatch(name="", fuzzy_match_percent=0.0, size=size)

    # Exact match: compare raw hex bytes per instruction
    if target_bytes == compiled_bytes:
        return FunctionMatch(name="", fuzzy_match_percent=100.0, size=size)

    # Fuzzy match: compare at the individual byte level so a single register
    # difference only costs 1-2 bytes, not an entire instruction.
    target_flat = [b for insn in target_bytes for b in insn.split()]
    compiled_flat = [b for insn in compiled_bytes for b in insn.split()]
    ratio = difflib.SequenceMatcher(None, target_flat, compiled_flat).ratio()
    return FunctionMatch(
        name="", fuzzy_match_percent=round(ratio * 100, 4), size=size
    )


def get_function_diff(
    function_name: str, source_file: str, config: Config
) -> str:
    """Get a unified diff between target and compiled assembly for a function.

    Args:
        function_name: Name of the function to diff.
        source_file: Object name e.g. "melee/gm/gm_1601.c"
        config: Project configuration.

    Returns:
        Unified diff string with --- target / +++ compiled headers.

    Raises:
        RuntimeError: If compiled .o doesn't exist, or function not found.
    """
    # Locate object files
    target_obj = _source_to_obj_path(source_file, config)
    stem = source_file.rsplit(".", 1)[0]
    compiled_obj = (
        config.melee.repo_path
        / config.melee.build_dir
        / config.melee.version
        / "src"
        / f"{stem}.o"
    )

    if not target_obj.exists():
        raise RuntimeError(
            f"Target object not found: {target_obj}. "
            f"Run ninja to generate target objects."
        )

    if not compiled_obj.exists():
        raise RuntimeError(
            f"Compiled object not found: {compiled_obj}. "
            f"Compile first with compile_and_check."
        )

    # Disassemble both
    target_asm = disassemble_object(target_obj, config)
    compiled_asm = disassemble_object(compiled_obj, config)

    # Extract the specific function
    target_func = extract_function_asm(target_asm, function_name)
    if target_func is None:
        raise RuntimeError(
            f"Function '{function_name}' not found in target object {target_obj}"
        )

    compiled_func = extract_function_asm(compiled_asm, function_name)
    if compiled_func is None:
        raise RuntimeError(
            f"Function '{function_name}' not found in compiled object {compiled_obj}"
        )

    # Normalize: extract just instructions for diffing
    target_lines = _normalize_for_diff(target_func)
    compiled_lines = _normalize_for_diff(compiled_func)

    diff = difflib.unified_diff(
        target_lines,
        compiled_lines,
        fromfile="target",
        tofile="compiled",
        lineterm="",
    )
    return "\n".join(diff)


def _normalize_for_diff(asm_text: str) -> list[str]:
    """Normalize assembly text for diffing: keep hex bytes + instruction."""
    lines = []
    for line in asm_text.splitlines():
        parsed = parse_instruction(line)
        if parsed:
            hex_b, insn = parsed
            lines.append(f"{hex_b}  {insn}")
    return lines


def check_match_via_disasm(
    object_name: str, config: Config
) -> CompileResult:
    """Compile a single object and check match via dtk disassembly.

    Replaces the old report.json-based approach. Compiles the object,
    disassembles both target and compiled .o, and compares per-function.

    Args:
        object_name: Object name e.g. "melee/lb/lbcommand.c"
        config: Project configuration.

    Returns:
        CompileResult with per-function match data.
    """
    # First compile
    compile_result = compile_object(object_name, config)
    if not compile_result.success:
        return compile_result

    # Locate object files
    target_obj = _source_to_obj_path(object_name, config)
    stem = object_name.rsplit(".", 1)[0]
    compiled_obj = (
        config.melee.repo_path
        / config.melee.build_dir
        / config.melee.version
        / "src"
        / f"{stem}.o"
    )

    if not target_obj.exists():
        compile_result.error = f"Target object not found: {target_obj}"
        compile_result.success = False
        return compile_result

    if not compiled_obj.exists():
        compile_result.error = f"Compiled object not found: {compiled_obj}"
        compile_result.success = False
        return compile_result

    # Disassemble both
    try:
        target_asm = disassemble_object(target_obj, config)
        compiled_asm = disassemble_object(compiled_obj, config)
    except RuntimeError as e:
        compile_result.error = str(e)
        compile_result.success = False
        return compile_result

    # Extract all functions from target
    target_functions = extract_all_functions(target_asm)
    compiled_functions = extract_all_functions(compiled_asm)

    for func_name, target_func_asm in target_functions.items():
        compiled_func_asm = compiled_functions.get(func_name)
        if compiled_func_asm is None:
            compile_result.functions.append(
                FunctionMatch(name=func_name, fuzzy_match_percent=0.0, size=0)
            )
            continue

        match = compute_function_match(target_func_asm, compiled_func_asm)
        compile_result.functions.append(
            FunctionMatch(
                name=func_name,
                fuzzy_match_percent=match.fuzzy_match_percent,
                size=match.size,
            )
        )

    return compile_result
