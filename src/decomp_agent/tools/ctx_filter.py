"""Smart filtering of .ctx preprocessed header files.

.ctx files contain all transitive includes flattened into one file, with
section markers like:
    /* "src/melee/it/items/itbombhei.h" line 3 "it/forward.h" */
    ...content...
    /* end "it/forward.h" */

This module parses those markers into sections, scores them by relevance
to the source file being decompiled, and selects the most useful headers
within a character budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Matches: /* "src/path/to/file.c" line 42 "header.h" */
_START_RE = re.compile(
    r'^/\*\s+"[^"]+"\s+line\s+\d+\s+"([^"]+)"\s+\*/$'
)

# Matches: /* end "header.h" */
_END_RE = re.compile(r'^/\*\s+end\s+"([^"]+)"\s+\*/$')

# Fighter character directory pattern (ftCaptain, ftFox, ftKirby, etc.)
_FIGHTER_CHAR_RE = re.compile(r"^ft[A-Z][a-zA-Z]+/")

# Lines that are just noise in headers
_NOISE_RE = re.compile(r"^\s*///\s*@(file|todo Delete)", re.IGNORECASE)


@dataclass
class CtxSection:
    """One parsed header section from a .ctx file."""

    header_name: str
    content: str
    start_line: int
    is_empty: bool


def parse_ctx_sections(ctx_text: str) -> list[CtxSection]:
    """Parse a .ctx file into a list of header sections.

    Uses a stack to handle nested includes. Returns a flat list of all
    sections with their content (the text between start/end markers).
    """
    lines = ctx_text.splitlines()
    sections: list[CtxSection] = []

    # Stack of (header_name, start_line, content_lines)
    stack: list[tuple[str, int, list[str]]] = []

    for line_no, line in enumerate(lines):
        stripped = line.strip()

        start_m = _START_RE.match(stripped)
        if start_m:
            header_name = start_m.group(1)
            stack.append((header_name, line_no, []))
            continue

        end_m = _END_RE.match(stripped)
        if end_m:
            header_name = end_m.group(1)
            # Pop matching entry from stack
            if stack and stack[-1][0] == header_name:
                name, start, content_lines = stack.pop()
                content = "\n".join(content_lines)
                is_empty = not content.strip()
                sections.append(
                    CtxSection(
                        header_name=name,
                        content=content,
                        start_line=start,
                        is_empty=is_empty,
                    )
                )
            continue

        # Regular content line — append to innermost open section
        if stack:
            stack[-1][2].append(line)

    return sections


def _extract_library_and_module(source_file: str) -> tuple[str, str]:
    """Extract library and module path from a source file path.

    "melee/it/items/itbombhei.c" -> library="it", module="it/items"
    "melee/ft/chara/ftKirby/ftKb_Init.c" -> library="ft", module="ft/chara/ftKirby"
    """
    # Strip "melee/" prefix if present
    path = source_file
    if path.startswith("melee/"):
        path = path[len("melee/"):]

    parts = path.split("/")
    library = parts[0] if parts else ""
    # Module is everything up to the filename
    module = "/".join(parts[:-1]) if len(parts) > 1 else library
    return library, module


def _extract_stem(source_file: str) -> str:
    """Extract filename stem from a source path.

    "melee/it/items/itbombhei.c" -> "itbombhei"
    """
    filename = source_file.rsplit("/", 1)[-1]
    return filename.rsplit(".", 1)[0]


def score_section(
    header_name: str, source_file: str, is_empty: bool
) -> int:
    """Score a header section 0-100 based on relevance to the source file.

    Higher scores = more relevant context for decompilation.
    """
    if is_empty:
        return 0

    h = header_name.lower()

    # Std C library — never useful
    if h in (
        "stdio.h", "stdlib.h", "stdarg.h", "stddef.h", "stdbool.h",
        "string.h", "ctype.h", "math.h", "limits.h", "float.h",
        "assert.h", "errno.h", "setjmp.h", "signal.h", "time.h",
        "stdint.h",
    ):
        return 0

    library, module = _extract_library_and_module(source_file)
    stem = _extract_stem(source_file)

    # Self header — stem match (itbombhei.h for itbombhei.c)
    header_stem = header_name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if header_stem.lower() == stem.lower():
        return 100

    # Normalize header_name for path comparisons
    hn = header_name

    # Fighter character headers — special handling
    if _FIGHTER_CHAR_RE.match(hn):
        # Extract the character dir from the header (e.g., "ftKirby" from "ftKirby/types.h")
        char_dir = hn.split("/")[0]
        # Score 90 if source is in that character's module
        if f"chara/{char_dir}" in module or module.startswith(char_dir):
            return 90
        # Otherwise irrelevant cross-library character types
        return 0

    # Same module directory
    if module and hn.startswith(module + "/"):
        return 90

    # Same library (e.g., "it/types.h" for source in "it/items/")
    if library and (
        hn.startswith(library + "/")
        or hn.startswith(library.lower() + "/")
    ):
        return 80

    # Core types
    if hn in ("dolphin/types.h", "platform.h", "placeholder.h"):
        return 70

    # Baselib core (HSD_GObj, HSD_JObj — the most commonly needed)
    if hn.startswith("baselib/") or hn.startswith("sysdolphin/"):
        core_baselib = (
            "gobj", "jobj", "dobj", "mobj", "cobj", "lobj", "tobj",
            "fobj", "aobj", "robj",
        )
        bl_stem = hn.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        if bl_stem in core_baselib or bl_stem == "forward":
            return 60
        return 35

    # Peer melee library types/forward
    peer_parts = hn.split("/")
    if len(peer_parts) >= 2:
        peer_lib = peer_parts[0]
        peer_file = peer_parts[-1].lower()
        # Recognized melee libraries
        melee_libs = {"ft", "it", "lb", "gr", "gm", "mn", "cm", "db", "ef", "pl", "mp", "sc", "vi", "ty", "un"}
        if peer_lib in melee_libs and peer_lib != library:
            if "forward" in peer_file or "types" in peer_file:
                return 50
            return 40

    # Common structs
    if "common_structs" in hn.lower():
        return 40

    # cmath.h (bundled with dolphin/types.h typically)
    if hn == "cmath.h":
        return 30

    # Dolphin math
    if hn.startswith("dolphin/mtx") or hn.startswith("dolphin/vec"):
        return 30

    # Dolphin GX
    if hn.startswith("dolphin/gx"):
        return 10

    # Dolphin OS — low priority
    if hn.startswith("dolphin/os"):
        return 5

    # Other dolphin SDK
    if hn.startswith("dolphin/"):
        return 10

    # MSL / Runtime
    if hn.startswith("MSL/") or hn.startswith("Runtime/"):
        return 0

    # Anything else — moderate default
    return 20


def _compact_content(content: str) -> str:
    """Remove noise lines and collapse excessive blank lines."""
    lines = content.splitlines()
    result: list[str] = []
    blank_count = 0

    for line in lines:
        if _NOISE_RE.match(line):
            continue
        if not line.strip():
            blank_count += 1
            if blank_count <= 1:
                result.append(line)
        else:
            blank_count = 0
            result.append(line)

    return "\n".join(result)


def filter_ctx(
    ctx_text: str,
    source_file: str,
    budget_chars: int = 20_000,
) -> str:
    """Filter a .ctx file to the most relevant headers within a budget.

    1. Parse into sections
    2. Deduplicate by header_name (keep first non-empty)
    3. Score each section
    4. Sort by score desc, then by original file position
    5. Include sections in order until budget is filled
    6. Compact content
    7. Append count of excluded headers
    """
    sections = parse_ctx_sections(ctx_text)

    if not sections:
        return ctx_text[:budget_chars] if len(ctx_text) > budget_chars else ctx_text

    # Deduplicate: keep first non-empty occurrence of each header
    seen: dict[str, int] = {}
    deduped: list[CtxSection] = []
    for sec in sections:
        if sec.header_name in seen:
            # Replace if previous was empty and this one isn't
            if not sec.is_empty and sections[seen[sec.header_name]].is_empty:
                deduped[seen[sec.header_name]] = sec
            continue
        seen[sec.header_name] = len(deduped)
        deduped.append(sec)

    # Score and sort
    scored = [
        (score_section(sec.header_name, source_file, sec.is_empty), idx, sec)
        for idx, sec in enumerate(deduped)
    ]
    # Sort by score descending, then original position ascending
    scored.sort(key=lambda x: (-x[0], x[1]))

    # Select sections within budget
    selected_indices: list[int] = []
    total_chars = 0
    excluded_count = 0

    for score, idx, sec in scored:
        if score == 0:
            excluded_count += 1
            continue
        compacted = _compact_content(sec.content)
        section_size = len(compacted) + len(sec.header_name) + 20  # marker overhead
        if total_chars + section_size <= budget_chars:
            selected_indices.append(idx)
            total_chars += section_size
        else:
            excluded_count += 1

    # Output in original file order
    selected_indices.sort()

    parts: list[str] = []
    for idx in selected_indices:
        sec = deduped[idx]
        compacted = _compact_content(sec.content)
        if compacted.strip():
            parts.append(f"/* === {sec.header_name} === */")
            parts.append(compacted)

    if excluded_count > 0:
        parts.append(
            f"\n/* {excluded_count} additional headers excluded (lower relevance) */"
        )

    return "\n".join(parts)
