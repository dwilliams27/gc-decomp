"""Tests for Phase 3: Decompilation Tools."""

from pathlib import Path

from decomp_agent.config import Config, MeleeConfig
from decomp_agent.tools.m2c_tool import (
    M2CResult,
    _source_to_asm_path,
    _source_to_obj_path,
    _ctx_file_path,
    extract_function_asm,
)
from decomp_agent.tools.ghidra import (
    GhidraResult,
    get_ghidra_decompilation,
    get_ghidra_decompilation_by_address,
)
from decomp_agent.tools.permuter import PermuterResult, _find_permuter


MELEE_REPO = Path("/Users/dwilliams/proj/melee")


def _make_config() -> Config:
    return Config(melee=MeleeConfig(repo_path=MELEE_REPO))


# --- m2c_tool.py tests ---


def test_source_to_asm_path():
    config = _make_config()
    path = _source_to_asm_path("melee/lb/lbcommand.c", config)
    assert path == MELEE_REPO / "build/GALE01/asm/melee/lb/lbcommand.s"


def test_source_to_asm_path_nested():
    config = _make_config()
    path = _source_to_asm_path("melee/ft/chara/ftFox/ftFox_SpecialLw.c", config)
    assert path == MELEE_REPO / "build/GALE01/asm/melee/ft/chara/ftFox/ftFox_SpecialLw.s"


def test_source_to_obj_path():
    config = _make_config()
    path = _source_to_obj_path("melee/lb/lbcommand.c", config)
    assert path == MELEE_REPO / "build/GALE01/obj/melee/lb/lbcommand.o"


def test_ctx_file_path():
    config = _make_config()
    path = _ctx_file_path(config)
    assert path == MELEE_REPO / "build/ctx.c"


def test_extract_function_asm_basic():
    """Extract a function from typical dtk-generated assembly."""
    asm = """\
.include "macros.inc"

.section .text, "ax"

.global Command_00
Command_00:
    stwu r1, -0x10(r1)
    mflr r0
    stw r0, 0x14(r1)
    li r4, 0
    stw r4, 0(r3)
    lwz r0, 0x14(r1)
    mtlr r0
    addi r1, r1, 0x10
    blr
.endfn Command_00

.global Command_01
Command_01:
    li r3, 1
    blr
.endfn Command_01
"""
    result = extract_function_asm(asm, "Command_00")
    assert result is not None
    assert "Command_00:" in result
    assert "stwu r1" in result
    assert "blr" in result
    # Should NOT contain Command_01
    assert "Command_01" not in result


def test_extract_function_asm_second_function():
    asm = """\
.global func_a
func_a:
    li r3, 0
    blr
.endfn func_a

.global func_b
func_b:
    li r3, 1
    blr
.endfn func_b
"""
    result = extract_function_asm(asm, "func_b")
    assert result is not None
    assert "func_b:" in result
    assert "li r3, 1" in result
    assert "func_a" not in result


def test_extract_function_asm_not_found():
    asm = """\
.global func_a
func_a:
    blr
.endfn func_a
"""
    result = extract_function_asm(asm, "nonexistent")
    assert result is None


def test_extract_function_asm_no_endfn():
    """Handle assembly without .endfn markers (terminated by next .global)."""
    asm = """\
.global func_a
func_a:
    li r3, 0
    blr

.global func_b
func_b:
    li r3, 1
    blr
"""
    result = extract_function_asm(asm, "func_a")
    assert result is not None
    assert "func_a:" in result
    assert "li r3, 0" in result
    assert "func_b" not in result


def test_extract_function_asm_with_size_directive():
    """Handle .size directive as end marker."""
    asm = """\
.global my_func
my_func:
    mflr r0
    stw r0, 4(r1)
    blr
.size my_func, . - my_func
"""
    result = extract_function_asm(asm, "my_func")
    assert result is not None
    assert "my_func:" in result
    assert "mflr r0" in result


def test_extract_function_asm_fn_directive():
    """Handle .fn/.endfn directives (actual dtk output format)."""
    asm = """\
.include "macros.inc"

.section .text, "ax"

.fn func_a, global
/* 80240000 00000000  38 60 00 00 */	li r3, 0
/* 80240004 00000004  4E 80 00 20 */	blr
.endfn func_a

.fn func_b, global
/* 80240008 00000008  38 60 00 01 */	li r3, 1
/* 8024000C 0000000C  4E 80 00 20 */	blr
.endfn func_b
"""
    result = extract_function_asm(asm, "func_a")
    assert result is not None
    assert ".fn func_a" in result
    assert "li r3, 0" in result
    assert ".endfn func_a" in result
    assert "func_b" not in result


def test_extract_function_asm_fn_directive_second():
    """Extract the second function using .fn directives."""
    asm = """\
.fn func_a, global
    li r3, 0
    blr
.endfn func_a

.fn func_b, global
    li r3, 1
    blr
.endfn func_b
"""
    result = extract_function_asm(asm, "func_b")
    assert result is not None
    assert ".fn func_b" in result
    assert "li r3, 1" in result
    assert "func_a" not in result


def test_m2c_result_properties():
    success = M2CResult(function_name="test", c_code="void test(void) {}")
    assert success.success
    assert success.c_code == "void test(void) {}"

    failure = M2CResult(function_name="test", error="m2c not found")
    assert not failure.success
    assert failure.error == "m2c not found"


# --- ghidra.py tests ---


def test_ghidra_disabled_by_default():
    """When ghidra.enabled is false, returns a clear error."""
    config = _make_config()
    result = get_ghidra_decompilation("some_func", config)
    assert not result.success
    assert "not enabled" in result.error


def test_ghidra_disabled_by_address():
    config = _make_config()
    result = get_ghidra_decompilation_by_address(0x80005BB0, config)
    assert not result.success
    assert "not enabled" in result.error


def test_ghidra_result_properties():
    success = GhidraResult(
        function_name="test",
        c_code="int test(void) { return 0; }",
        signature="int test(void)",
        return_type="int",
        parameters=[],
    )
    assert success.success

    failure = GhidraResult(function_name="test", error="failed")
    assert not failure.success


def test_ghidra_result_format_for_llm():
    result = GhidraResult(
        function_name="Command_00",
        c_code="void Command_00(CommandInfo *info) {\n    info->u = NULL;\n}",
        signature="void Command_00(CommandInfo *)",
    )
    output = result.format_for_llm()
    assert "Command_00" in output
    assert "Signature:" in output
    assert "info->u = NULL" in output

    # Error case
    err_result = GhidraResult(function_name="test", error="not enabled")
    assert "unavailable" in err_result.format_for_llm()


# --- permuter.py tests ---


def test_permuter_result_properties():
    # Perfect match
    result = PermuterResult(
        function_name="test",
        best_score=0,
        best_code="void test(void) {}",
        iterations=100,
    )
    assert result.success
    assert result.improved

    # Improved but not matched
    result = PermuterResult(
        function_name="test",
        best_score=50,
        best_code="void test(void) { /* close */ }",
        iterations=500,
    )
    assert not result.success
    assert result.improved

    # No improvement
    result = PermuterResult(
        function_name="test",
        error="permuter not found",
    )
    assert not result.success
    assert not result.improved


def test_permuter_not_installed():
    """_find_permuter returns None when permuter isn't available."""
    # This test verifies the function doesn't crash
    # It may return None or a path depending on the environment
    result = _find_permuter()
    # Just ensure it returns the right type
    assert result is None or isinstance(result, Path)
