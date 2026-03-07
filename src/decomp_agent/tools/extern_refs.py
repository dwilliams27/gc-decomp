"""Extract extern references from target assembly and look up their declarations.

Parses target assembly for:
- `bl <symbol>` — function calls
- `<symbol>@sda21` — small data area globals
- `<symbol>@ha` / `<symbol>@l` — large data area globals

Then searches the .ctx file (preprocessed headers) and codebase headers for
declarations so the agent knows what externs are available.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from decomp_agent.config import Config

# Pattern: bl <symbol>
_BL_RE = re.compile(r"\bbl\s+(\w+)\s*$", re.MULTILINE)

# Pattern: <symbol>@sda21, <symbol>@ha, <symbol>@l
_GLOBAL_REF_RE = re.compile(r"(\w+)@(?:sda21|ha|l)\b")


@dataclass
class ExternRefs:
    """Extern references extracted from target assembly."""

    called_functions: list[str] = field(default_factory=list)
    referenced_globals: list[str] = field(default_factory=list)


@dataclass
class ExternDecl:
    """A resolved extern declaration."""

    symbol: str
    kind: str  # "function" or "global"
    declaration: str  # The C declaration line(s)
    source: str  # Where found: "ctx" (already included) or header path


@dataclass
class ExternContext:
    """Resolved extern context for a function."""

    available: list[ExternDecl] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def format_for_llm(self) -> str:
        if not self.available and not self.missing:
            return ""

        parts: list[str] = []
        parts.append("=== Extern references from target assembly ===")

        if self.available:
            parts.append("\nDeclared (available via included headers):")
            for decl in self.available:
                parts.append(f"  {decl.declaration}")

        if self.missing:
            parts.append(
                "\nNot declared in current headers — you may need to add "
                "extern declarations or #include directives for these:"
            )

            # Group by kind
            missing_funcs = []
            missing_globals = []
            for sym in self.missing:
                if sym in self._missing_funcs_set:
                    missing_funcs.append(sym)
                else:
                    missing_globals.append(sym)

            if missing_funcs:
                parts.append("  Functions: " + ", ".join(missing_funcs))
            if missing_globals:
                parts.append("  Globals: " + ", ".join(missing_globals))

            if self.found_elsewhere:
                parts.append(
                    "\nDeclarations found elsewhere in the codebase "
                    "(add appropriate #include or extern):"
                )
                for decl in self.found_elsewhere:
                    parts.append(f"  // from {decl.source}")
                    parts.append(f"  {decl.declaration}")

        return "\n".join(parts)

    _missing_funcs_set: set[str] = field(default_factory=set, repr=False)
    found_elsewhere: list[ExternDecl] = field(default_factory=list)


def extract_extern_refs(asm_text: str, function_name: str) -> ExternRefs:
    """Parse target assembly to find extern references.

    Args:
        asm_text: Assembly text for a single function.
        function_name: Name of the function (excluded from results).

    Returns:
        ExternRefs with deduplicated lists of called functions and globals.
    """
    refs = ExternRefs()

    # Extract bl targets (function calls)
    seen_funcs: set[str] = set()
    for match in _BL_RE.finditer(asm_text):
        symbol = match.group(1)
        # Skip local labels (.L_XXXX) and the function itself
        if symbol.startswith(".") or symbol.startswith("lbl_"):
            continue
        if symbol == function_name:
            continue
        if symbol not in seen_funcs:
            seen_funcs.add(symbol)
            refs.called_functions.append(symbol)

    # Extract global references (@sda21, @ha, @l)
    seen_globals: set[str] = set()
    for match in _GLOBAL_REF_RE.finditer(asm_text):
        symbol = match.group(1)
        if symbol not in seen_globals:
            seen_globals.add(symbol)
            refs.referenced_globals.append(symbol)

    return refs


def _search_ctx_for_symbol(ctx_text: str, symbol: str) -> str | None:
    """Search preprocessed context (.ctx file) for a symbol declaration.

    Returns the declaration line(s) if found, or None.
    """
    lines = ctx_text.splitlines()

    # Try to find function prototype or variable declaration
    # Patterns: "type symbol(", "type *symbol(", "extern type symbol;",
    # "static type symbol;", "type symbol ="
    patterns = [
        # Function declaration/prototype: word(s) then symbol then (
        re.compile(
            rf"^[^\S\n]*(?:extern\s+|static\s+)?"
            rf"(?:[\w*\s]+\s+\*?\s*){symbol}\s*\(",
            re.MULTILINE,
        ),
        # Variable declaration: type symbol; or type symbol =
        re.compile(
            rf"^[^\S\n]*(?:extern\s+|static\s+)?"
            rf"[\w*\s]+\s+\*?\s*{re.escape(symbol)}\s*[;=\[]",
            re.MULTILINE,
        ),
        # #define
        re.compile(rf"^#define\s+{re.escape(symbol)}\b", re.MULTILINE),
    ]

    for pattern in patterns:
        match = pattern.search(ctx_text)
        if match:
            line_start = ctx_text.rfind("\n", 0, match.start()) + 1
            # Get up to 3 lines for multi-line declarations
            result_lines = []
            pos = line_start
            paren_depth = 0
            for _ in range(5):
                line_end = ctx_text.find("\n", pos)
                if line_end == -1:
                    line_end = len(ctx_text)
                line = ctx_text[pos:line_end].strip()
                result_lines.append(line)
                paren_depth += line.count("(") - line.count(")")
                if line.endswith(";") or line.endswith("}") or (
                    paren_depth <= 0 and line.endswith(")")
                ):
                    break
                pos = line_end + 1

            decl = " ".join(result_lines)
            # Clean up excessive whitespace
            decl = re.sub(r"\s+", " ", decl).strip()
            return decl

    return None


def _grep_codebase_for_symbol(
    symbol: str, config: Config
) -> ExternDecl | None:
    """Search codebase headers for a symbol declaration via grep.

    Searches include/ and src/ directories for header files containing
    the symbol. Returns the first match found.
    """
    # Search in include/ and src/ header files
    search_dirs = [
        config.melee.repo_path / "include",
        config.melee.repo_path / "src",
    ]

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue

        result = subprocess.run(
            [
                "grep", "-rn", "--include=*.h",
                "-m", "1",  # first match only
                symbol,
                str(search_dir),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0 and result.stdout.strip():
            line = result.stdout.strip().split("\n")[0]
            # Parse "filepath:lineno:content"
            parts = line.split(":", 2)
            if len(parts) >= 3:
                filepath = parts[0]
                content = parts[2].strip()

                # Skip if this is just a comment or include
                if content.startswith("//") or content.startswith("#include"):
                    continue

                # Make path relative for display
                try:
                    rel_path = Path(filepath).relative_to(config.melee.repo_path)
                except ValueError:
                    rel_path = Path(filepath)

                return ExternDecl(
                    symbol=symbol,
                    kind="function" if "(" in content else "global",
                    declaration=content,
                    source=str(rel_path),
                )

    return None


def resolve_extern_context(
    function_name: str,
    source_file: str,
    config: Config,
    asm_text: str | None = None,
) -> ExternContext:
    """Extract extern refs from assembly and resolve their declarations.

    Args:
        function_name: Target function name.
        source_file: Object name e.g. "melee/mn/mngallery.c"
        config: Project configuration.
        asm_text: Pre-fetched assembly text. If None, fetches it.

    Returns:
        ExternContext with resolved declarations.
    """
    # Get assembly if not provided
    if asm_text is None:
        from decomp_agent.tools.m2c_tool import get_target_assembly
        asm_text = get_target_assembly(function_name, source_file, config)
        if asm_text is None:
            return ExternContext()

    # Extract references
    refs = extract_extern_refs(asm_text, function_name)
    if not refs.called_functions and not refs.referenced_globals:
        return ExternContext()

    # Load .ctx file for lookup
    from decomp_agent.tools.context import _get_ctx_file

    ctx_path = _get_ctx_file(source_file, config)
    ctx_text = ""
    if ctx_path.exists():
        ctx_text = ctx_path.read_text(encoding="utf-8", errors="replace")

    result = ExternContext()
    all_symbols = [
        (sym, "function") for sym in refs.called_functions
    ] + [
        (sym, "global") for sym in refs.referenced_globals
    ]

    missing_symbols: list[tuple[str, str]] = []

    for symbol, kind in all_symbols:
        if ctx_text:
            decl = _search_ctx_for_symbol(ctx_text, symbol)
            if decl:
                result.available.append(ExternDecl(
                    symbol=symbol,
                    kind=kind,
                    declaration=decl,
                    source="ctx",
                ))
                continue

        missing_symbols.append((symbol, kind))

    # For missing symbols, search the broader codebase
    for symbol, kind in missing_symbols:
        result.missing.append(symbol)
        if kind == "function":
            result._missing_funcs_set.add(symbol)

        found = _grep_codebase_for_symbol(symbol, config)
        if found:
            result.found_elsewhere.append(found)

    return result
