"""Read/write C source files and manipulate function bodies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class FunctionSpan:
    """Location of a function definition in a source file."""

    name: str
    start_line: int  # Line number of the first line (return type / signature)
    end_line: int  # Line number of the closing brace
    body_start_line: int  # Line number of the opening brace

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


# Matches a function definition start: return_type func_name(...)
# Handles: static, inline, void, int, bool, etc. + pointer returns
# Does NOT match: forward declarations (ending with ;), macros, struct definitions
# Requires mandatory whitespace or * between return type and function name to avoid
# matching function pointer declarations like: void (*name[16])(params) = {
_FUNC_DEF_RE = re.compile(
    r"^"
    r"(?:(?:static|inline|extern)\s+)*"  # optional qualifiers
    r"(?:(?:const|volatile|unsigned|signed|long|short|enum|struct|union)\s+)*"  # type qualifiers
    r"\w+"  # base return type
    r"(?:\s*\*)*"  # optional pointer stars
    r"\s+"  # mandatory space before function name
    r"(\w+)"  # function name (captured)
    r"\s*\(",  # opening paren
)


def read_source_file(source_path: Path) -> str:
    """Read a C source file and return its contents."""
    return source_path.read_text(encoding="utf-8")


def write_source_file(source_path: Path, content: str) -> None:
    """Write content to a C source file."""
    source_path.write_text(content, encoding="utf-8")


def _iter_code_chars(
    lines: list[str], start_line: int = 0
) -> Iterator[tuple[int, int, str]]:
    """Yield (line_index, col_index, char) for code characters only.

    Skips characters inside line comments (//), block comments (/* */),
    and string/char literals ("...", '...'), handling escape sequences.
    """
    in_block_comment = False
    in_string: str | bool = False
    escape_next = False

    for i in range(start_line, len(lines)):
        line = lines[i]
        j = 0
        while j < len(line):
            ch = line[j]

            if escape_next:
                escape_next = False
                j += 1
                continue

            if in_string:
                if ch == "\\":
                    escape_next = True
                elif ch == in_string:
                    in_string = False
                j += 1
                continue

            if in_block_comment:
                if ch == "*" and j + 1 < len(line) and line[j + 1] == "/":
                    in_block_comment = False
                    j += 2
                    continue
                j += 1
                continue

            # Check for comments
            if ch == "/" and j + 1 < len(line):
                if line[j + 1] == "/":
                    break  # rest of line is a line comment
                if line[j + 1] == "*":
                    in_block_comment = True
                    j += 2
                    continue

            # Check for strings/char literals
            if ch in ('"', "'"):
                in_string = ch
                j += 1
                continue

            yield i, j, ch
            j += 1


def _find_matching_brace(lines: list[str], start_line: int) -> int | None:
    """Find the line number of the closing brace matching the opening brace.

    Scans from start_line forward, counting braces. Skips braces
    inside strings and comments.
    """
    depth = 0
    for line_idx, _, ch in _iter_code_chars(lines, start_line):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return line_idx
    return None


def _find_close_paren(
    lines: list[str], start_line: int, end_line: int
) -> int | None:
    """Find the line containing the matching ')' for the first '(' found.

    Scans code characters only (skipping comments/strings).
    Returns the line number where paren_depth returns to 0, or None.
    """
    depth = 0
    for line_idx, _, ch in _iter_code_chars(lines, start_line):
        if line_idx > end_line:
            break
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return line_idx
    return None


def find_functions(source: str) -> list[FunctionSpan]:
    """Find all function definitions in C source code.

    Returns a list of FunctionSpan objects describing where each
    function is located in the file.
    """
    lines = source.splitlines()
    functions: list[FunctionSpan] = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Skip preprocessor directives
        if line.startswith("#"):
            i += 1
            continue

        # Try to match a function definition
        match = _FUNC_DEF_RE.match(line)
        if not match:
            i += 1
            continue

        func_name = match.group(1)

        # Find the closing paren of the parameter list, scanning code chars
        # only (skipping comments and strings) to avoid false `)` matches.
        search_end = min(i + 10, len(lines))  # look ahead up to 10 lines
        close_paren_line = _find_close_paren(lines, i, search_end - 1)

        if close_paren_line is None:
            i += 1
            continue

        j = close_paren_line

        # Check if this is a forward declaration or function definition
        # by looking at what follows the closing paren.
        brace_line = None
        rest = lines[j].strip()
        after_paren = rest[rest.rfind(")") + 1 :].strip() if ")" in rest else ""

        if after_paren == ";" or after_paren.startswith(";"):
            i += 1
            continue  # forward declaration
        if "=" in after_paren:
            i += 1
            continue  # variable initialization: ) = {
        if "{" in after_paren:
            brace_line = j
        elif j + 1 < len(lines):
            next_stripped = lines[j + 1].strip()
            if next_stripped.startswith("{"):
                brace_line = j + 1
            elif next_stripped.startswith(";") or "=" in next_stripped:
                i += 1
                continue  # forward declaration or variable init

        if brace_line is not None:
            end_line = _find_matching_brace(lines, brace_line)
            if end_line is not None:
                functions.append(
                    FunctionSpan(
                        name=func_name,
                        start_line=i,
                        end_line=end_line,
                        body_start_line=brace_line,
                    )
                )
                i = end_line + 1
                continue

        i += 1

    return functions


def get_function_source(source: str, function_name: str) -> str | None:
    """Extract the complete source code of a function by name.

    Returns the full function including signature and body, or None
    if the function is not found.
    """
    lines = source.splitlines()
    for span in find_functions(source):
        if span.name == function_name:
            return "\n".join(lines[span.start_line : span.end_line + 1])
    return None


def replace_function(
    source: str, function_name: str, new_code: str
) -> str | None:
    """Replace a function's entire definition with new code.

    Args:
        source: The full source file content
        function_name: Name of the function to replace
        new_code: Complete new function code (signature + body)

    Returns:
        Updated source file content, or None if function not found.
    """
    lines = source.splitlines()
    for span in find_functions(source):
        if span.name == function_name:
            before = lines[: span.start_line]
            after = lines[span.end_line + 1 :]
            new_lines = new_code.splitlines()
            result = before + new_lines + after
            return "\n".join(result) + "\n"
    return None


def insert_function(
    source: str, new_code: str, after_function: str | None = None
) -> str:
    """Insert a new function into the source file.

    If after_function is specified, inserts after that function.
    Otherwise appends to the end of the file.
    """
    if after_function:
        lines = source.splitlines()
        for span in find_functions(source):
            if span.name == after_function:
                before = lines[: span.end_line + 1]
                after = lines[span.end_line + 1 :]
                new_lines = [""] + new_code.splitlines()
                result = before + new_lines + after
                return "\n".join(result) + "\n"

    # Append to end
    if not source.endswith("\n"):
        source += "\n"
    return source + "\n" + new_code + "\n"


def list_functions(source_path: Path) -> list[str]:
    """Return a list of function names defined in a source file."""
    source = read_source_file(source_path)
    return [f.name for f in find_functions(source)]
