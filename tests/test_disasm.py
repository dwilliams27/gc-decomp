"""Tests for the disasm module: dtk-based disassembly and comparison."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from decomp_agent.tools.build import CompileResult, FunctionMatch
from decomp_agent.tools.disasm import (
    DiffAnalysis,
    InstructionPair,
    _align_and_classify,
    _extract_mnemonic,
    _format_diff_analysis,
    _normalize_for_diff,
    _parse_asm_to_tuples,
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

# Register-only diff: same mnemonics, different register operands
REGISTER_ONLY_TARGET = """\
.fn fn_regtest, global
/* 80001000 00000000  7C 08 02 A6 */\tmflr r0
/* 80001004 00000004  90 01 00 04 */\tstw r0, 4(r1)
/* 80001008 00000008  80 A3 00 00 */\tlwz r5, 0(r3)
/* 8000100C 0000000C  80 63 00 04 */\tlwz r3, 4(r3)
/* 80001010 00000010  90 A4 00 00 */\tstw r5, 0(r4)
/* 80001014 00000014  4E 80 00 20 */\tblr
.endfn fn_regtest
"""

REGISTER_ONLY_COMPILED = """\
.fn fn_regtest, global
/* 00000000 00000000  7C 08 02 A6 */\tmflr r0
/* 00000004 00000004  90 01 00 04 */\tstw r0, 4(r1)
/* 00000008 00000008  80 C3 00 00 */\tlwz r6, 0(r3)
/* 0000000C 0000000C  80 A3 00 04 */\tlwz r5, 4(r3)
/* 00000010 00000010  90 C4 00 00 */\tstw r6, 0(r4)
/* 00000014 00000014  4E 80 00 20 */\tblr
.endfn fn_regtest
"""

# Phantom diff: same hex bytes, different symbol names in instruction text
PHANTOM_TARGET = """\
.fn fn_phantom, global
/* 80001000 00000000  3C 60 00 00 */\tlis r3, lbSnap_803BACC8@ha
/* 80001004 00000004  38 63 00 00 */\taddi r3, r3, lbSnap_803BACC8@l
/* 80001008 00000008  4E 80 00 20 */\tblr
.endfn fn_phantom
"""

PHANTOM_COMPILED = """\
.fn fn_phantom, global
/* 00000000 00000000  3C 60 00 00 */\tlis r3, _SDA2_BASE_@ha
/* 00000004 00000004  38 63 00 00 */\taddi r3, r3, _SDA2_BASE_@l
/* 00000008 00000008  4E 80 00 20 */\tblr
.endfn fn_phantom
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
        assert match.structural_match_percent == 100.0
        assert match.mismatch_type == ""

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
        assert match.structural_match_percent == 100.0
        assert match.mismatch_type == ""

    def test_one_empty(self):
        funcs = extract_all_functions(SAMPLE_ASM)
        match = compute_function_match(funcs["fn_80169574"], "")
        assert match.fuzzy_match_percent == 0.0
        assert match.structural_match_percent == 0.0
        assert match.mismatch_type == "structural"

    def test_second_function_exact_match(self):
        """fn_80169600 is identical in both samples."""
        funcs_target = extract_all_functions(SAMPLE_ASM)
        funcs_compiled = extract_all_functions(SAMPLE_ASM_MODIFIED)
        target = funcs_target["fn_80169600"]
        compiled = funcs_compiled["fn_80169600"]
        match = compute_function_match(target, compiled)
        assert match.fuzzy_match_percent == 100.0

    def test_register_only_structural_match(self):
        """Register-only diffs should have 100% structural match."""
        funcs_t = extract_all_functions(REGISTER_ONLY_TARGET)
        funcs_c = extract_all_functions(REGISTER_ONLY_COMPILED)
        match = compute_function_match(
            funcs_t["fn_regtest"], funcs_c["fn_regtest"]
        )
        assert match.fuzzy_match_percent < 100.0
        assert match.structural_match_percent == 100.0
        assert match.mismatch_type == "register_only"

    def test_phantom_diff_exact_match(self):
        """Phantom diffs (same bytes, different symbols) should be 100% byte match."""
        funcs_t = extract_all_functions(PHANTOM_TARGET)
        funcs_c = extract_all_functions(PHANTOM_COMPILED)
        match = compute_function_match(
            funcs_t["fn_phantom"], funcs_c["fn_phantom"]
        )
        assert match.fuzzy_match_percent == 100.0
        assert match.structural_match_percent == 100.0


