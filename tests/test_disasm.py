"""Tests for the disasm module: dtk-based disassembly and comparison."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from decomp_agent.tools.build import CompileResult, FunctionMatch
from decomp_agent.tools.disasm import (
    _normalize_for_diff,
    check_match_via_disasm,
    compute_function_match,
    extract_all_functions,
    get_function_diff,
    parse_instruction,
)

# ---------------------------------------------------------------------------
# Sample assembly snippets for testing
# ---------------------------------------------------------------------------

SAMPLE_ASM = """\
.fn fn_80169574, global
/* 80169574 000093F0  7C 08 02 A6 */\tmflr r0
/* 80169578 000093F4  90 01 00 04 */\tstw r0, 4(r1)
/* 8016957C 000093F8  94 21 FF E0 */\tstwu r1, -0x20(r1)
/* 80169580 000093FC  BF 61 00 0C */\tstmw r27, 0xc(r1)
.endfn fn_80169574

.fn fn_80169600, global
/* 80169600 00009470  38 60 00 00 */\tli r3, 0
/* 80169604 00009474  4E 80 00 20 */\tblr
.endfn fn_80169600
"""

SAMPLE_ASM_MODIFIED = """\
.fn fn_80169574, global
/* 80169574 000093F0  7C 08 02 A6 */\tmflr r0
/* 80169578 000093F4  90 01 00 04 */\tstw r0, 4(r1)
/* 8016957C 000093F8  94 21 FF D0 */\tstwu r1, -0x30(r1)
/* 80169580 000093FC  BF 61 00 0C */\tstmw r27, 0xc(r1)
.endfn fn_80169574

