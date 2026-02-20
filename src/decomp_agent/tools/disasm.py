"""Disassembly and comparison tools using dtk elf disasm.

Provides reliable, non-interactive disassembly and function-level comparison
as a replacement for the broken objdiff-cli (which requires a TTY).
"""

from __future__ import annotations

import difflib
import os
import re
import tempfile
from dataclasses import dataclass, field
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

# ---------------------------------------------------------------------------
# Dataclasses for diff analysis
# ---------------------------------------------------------------------------


@dataclass
class InstructionPair:
    """A pair of aligned instructions from target and compiled."""

    index: int
    target_hex: str
    compiled_hex: str
    target_insn: str
    compiled_insn: str
    mismatch_type: str  # "match", "phantom", "register", "opcode", "extra_target", "extra_compiled"


@dataclass
class DiffAnalysis:
    """Result of analyzing the diff between target and compiled assembly."""

    total: int  # total instruction count (max of target, compiled)
    matching: int  # byte-identical instructions
    phantom: int  # same bytes, different symbol text
    register_only: int  # same mnemonic, different register operands
    opcode_diffs: int  # different mnemonics
    extra_target: int  # instructions only in target
    extra_compiled: int  # instructions only in compiled
    pairs: list[InstructionPair] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core disassembly functions
# ---------------------------------------------------------------------------


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
    # When Docker is enabled, the output file must be inside the repo
    # (bind-mounted) so both the container and host can access it.
    # Host /tmp is not visible inside the container.
    if config.docker.enabled:
        # Use a unique filename to avoid races with concurrent workers
        unique = f"_dtk_disasm_{os.getpid()}_{id(obj_path)}.s"
        output_path = config.melee.repo_path / "build" / unique
    else:
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

    # Output path must be relative to repo for Docker
    if config.docker.enabled:
        out_arg = str(output_path.relative_to(config.melee.repo_path))
    else:
        out_arg = str(output_path)

    result = run_in_repo(
        [dtk_path, "elf", "disasm", obj_rel, out_arg],
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


# ---------------------------------------------------------------------------
# Diff analysis helpers
# ---------------------------------------------------------------------------


def _extract_mnemonic(insn_text: str) -> str:
    """Get the opcode mnemonic from instruction text.

    Examples:
        "mflr r0" -> "mflr"
        "stw r0, 4(r1)" -> "stw"
        "lis r3, lbSnap_803BACC8@ha" -> "lis"
        "" -> ""
    """
    parts = insn_text.strip().split(None, 1)
    return parts[0] if parts else ""


def _parse_asm_to_tuples(asm_text: str) -> list[tuple[str, str]]:
    """Parse assembly text into list of (hex_bytes, instruction_text) tuples."""
    result = []
    for line in asm_text.splitlines():
        parsed = parse_instruction(line)
        if parsed:
            result.append(parsed)
    return result


def _align_and_classify(
    target_parsed: list[tuple[str, str]],
    compiled_parsed: list[tuple[str, str]],
) -> DiffAnalysis:
    """Align target and compiled instructions using SequenceMatcher on hex bytes,
    then classify each pair.

    Returns a DiffAnalysis with all counts and classified pairs.
    """
    target_hex = [t[0] for t in target_parsed]
    compiled_hex = [c[0] for c in compiled_parsed]

    matcher = difflib.SequenceMatcher(None, target_hex, compiled_hex)
    pairs: list[InstructionPair] = []

    matching = 0
    phantom = 0
    register_only = 0
    opcode_diffs = 0
    extra_target = 0
    extra_compiled = 0
    idx = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for ti, ci in zip(range(i1, i2), range(j1, j2)):
                t_hex, t_insn = target_parsed[ti]
                c_hex, c_insn = compiled_parsed[ci]
                # Even "equal" hex may have different symbol text — that's a phantom
                if t_insn != c_insn:
                    mtype = "phantom"
                    phantom += 1
                else:
                    mtype = "match"
                matching += 1
                pairs.append(InstructionPair(
                    index=idx, target_hex=t_hex, compiled_hex=c_hex,
                    target_insn=t_insn, compiled_insn=c_insn,
                    mismatch_type=mtype,
                ))
                idx += 1
        elif tag == "replace":
            # Pair up replacements; handle length differences
            t_range = list(range(i1, i2))
            c_range = list(range(j1, j2))
            paired = min(len(t_range), len(c_range))
            for k in range(paired):
                t_hex, t_insn = target_parsed[t_range[k]]
                c_hex, c_insn = compiled_parsed[c_range[k]]
                t_mnem = _extract_mnemonic(t_insn)
                c_mnem = _extract_mnemonic(c_insn)
                if t_hex == c_hex:
                    # Same bytes but landed in a replace block — phantom
                    mtype = "phantom"
                    phantom += 1
                    matching += 1
                elif t_mnem == c_mnem:
                    mtype = "register"
                    register_only += 1
                else:
                    mtype = "opcode"
                    opcode_diffs += 1
                pairs.append(InstructionPair(
                    index=idx, target_hex=t_hex, compiled_hex=c_hex,
                    target_insn=t_insn, compiled_insn=c_insn,
                    mismatch_type=mtype,
                ))
                idx += 1
            # Remaining unpaired from target
            for k in range(paired, len(t_range)):
                t_hex, t_insn = target_parsed[t_range[k]]
                extra_target += 1
                pairs.append(InstructionPair(
                    index=idx, target_hex=t_hex, compiled_hex="",
                    target_insn=t_insn, compiled_insn="",
                    mismatch_type="extra_target",
                ))
                idx += 1
            # Remaining unpaired from compiled
            for k in range(paired, len(c_range)):
                c_hex, c_insn = compiled_parsed[c_range[k]]
                extra_compiled += 1
                pairs.append(InstructionPair(
                    index=idx, target_hex="", compiled_hex=c_hex,
                    target_insn="", compiled_insn=c_insn,
                    mismatch_type="extra_compiled",
                ))
                idx += 1
        elif tag == "delete":
            for ti in range(i1, i2):
                t_hex, t_insn = target_parsed[ti]
                extra_target += 1
                pairs.append(InstructionPair(
                    index=idx, target_hex=t_hex, compiled_hex="",
                    target_insn=t_insn, compiled_insn="",
                    mismatch_type="extra_target",
                ))
                idx += 1
        elif tag == "insert":
            for ci in range(j1, j2):
                c_hex, c_insn = compiled_parsed[ci]
                extra_compiled += 1
                pairs.append(InstructionPair(
                    index=idx, target_hex="", compiled_hex=c_hex,
                    target_insn="", compiled_insn=c_insn,
                    mismatch_type="extra_compiled",
                ))
                idx += 1

    total = max(len(target_parsed), len(compiled_parsed))

    return DiffAnalysis(
        total=total,
        matching=matching,
        phantom=phantom,
        register_only=register_only,
        opcode_diffs=opcode_diffs,
        extra_target=extra_target,
        extra_compiled=extra_compiled,
        pairs=pairs,
    )


def _format_diff_analysis(analysis: DiffAnalysis) -> str:
    """Produce the final diff output string: summary + instruction diff.

    Shows basic counts and the raw annotated diff. No classification or
    diagnosis — the model should reason about the assembly directly.
    """
    diff_count = (
        analysis.register_only + analysis.opcode_diffs
        + analysis.extra_target + analysis.extra_compiled
    )
    if diff_count == 0:
        return "All instructions match."

    lines: list[str] = []

    lines.append(
        f"{analysis.total} instructions total, "
        f"{analysis.matching} match, {diff_count} differ"
    )

    if analysis.phantom > 0:
        lines.append(f"({analysis.phantom} phantom diffs filtered — same bytes, different symbols)")

    lines.append("")

    # Format pairs with context collapsing
    consecutive_matches = 0
    deferred_match_count = 0
    i = 0
    while i < len(analysis.pairs):
        pair = analysis.pairs[i]

        if pair.mismatch_type in ("match", "phantom"):
            # Count consecutive matches
            run_start = i
            while (
                i < len(analysis.pairs)
                and analysis.pairs[i].mismatch_type in ("match", "phantom")
            ):
                i += 1
            run_len = i - run_start

            if run_len <= 3:
                # Show all context lines
                for j in range(run_start, i):
                    p = analysis.pairs[j]
                    lines.append(f"  {p.target_hex}  {p.target_insn}")
            else:
                # Show first, collapse middle, show last
                p = analysis.pairs[run_start]
                lines.append(f"  {p.target_hex}  {p.target_insn}")
                collapsed = run_len - 2
                lines.append(f"  ... ({collapsed} matching instructions) ...")
                p = analysis.pairs[i - 1]
                lines.append(f"  {p.target_hex}  {p.target_insn}")
        elif pair.mismatch_type == "register":
            lines.append(f"- {pair.target_hex}  {pair.target_insn}                [register]")
            lines.append(f"+ {pair.compiled_hex}  {pair.compiled_insn}")
            i += 1
        elif pair.mismatch_type == "opcode":
            lines.append(f"- {pair.target_hex}  {pair.target_insn}                [opcode]")
            lines.append(f"+ {pair.compiled_hex}  {pair.compiled_insn}")
            i += 1
        elif pair.mismatch_type == "extra_target":
            lines.append(f"- {pair.target_hex}  {pair.target_insn}                [missing]")
            i += 1
        elif pair.mismatch_type == "extra_compiled":
            lines.append(f"+ {pair.compiled_hex}  {pair.compiled_insn}                [extra]")
            i += 1
        else:
            i += 1

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Match computation
# ---------------------------------------------------------------------------


def compute_function_match(target_asm: str, compiled_asm: str) -> FunctionMatch:
    """Compare target and compiled assembly to compute match percentage.

    - Exact match: all hex bytes match -> 100.0%
    - Fuzzy match: SequenceMatcher on individual machine code bytes -> ratio * 100

    Also computes structural_match_percent (mnemonic-level match) and
    mismatch_type classification.

    Uses byte-level comparison (not instruction-string-level) so that a single
    register difference (e.g. r5 vs r6) only penalizes the 1-2 bytes that
    encode the register, not the entire instruction.

    Args:
        target_asm: Target function assembly text.
        compiled_asm: Compiled function assembly text.

    Returns:
        FunctionMatch with name="", fuzzy_match_percent, size,
        structural_match_percent, and mismatch_type.
    """
    target_parsed = _parse_asm_to_tuples(target_asm)
    compiled_parsed = _parse_asm_to_tuples(compiled_asm)

    target_bytes = [t[0] for t in target_parsed]
    compiled_bytes = [c[0] for c in compiled_parsed]

    size = len(target_bytes) * 4

    if not target_bytes and not compiled_bytes:
        return FunctionMatch(
            name="", fuzzy_match_percent=100.0, size=0,
            structural_match_percent=100.0, mismatch_type="",
        )

    if not target_bytes or not compiled_bytes:
        return FunctionMatch(
            name="", fuzzy_match_percent=0.0, size=size,
            structural_match_percent=0.0, mismatch_type="structural",
        )

    # Exact match: compare raw hex bytes per instruction
    if target_bytes == compiled_bytes:
        return FunctionMatch(
            name="", fuzzy_match_percent=100.0, size=size,
            structural_match_percent=100.0, mismatch_type="",
        )

    # Fuzzy match: compare at the individual byte level so a single register
    # difference only costs 1-2 bytes, not an entire instruction.
    target_flat = [b for insn in target_bytes for b in insn.split()]
    compiled_flat = [b for insn in compiled_bytes for b in insn.split()]
    ratio = difflib.SequenceMatcher(None, target_flat, compiled_flat).ratio()

    # Structural match: compare mnemonic sequences
    target_mnemonics = [_extract_mnemonic(t[1]) for t in target_parsed]
    compiled_mnemonics = [_extract_mnemonic(c[1]) for c in compiled_parsed]

    if target_mnemonics == compiled_mnemonics:
        structural_pct = 100.0
        mismatch_type = "register_only"
    else:
        structural_ratio = difflib.SequenceMatcher(
            None, target_mnemonics, compiled_mnemonics
        ).ratio()
        structural_pct = round(structural_ratio * 100, 4)
        # Classify based on the analysis
        if structural_pct == 100.0:
            mismatch_type = "register_only"
        elif len(target_mnemonics) != len(compiled_mnemonics):
            mismatch_type = "structural"
        else:
            mismatch_type = "opcode"

    return FunctionMatch(
        name="",
        fuzzy_match_percent=round(ratio * 100, 4),
        size=size,
        structural_match_percent=structural_pct,
        mismatch_type=mismatch_type,
    )


# ---------------------------------------------------------------------------
# Diff output (public API)
# ---------------------------------------------------------------------------


def get_function_diff(
    function_name: str, source_file: str, config: Config
) -> str:
    """Get an analyzed diff between target and compiled assembly for a function.

    Returns a structured diff with:
    - Summary header: instruction counts, mismatch classification, register swaps
    - Instruction diff: aligned pairs with [register]/[opcode]/[missing]/[extra] tags
    - Phantom diffs (same bytes, different symbols) are filtered out

    Args:
        function_name: Name of the function to diff.
        source_file: Object name e.g. "melee/gm/gm_1601.c"
        config: Project configuration.

    Returns:
        Analyzed diff string with summary + instruction diff.

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

    # Parse and analyze
    target_parsed = _parse_asm_to_tuples(target_func)
    compiled_parsed = _parse_asm_to_tuples(compiled_func)
    analysis = _align_and_classify(target_parsed, compiled_parsed)

    return _format_diff_analysis(analysis)


def _normalize_for_diff(asm_text: str) -> list[str]:
    """Normalize assembly text for diffing: keep hex bytes + instruction."""
    lines = []
    for line in asm_text.splitlines():
        parsed = parse_instruction(line)
        if parsed:
            hex_b, insn = parsed
            lines.append(f"{hex_b}  {insn}")
    return lines


# ---------------------------------------------------------------------------
# Integration: compile + disassemble + compare
# ---------------------------------------------------------------------------


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
                structural_match_percent=match.structural_match_percent,
                mismatch_type=match.mismatch_type,
            )
        )

    return compile_result
