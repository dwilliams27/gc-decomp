"""Tests for Phase 2: Build + Verify Tools."""

from pathlib import Path

from decomp_agent.config import Config, MeleeConfig
from decomp_agent.tools.source import (
    find_functions,
    get_function_source,
    read_source_file,
    replace_function,
    insert_function,
)
from decomp_agent.tools.context import get_function_context
from decomp_agent.tools.build import (
    CompileResult,
    FunctionMatch,
    _object_to_build_target,
    _object_to_unit_name,
)


MELEE_REPO = Path("/Users/dwilliams/proj/melee")


def _make_config() -> Config:
    return Config(melee=MeleeConfig(repo_path=MELEE_REPO))


# --- source.py tests ---


def test_find_functions_matching_file():
    src = read_source_file(MELEE_REPO / "src/melee/lb/lbcommand.c")
    funcs = find_functions(src)
    names = [f.name for f in funcs]
    assert "Command_00" in names
    assert "Command_Execute" in names
    # Should NOT contain the function pointer array "d"
    assert "d" not in names


def test_find_functions_nonmatching_file():
    src = read_source_file(MELEE_REPO / "src/melee/lb/lbcollision.c")
    funcs = find_functions(src)
    names = [f.name for f in funcs]
    assert "lbColl_80005BB0" in names
    assert len(funcs) > 30  # lots of functions


def test_find_functions_forward_declarations_skipped():
    src = read_source_file(MELEE_REPO / "src/melee/ft/ftaction.c")
    funcs = find_functions(src)
    names = [f.name for f in funcs]
    # Forward declarations should not be included as function definitions
    # All functions should start after the forward declarations
    for f in funcs:
        assert f.start_line > 100  # forward decls are in first ~100 lines


def test_get_function_source():
    src = read_source_file(MELEE_REPO / "src/melee/lb/lbcommand.c")
    code = get_function_source(src, "Command_00")
    assert code is not None
    assert "Command_00" in code
    assert "info->u = NULL" in code


def test_get_function_source_not_found():
    src = read_source_file(MELEE_REPO / "src/melee/lb/lbcommand.c")
    code = get_function_source(src, "nonexistent_function")
    assert code is None


def test_replace_function():
    src = read_source_file(MELEE_REPO / "src/melee/lb/lbcommand.c")
    new_code = """void Command_00(CommandInfo* info)
{
    // new implementation
    info->u = NULL;
}"""
    result = replace_function(src, "Command_00", new_code)
    assert result is not None
    assert "// new implementation" in result
    # Other functions should still be there
    assert "Command_01" in result
    assert "Command_Execute" in result


def test_replace_function_not_found():
    src = "void foo(void) { }\n"
    result = replace_function(src, "bar", "void bar(void) { }")
    assert result is None


def test_insert_function():
    src = """void foo(void)
{
    return;
}
"""
    new_func = """void bar(void)
{
    return;
}"""
    result = insert_function(src, new_func, after_function="foo")
    assert "bar" in result
    # bar should come after foo
    assert result.index("foo") < result.index("bar")


def test_find_functions_handles_inline():
    src = """static inline void helper(int x)
{
    return;
}

void main_func(void)
{
    helper(1);
}
"""
    funcs = find_functions(src)
    names = [f.name for f in funcs]
    assert "helper" in names
    assert "main_func" in names


def test_find_functions_skips_function_pointers():
    """Function pointer arrays should not be detected as functions."""
    src = """void (*table[4])(int) = {
    func_a, func_b, func_c, func_d
};

void real_func(int x)
{
    table[x](x);
}
"""
    funcs = find_functions(src)
    names = [f.name for f in funcs]
    assert "real_func" in names
    assert len(funcs) == 1  # only real_func, not "table"


def test_brace_matching_with_strings():
    """Braces inside strings should not affect matching."""
    src = """void func(void)
{
    char* s = "{ }";
    printf("{%s}", s);
}
"""
    funcs = find_functions(src)
    assert len(funcs) == 1
    assert funcs[0].name == "func"
    assert funcs[0].end_line == 4  # closing brace


def test_paren_in_comment_multiline_signature():
    """Parens inside comments should not affect paren depth tracking."""
    src = """bool func(const Vec3* arg0, /* ) */ const Vec3* arg1,
          Vec3* arg3, float arg8)
{
    return 0;
}
"""
    funcs = find_functions(src)
    assert len(funcs) == 1
    assert funcs[0].name == "func"


def test_paren_in_string_signature():
    """Parens inside strings should not affect paren depth tracking."""
    src = """void func(char* fmt)
{
    printf("hello )");
}
"""
    funcs = find_functions(src)
    assert len(funcs) == 1
    assert funcs[0].name == "func"


# --- build.py tests ---


def test_object_to_build_target():
    config = _make_config()
    target = _object_to_build_target("melee/lb/lbcommand.c", config)
    assert target == "build/GALE01/src/melee/lb/lbcommand.o"


def test_object_to_unit_name():
    assert _object_to_unit_name("melee/lb/lbcommand.c") == "main/melee/lb/lbcommand"


def test_compile_result_properties():
    result = CompileResult(
        object_name="test.c",
        success=True,
        functions=[
            FunctionMatch(name="func_a", fuzzy_match_percent=100.0, size=64),
            FunctionMatch(name="func_b", fuzzy_match_percent=75.0, size=128),
        ],
    )
    assert not result.all_matched
    assert result.match_percent == 87.5
    assert result.get_function("func_a").is_matched
    assert not result.get_function("func_b").is_matched


# --- context.py tests ---


def test_get_function_context_basic():
    config = _make_config()
    ctx = get_function_context(
        "Command_00",
        "melee/lb/lbcommand.c",
        config,
        include_ctx=False,
    )
    assert ctx.function_name == "Command_00"
    assert ctx.file_source is not None
    assert len(ctx.includes) > 0
    assert any("lbcommand.h" in inc for inc in ctx.includes)


def test_context_nearby_functions():
    config = _make_config()
    ctx = get_function_context(
        "Command_00",
        "melee/lb/lbcommand.c",
        config,
        include_ctx=False,
        max_nearby=5,
    )
    # Should find nearby functions
    assert len(ctx.nearby_functions) > 0
    assert len(ctx.nearby_functions) <= 5


def test_context_format_for_llm():
    config = _make_config()
    ctx = get_function_context(
        "Command_00",
        "melee/lb/lbcommand.c",
        config,
        include_ctx=False,
    )
    output = ctx.format_for_llm()
    assert "Command_00" in output
    assert "lbcommand.c" in output