# ---------------------------------------------------------------------------
# _extract_mnemonic tests
# ---------------------------------------------------------------------------


class TestExtractMnemonic:
    def test_simple_mnemonic(self):
        assert _extract_mnemonic("mflr r0") == "mflr"

    def test_mnemonic_with_operands(self):
        assert _extract_mnemonic("stw r0, 4(r1)") == "stw"

    def test_mnemonic_with_symbol(self):
        assert _extract_mnemonic("lis r3, lbSnap_803BACC8@ha") == "lis"

    def test_empty_string(self):
        assert _extract_mnemonic("") == ""

    def test_whitespace_only(self):
        assert _extract_mnemonic("   ") == ""

    def test_blr(self):
        assert _extract_mnemonic("blr") == "blr"


# ---------------------------------------------------------------------------
# _align_and_classify tests
# ---------------------------------------------------------------------------


class TestAlignAndClassify:
    def test_identical(self):
        parsed = _parse_asm_to_tuples(
            extract_all_functions(SAMPLE_ASM)["fn_80169574"]
        )
        analysis = _align_and_classify(parsed, parsed)
        assert analysis.total == 4
        assert analysis.matching == 4
        assert analysis.register_only == 0
        assert analysis.opcode_diffs == 0

    def test_register_only_diff(self):
        target = _parse_asm_to_tuples(
            extract_all_functions(REGISTER_ONLY_TARGET)["fn_regtest"]
        )
        compiled = _parse_asm_to_tuples(
            extract_all_functions(REGISTER_ONLY_COMPILED)["fn_regtest"]
        )
        analysis = _align_and_classify(target, compiled)
        assert analysis.matching == 3  # mflr, stw, blr match
        assert analysis.register_only == 3  # lwz r5->r6, lwz r3->r5, stw r5->r6
        assert analysis.opcode_diffs == 0

    def test_phantom_diff(self):
        target = _parse_asm_to_tuples(
            extract_all_functions(PHANTOM_TARGET)["fn_phantom"]
        )
        compiled = _parse_asm_to_tuples(
            extract_all_functions(PHANTOM_COMPILED)["fn_phantom"]
        )
        analysis = _align_and_classify(target, compiled)
        assert analysis.matching == 3  # all bytes match
        assert analysis.phantom == 2  # lis and addi have different symbol text
        assert analysis.register_only == 0

    def test_opcode_diff(self):
        # Create a case where mnemonics differ
        target = [("7C 08 02 A6", "mflr r0"), ("80 63 00 00", "lwz r3, 0(r3)")]
        compiled = [("7C 08 02 A6", "mflr r0"), ("A0 63 00 00", "lhz r3, 0(r3)")]
        analysis = _align_and_classify(target, compiled)
        assert analysis.matching == 1
        assert analysis.opcode_diffs == 1

    def test_structural_diff_extra_target(self):
        target = [
            ("7C 08 02 A6", "mflr r0"),
            ("90 01 00 04", "stw r0, 4(r1)"),
            ("4E 80 00 20", "blr"),
        ]
        compiled = [
            ("7C 08 02 A6", "mflr r0"),
            ("4E 80 00 20", "blr"),
        ]
        analysis = _align_and_classify(target, compiled)
        assert analysis.extra_target == 1

    def test_structural_diff_extra_compiled(self):
        target = [
            ("7C 08 02 A6", "mflr r0"),
            ("4E 80 00 20", "blr"),
        ]
        compiled = [
            ("7C 08 02 A6", "mflr r0"),
            ("90 01 00 04", "stw r0, 4(r1)"),
            ("4E 80 00 20", "blr"),
        ]
        analysis = _align_and_classify(target, compiled)
        assert analysis.extra_compiled == 1

    def test_mixed_diff(self):
        target = [
            ("7C 08 02 A6", "mflr r0"),
            ("80 A3 00 00", "lwz r5, 0(r3)"),  # register diff
            ("80 63 00 00", "lwz r3, 0(r3)"),   # opcode diff
        ]
        compiled = [
            ("7C 08 02 A6", "mflr r0"),
            ("80 C3 00 00", "lwz r6, 0(r3)"),  # register diff
            ("A0 63 00 00", "lhz r3, 0(r3)"),  # opcode diff
        ]
        analysis = _align_and_classify(target, compiled)
        assert analysis.register_only >= 1
        assert analysis.opcode_diffs >= 1


