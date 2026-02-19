"""Build + verify tools: compile objects and check match status."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from decomp_agent.config import Config
from decomp_agent.tools.run import run_in_repo


@dataclass
class FunctionMatch:
    """Match result for a single function within an object."""

    name: str
    fuzzy_match_percent: float
    size: int
    structural_match_percent: float = 0.0  # mnemonic-level match
    mismatch_type: str = ""  # "register_only", "opcode", "structural", "mixed", ""

    @property
    def is_matched(self) -> bool:
        return self.fuzzy_match_percent == 100.0


@dataclass
class CompileResult:
    """Result of compiling and checking an object file."""

    object_name: str  # e.g. "melee/lb/lbcommand.c"
    success: bool  # True if compilation succeeded
    functions: list[FunctionMatch] = field(default_factory=list)
    error: str | None = None

    @property
    def all_matched(self) -> bool:
        return all(f.is_matched for f in self.functions)

    @property
    def match_percent(self) -> float:
        if not self.functions:
            return 0.0
        return sum(f.fuzzy_match_percent for f in self.functions) / len(
            self.functions
        )

    def get_function(self, name: str) -> FunctionMatch | None:
        for f in self.functions:
            if f.name == name:
                return f
        return None


def _object_to_build_target(object_name: str, config: Config) -> str:
    """Convert object name to ninja build target path.

    e.g. "melee/lb/lbcommand.c" -> "build/GALE01/src/melee/lb/lbcommand.o"
    """
    stem = object_name.rsplit(".", 1)[0]  # strip .c extension
    return f"{config.melee.build_dir}/{config.melee.version}/src/{stem}.o"


def _object_to_unit_name(object_name: str) -> str:
    """Convert object name to objdiff unit name.

    e.g. "melee/lb/lbcommand.c" -> "main/melee/lb/lbcommand"
    """
    stem = object_name.rsplit(".", 1)[0]
    return f"main/{stem}"


def compile_object(object_name: str, config: Config) -> CompileResult:
    """Compile a single object file using ninja.

    Args:
        object_name: Object name from configure.py, e.g. "melee/lb/lbcommand.c"
        config: Project configuration

    Returns:
        CompileResult with success status and any error message.
    """
    target = _object_to_build_target(object_name, config)

    try:
        result = run_in_repo(["ninja", target], config=config)
    except subprocess.TimeoutExpired:
        return CompileResult(
            object_name=object_name,
            success=False,
            error="Compilation timed out",
        )

    if result.returncode != 0:
        return CompileResult(
            object_name=object_name,
            success=False,
            error=result.stderr or result.stdout,
        )

    return CompileResult(object_name=object_name, success=True)


def check_match(object_name: str, config: Config) -> CompileResult:
    """Compile an object and check match status via dtk disassembly.

    Compiles the single object, then disassembles both target and compiled
    .o files to compute per-function match percentages. Much faster than
    the old report.json approach (single object vs rebuilding all 968).
    """
    from decomp_agent.tools.disasm import check_match_via_disasm

    return check_match_via_disasm(object_name, config)
