"""Pydantic models for agent-facing tool parameters.

Each model's docstring becomes the tool description when passed to
``openai.pydantic_function_tool()``.  Fields are what the LLM provides;
``config`` is injected by the registry at dispatch time.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GetTargetAssemblyParams(BaseModel):
    """Get the target PowerPC assembly for a function. Returns the disassembled
    instructions from the original game binary that your C code must match."""

    function_name: str = Field(description="Name of the function")
    source_file: str = Field(
        description='Object name from configure.py, e.g. "melee/lb/lbcommand.c"'
    )


class GetGhidraDecompilationParams(BaseModel):
    """Get Ghidra's decompiled C code for a function. Provides type-aware
    decompilation with struct access patterns and control flow — useful for
    understanding what the function does, but not directly usable as matching C."""

    function_name: str = Field(description="Name of the function")


class GetM2CDecompilationParams(BaseModel):
    """Run m2c on a function's target assembly to produce an initial C
    decompilation. The output is matching-oriented and makes a good starting
    template, but usually needs manual adjustment.

    Optional flags improve output quality:
    - no_casts: Remove type casts for cleaner output
    - stack_structs: Infer stack struct types (Vec3 copies become single assigns)
    - globals_none: Don't emit global declarations (cleaner for focused work)
    - void: Assume function returns void
    - no_andor: Disable &&/|| detection
    - no_switches: Disable irregular switch detection

    Optional union_fields fix wrong union member selection:
    - Format: ["StructName:field_name"] e.g. ["Item_ItemVars:leadead"]"""

    function_name: str = Field(description="Name of the function")
    source_file: str = Field(
        description='Object name from configure.py, e.g. "melee/lb/lbcommand.c"'
    )
    flags: list[str] | None = Field(
        default=None,
        description=(
            'Optional m2c flags: "no_casts", "stack_structs", "globals_none", '
            '"globals_all", "void", "no_andor", "no_switches", "no_unk_inference"'
        ),
    )
    union_fields: list[str] | None = Field(
        default=None,
        description=(
            'Optional union field selections, format: "StructName:field_name". '
            'Fixes m2c defaulting to wrong union variant.'
        ),
    )


class GetContextParams(BaseModel):
    """Get preprocessed headers, types, and nearby matched functions for
    context. Returns everything the function can reference: struct definitions,
    enums, externs, and examples of already-matched functions in the same file."""

    function_name: str = Field(description="Name of the function")
    source_file: str = Field(
        description='Object name from configure.py, e.g. "melee/lb/lbcommand.c"'
    )


class ReadSourceFileParams(BaseModel):
    """Read the current contents of a C source file. Use this to see the
    existing code, includes, and function stubs before making changes."""

    source_file: str = Field(
        description='Object name from configure.py, e.g. "melee/lb/lbcommand.c"'
    )


class WriteFunctionParams(BaseModel):
    """Replace a function's implementation, compile, and return match results.
    Provide the complete function including signature and body. The old
    implementation is found by name and fully replaced. Automatically compiles
    and returns match percentages. If compilation fails, the code is reverted
    to the previous working version."""

    source_file: str = Field(
        description='Object name from configure.py, e.g. "melee/lb/lbcommand.c"'
    )
    function_name: str = Field(description="Name of the function to replace")
    code: str = Field(
        description="Complete function code (signature + body) to write"
    )


class CompileAndCheckParams(BaseModel):
    """Compile the source file and check match status for all functions in the
    translation unit. Returns per-function fuzzy match percentages showing how
    close each function is to matching the target."""

    source_file: str = Field(
        description='Object name from configure.py, e.g. "melee/lb/lbcommand.c"'
    )


class GetDiffParams(BaseModel):
    """Get the assembly diff between compiled and target code for a specific
    function. Shows instruction-level differences so you can see exactly which
    instructions don't match and adjust your C code accordingly."""

    source_file: str = Field(
        description='Object name from configure.py, e.g. "melee/lb/lbcommand.c"'
    )
    function_name: str = Field(description="Name of the function to diff")


class RunPermuterParams(BaseModel):
    """Run decomp-permuter to automatically search for matching code
    permutations. Best used when you're close (>90%% match) but stuck on
    register allocation or instruction ordering issues."""

    function_name: str = Field(description="Name of the function to permute")
    source_file: str = Field(
        description='Object name from configure.py, e.g. "melee/lb/lbcommand.c"'
    )
    timeout: int = Field(
        default=1800,
        description="Max seconds to run (default 1800 = 30 min)",
    )
    workers: int = Field(
        default=8,
        description="Number of parallel permuter workers (default 8)",
    )


class MarkCompleteParams(BaseModel):
    """Mark a function as successfully matched. Call this after compile_and_check
    confirms 100%% match for your target function."""

    function_name: str = Field(description="Name of the matched function")
    source_file: str = Field(
        description='Object name from configure.py, e.g. "melee/lb/lbcommand.c"'
    )