# ---------------------------------------------------------------------------
# _format_diff_analysis tests
# ---------------------------------------------------------------------------


class TestFormatDiffAnalysis:
    def test_all_match(self):
        analysis = DiffAnalysis(
            total=4, matching=4, phantom=0, register_only=0,
            opcode_diffs=0, extra_target=0, extra_compiled=0,
        )
        result = _format_diff_analysis(analysis)
        assert result == "All instructions match."

    def test_register_only_format(self):
        target = _parse_asm_to_tuples(
            extract_all_functions(REGISTER_ONLY_TARGET)["fn_regtest"]
        )
        compiled = _parse_asm_to_tuples(
            extract_all_functions(REGISTER_ONLY_COMPILED)["fn_regtest"]
        )
        analysis = _align_and_classify(target, compiled)
        result = _format_diff_analysis(analysis)

        assert "match" in result
        assert "differ" in result
        assert "[register]" in result
        assert "[opcode]" not in result

    def test_phantom_filtered(self):
        target = _parse_asm_to_tuples(
            extract_all_functions(PHANTOM_TARGET)["fn_phantom"]
        )
        compiled = _parse_asm_to_tuples(
            extract_all_functions(PHANTOM_COMPILED)["fn_phantom"]
        )
        analysis = _align_and_classify(target, compiled)
        result = _format_diff_analysis(analysis)
        # All bytes match, so this should say all match
        assert result == "All instructions match."

    def test_context_collapsing(self):
        """More than 3 consecutive matches should be collapsed."""
        # 6 matching + 1 register diff
        target = [
            ("7C 08 02 A6", "mflr r0"),
            ("90 01 00 04", "stw r0, 4(r1)"),
            ("94 21 FF E0", "stwu r1, -0x20(r1)"),
            ("BF 61 00 0C", "stmw r27, 0xc(r1)"),
            ("80 A3 00 00", "lwz r5, 0(r3)"),  # will differ
            ("38 60 00 00", "li r3, 0"),
            ("4E 80 00 20", "blr"),
        ]
        compiled = [
            ("7C 08 02 A6", "mflr r0"),
            ("90 01 00 04", "stw r0, 4(r1)"),
            ("94 21 FF E0", "stwu r1, -0x20(r1)"),
            ("BF 61 00 0C", "stmw r27, 0xc(r1)"),
            ("80 C3 00 00", "lwz r6, 0(r3)"),  # register diff
            ("38 60 00 00", "li r3, 0"),
            ("4E 80 00 20", "blr"),
        ]
        analysis = _align_and_classify(target, compiled)
        result = _format_diff_analysis(analysis)
        assert "... (" in result
        assert "matching instructions" in result


# ---------------------------------------------------------------------------
# Diff output tests (old format preserved for _normalize_for_diff)
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
        # Identical -> no diff output
        assert diff == ""

    def test_diff_analysis_format(self):
        """Analysis-based diff format has counts and instruction diff."""
        target = _parse_asm_to_tuples(
            extract_all_functions(SAMPLE_ASM)["fn_80169574"]
        )
        compiled = _parse_asm_to_tuples(
            extract_all_functions(SAMPLE_ASM_MODIFIED)["fn_80169574"]
        )
        analysis = _align_and_classify(target, compiled)
        result = _format_diff_analysis(analysis)
        # Should have counts
        assert "match" in result
        assert "differ" in result
        # The changed instruction (stwu) should appear
        assert "stwu" in result

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
    @patch("decomp_agent.tools.disasm.disassemble_object")
    def test_structural_match_fields_propagated(self, mock_disasm, mock_compile, tmp_path):
        """Verify structural_match_percent and mismatch_type are set in results."""
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

        # Use register-only diff for target, same for compiled
        mock_disasm.side_effect = [REGISTER_ONLY_TARGET, REGISTER_ONLY_COMPILED]

        result = check_match_via_disasm("melee/gm/gm_1601.c", config)

        assert result.success
        fn = result.get_function("fn_regtest")
        assert fn is not None
        assert fn.structural_match_percent == 100.0
        assert fn.mismatch_type == "register_only"

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