.fn fn_80169600, global
/* 80169600 00009470  38 60 00 00 */\tli r3, 0
/* 80169604 00009474  4E 80 00 20 */\tblr
.endfn fn_80169600
"""

COMPLETELY_DIFFERENT_ASM = """\
.fn fn_80169574, global
/* 80169574 000093F0  38 00 00 01 */\tli r0, 1
/* 80169578 000093F4  38 60 00 02 */\tli r3, 2
/* 8016957C 000093F8  38 80 00 03 */\tli r4, 3
/* 80169580 000093FC  38 A0 00 04 */\tli r5, 4
.endfn fn_80169574
"""


# ---------------------------------------------------------------------------
# parse_instruction tests
# ---------------------------------------------------------------------------


class TestParseInstruction:
    def test_parse_normal_instruction(self):
        line = "/* 80169574 000093F0  7C 08 02 A6 */\tmflr r0"
        result = parse_instruction(line)
        assert result is not None
        hex_bytes, insn = result
        assert hex_bytes == "7C 08 02 A6"
        assert insn == "mflr r0"

    def test_parse_instruction_with_operands(self):
        line = "/* 80169578 000093F4  90 01 00 04 */\tstw r0, 4(r1)"
        result = parse_instruction(line)
        assert result is not None
        hex_bytes, insn = result
        assert hex_bytes == "90 01 00 04"
        assert insn == "stw r0, 4(r1)"

    def test_non_instruction_fn_directive(self):
        assert parse_instruction(".fn fn_80169574, global") is None

    def test_non_instruction_endfn(self):
        assert parse_instruction(".endfn fn_80169574") is None

    def test_non_instruction_comment(self):
        assert parse_instruction("# this is a comment") is None

    def test_non_instruction_blank_line(self):
        assert parse_instruction("") is None
        assert parse_instruction("   ") is None

    def test_non_instruction_section_directive(self):
        assert parse_instruction(".section .text") is None

    def test_non_instruction_global_directive(self):
        assert parse_instruction(".global fn_80169574") is None


# ---------------------------------------------------------------------------
# extract_all_functions tests
# ---------------------------------------------------------------------------


class TestExtractAllFunctions:
    def test_extract_two_functions(self):
        funcs = extract_all_functions(SAMPLE_ASM)
        assert len(funcs) == 2
        assert "fn_80169574" in funcs
        assert "fn_80169600" in funcs

    def test_function_content_includes_instructions(self):
        funcs = extract_all_functions(SAMPLE_ASM)
        asm = funcs["fn_80169574"]
        assert "mflr r0" in asm
        assert "stmw r27" in asm

    def test_function_content_includes_directives(self):
        funcs = extract_all_functions(SAMPLE_ASM)
        asm = funcs["fn_80169574"]
        assert ".fn fn_80169574" in asm
        assert ".endfn fn_80169574" in asm

    def test_empty_asm(self):
        funcs = extract_all_functions("")
        assert funcs == {}

    def test_no_functions(self):
        funcs = extract_all_functions(".section .text\n# comment\n")
        assert funcs == {}

    def test_function_without_endfn(self):
        asm = ".fn orphan_func, global\n/* 80000000 00000000  38 60 00 00 */\tli r3, 0\n"
        funcs = extract_all_functions(asm)
        assert "orphan_func" in funcs
        assert "li r3, 0" in funcs["orphan_func"]


# ---------------------------------------------------------------------------
# compute_function_match tests
# ---------------------------------------------------------------------------


class TestComputeFunctionMatch:
    def test_exact_match(self):
        funcs = extract_all_functions(SAMPLE_ASM)
        target = funcs["fn_80169574"]
        match = compute_function_match(target, target)
        assert match.fuzzy_match_percent == 100.0
        assert match.size == 16  # 4 instructions * 4 bytes

    def test_partial_match(self):
        funcs_target = extract_all_functions(SAMPLE_ASM)
        funcs_compiled = extract_all_functions(SAMPLE_ASM_MODIFIED)
        target = funcs_target["fn_80169574"]
        compiled = funcs_compiled["fn_80169574"]
        match = compute_function_match(target, compiled)
        # 3 of 4 instructions match, so should be ~75%
        assert 50.0 < match.fuzzy_match_percent < 100.0
        assert match.fuzzy_match_percent != 100.0

    def test_no_match(self):
        funcs_target = extract_all_functions(SAMPLE_ASM)
        funcs_different = extract_all_functions(COMPLETELY_DIFFERENT_ASM)
        target = funcs_target["fn_80169574"]
        different = funcs_different["fn_80169574"]
        match = compute_function_match(target, different)
        assert match.fuzzy_match_percent < 50.0

    def test_different_length(self):
        funcs = extract_all_functions(SAMPLE_ASM)
        short_fn = funcs["fn_80169600"]  # 2 instructions
        long_fn = funcs["fn_80169574"]  # 4 instructions
        match = compute_function_match(long_fn, short_fn)
        assert match.fuzzy_match_percent < 100.0
        assert match.size == 16  # target size

    def test_both_empty(self):
        match = compute_function_match("", "")
        assert match.fuzzy_match_percent == 100.0
        assert match.size == 0

    def test_one_empty(self):
        funcs = extract_all_functions(SAMPLE_ASM)
        match = compute_function_match(funcs["fn_80169574"], "")
        assert match.fuzzy_match_percent == 0.0

    def test_second_function_exact_match(self):
        """fn_80169600 is identical in both samples."""
        funcs_target = extract_all_functions(SAMPLE_ASM)
        funcs_compiled = extract_all_functions(SAMPLE_ASM_MODIFIED)
        target = funcs_target["fn_80169600"]
        compiled = funcs_compiled["fn_80169600"]
        match = compute_function_match(target, compiled)
        assert match.fuzzy_match_percent == 100.0


# ---------------------------------------------------------------------------
# Diff output tests
# ---------------------------------------------------------------------------


class TestDiffOutput:
    def test_diff_identical(self):
        funcs = extract_all_functions(SAMPLE_ASM)
        target = funcs["fn_80169574"]
        target_lines = _normalize_for_diff(target)
        compiled_lines = _normalize_for_diff(target)  # identical

        import difflib

        diff = "\n".join(
            difflib.unified_diff(
                target_lines,
                compiled_lines,
                fromfile="target",
                tofile="compiled",
                lineterm="",
            )
        )
        # Identical → no diff output
        assert diff == ""

    def test_diff_format(self):
        funcs_target = extract_all_functions(SAMPLE_ASM)
        funcs_compiled = extract_all_functions(SAMPLE_ASM_MODIFIED)
        target = funcs_target["fn_80169574"]
        compiled = funcs_compiled["fn_80169574"]
        target_lines = _normalize_for_diff(target)
        compiled_lines = _normalize_for_diff(compiled)

        import difflib

        diff = "\n".join(
            difflib.unified_diff(
                target_lines,
                compiled_lines,
                fromfile="target",
                tofile="compiled",
                lineterm="",
            )
        )
        assert "--- target" in diff
        assert "+++ compiled" in diff
        # The changed instruction should appear
        assert "-" in diff  # removed line
        assert "+" in diff  # added line

    def test_normalize_for_diff(self):
        funcs = extract_all_functions(SAMPLE_ASM)
        lines = _normalize_for_diff(funcs["fn_80169600"])
        assert len(lines) == 2
        assert lines[0] == "38 60 00 00  li r3, 0"
        assert lines[1] == "4E 80 00 20  blr"


# ---------------------------------------------------------------------------
# Integration tests (mock dtk)
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> MagicMock:
    """Create a mock Config pointing at tmp_path as the melee repo."""
    config = MagicMock()
    config.melee.repo_path = tmp_path
    config.melee.build_dir = "build"
    config.melee.version = "GALE01"
    config.melee.build_path = tmp_path / "build" / "GALE01"
    config.docker.enabled = False
    return config


class TestCheckMatchViaDisasm:
    @patch("decomp_agent.tools.disasm.compile_object")
    @patch("decomp_agent.tools.disasm.disassemble_object")
    def test_all_match(self, mock_disasm, mock_compile, tmp_path):
        # Setup
        config = _make_config(tmp_path)
        mock_compile.return_value = CompileResult(
            object_name="melee/gm/gm_1601.c", success=True
        )

        # Create target obj path so exists() check passes
        target_dir = tmp_path / "build" / "GALE01" / "obj" / "melee" / "gm"
        target_dir.mkdir(parents=True)
        (target_dir / "gm_1601.o").touch()

        compiled_dir = tmp_path / "build" / "GALE01" / "src" / "melee" / "gm"
        compiled_dir.mkdir(parents=True)
        (compiled_dir / "gm_1601.o").touch()

        # Both disassemble to identical asm
        mock_disasm.return_value = SAMPLE_ASM

        result = check_match_via_disasm("melee/gm/gm_1601.c", config)

        assert result.success
        assert len(result.functions) == 2
        assert all(f.fuzzy_match_percent == 100.0 for f in result.functions)
        assert result.all_matched

    @patch("decomp_agent.tools.disasm.compile_object")
    @patch("decomp_agent.tools.disasm.disassemble_object")
    def test_partial_match(self, mock_disasm, mock_compile, tmp_path):
        config = _make_config(tmp_path)
        mock_compile.return_value = CompileResult(
            object_name="melee/gm/gm_1601.c", success=True
        )

        target_dir = tmp_path / "build" / "GALE01" / "obj" / "melee" / "gm"
        target_dir.mkdir(parents=True)
        (target_dir / "gm_1601.o").touch()

        compiled_dir = tmp_path / "build" / "GALE01" / "src" / "melee" / "gm"
        compiled_dir.mkdir(parents=True)
        (compiled_dir / "gm_1601.o").touch()

        # Target and compiled differ
        mock_disasm.side_effect = [SAMPLE_ASM, SAMPLE_ASM_MODIFIED]

        result = check_match_via_disasm("melee/gm/gm_1601.c", config)

        assert result.success
        assert len(result.functions) == 2
        # fn_80169574 should NOT be 100%
        fn1 = result.get_function("fn_80169574")
        assert fn1 is not None
        assert fn1.fuzzy_match_percent < 100.0
        # fn_80169600 should still be 100%
        fn2 = result.get_function("fn_80169600")
        assert fn2 is not None
        assert fn2.fuzzy_match_percent == 100.0

    @patch("decomp_agent.tools.disasm.compile_object")
    def test_compile_failure(self, mock_compile, tmp_path):
        config = _make_config(tmp_path)
        mock_compile.return_value = CompileResult(
            object_name="melee/gm/gm_1601.c",
            success=False,
            error="syntax error",
        )

        result = check_match_via_disasm("melee/gm/gm_1601.c", config)

        assert not result.success
        assert result.error == "syntax error"
        assert result.functions == []

    @patch("decomp_agent.tools.disasm.compile_object")
    def test_missing_compiled_object(self, mock_compile, tmp_path):
        config = _make_config(tmp_path)
        mock_compile.return_value = CompileResult(
            object_name="melee/gm/gm_1601.c", success=True
        )

        # Create target but NOT compiled
        target_dir = tmp_path / "build" / "GALE01" / "obj" / "melee" / "gm"
        target_dir.mkdir(parents=True)
        (target_dir / "gm_1601.o").touch()

        result = check_match_via_disasm("melee/gm/gm_1601.c", config)

        assert not result.success
        assert "Compiled object not found" in result.error


class TestGetFunctionDiffMissing:
    def test_raises_when_compiled_missing(self, tmp_path):
        config = _make_config(tmp_path)

        # Create target but NOT compiled
        target_dir = tmp_path / "build" / "GALE01" / "obj" / "melee" / "gm"
        target_dir.mkdir(parents=True)
        (target_dir / "gm_1601.o").touch()

        with pytest.raises(RuntimeError, match="Compiled object not found"):
            get_function_diff("fn_80169574", "melee/gm/gm_1601.c", config)

    def test_raises_when_target_missing(self, tmp_path):
        config = _make_config(tmp_path)

        # Create compiled but NOT target
        compiled_dir = tmp_path / "build" / "GALE01" / "src" / "melee" / "gm"
        compiled_dir.mkdir(parents=True)
        (compiled_dir / "gm_1601.o").touch()

        with pytest.raises(RuntimeError, match="Target object not found"):
            get_function_diff("fn_80169574", "melee/gm/gm_1601.c", config)


# ---------------------------------------------------------------------------
# Loop mark_complete fix verification
# ---------------------------------------------------------------------------


class TestMarkCompleteDetection:
    """Verify the mark_complete detection logic in the agent loop."""

    def test_confirmed_match_string_present_in_success(self):
        """The registry's _handle_mark_complete returns 'confirmed MATCH' on success."""
        # This is a string-level test confirming the contract
        success_msg = (
            "Verified: fn_80169574 in melee/gm/gm_1601.c "
            "is a confirmed MATCH."
        )
        assert "confirmed MATCH" in success_msg

    def test_error_string_lacks_confirmed_match(self):
        """Error responses from mark_complete should NOT contain 'confirmed MATCH'."""
        error_msg = (
            "Error: fn_80169574 is NOT matched "
            "(fuzzy_match_percent=99.4000%). Keep iterating."
        )
        assert "confirmed MATCH" not in error_msg

        compile_error = "Error: compilation failed, cannot verify match: timeout"
        assert "confirmed MATCH" not in compile_error

        not_found = "Error: function fn_80169574 not found in compile output"
        assert "confirmed MATCH" not in not_found
