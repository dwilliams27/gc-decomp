"""Parse melee's configure.py to extract object status information."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ObjectStatus(Enum):
    MATCHING = "Matching"
    NON_MATCHING = "NonMatching"
    EQUIVALENT = "Equivalent"


@dataclass
class ObjectEntry:
    """A single translation unit (C file) in the melee project."""

    name: str  # e.g. "melee/lb/lbcommand.c"
    status: ObjectStatus
    library: str  # e.g. "lb (Library)"

    @property
    def source_path(self) -> str:
        """Path relative to src/ in the melee repo."""
        return self.name

    @property
    def is_matching(self) -> bool:
        return self.status == ObjectStatus.MATCHING

    @property
    def is_non_matching(self) -> bool:
        return self.status == ObjectStatus.NON_MATCHING


# Regex to match library definitions like: MeleeLib("lb (Library)", [...])
# Captures the library type and library name
_LIB_RE = re.compile(
    r"(?:MeleeLib|DolphinLib|SysdolphinLib|RuntimeLib|TRKLib|Lib)\(\s*"
    r'"([^"]+)"',
)

# Regex to match Object() declarations
# Captures status and filename
_OBJECT_RE = re.compile(
    r"Object\(\s*(Matching|NonMatching|Equivalent)\s*,\s*"
    r'"([^"]+)"',
)

_STATUS_MAP = {
    "Matching": ObjectStatus.MATCHING,
    "NonMatching": ObjectStatus.NON_MATCHING,
    "Equivalent": ObjectStatus.EQUIVALENT,
}


def parse_configure_py(configure_path: Path) -> list[ObjectEntry]:
    """Parse configure.py and extract all Object() declarations with status.

    First finds all library definitions and their positions in the file,
    then finds all Object() declarations and assigns each to the most
    recent library definition that precedes it.
    """
    text = configure_path.read_text()

    # Find all library definitions with their positions
    lib_positions: list[tuple[int, str]] = []
    for m in _LIB_RE.finditer(text):
        lib_positions.append((m.start(), m.group(1)))

    # Find all Object declarations with their positions
    objects: list[ObjectEntry] = []
    for m in _OBJECT_RE.finditer(text):
        pos = m.start()
        status_str, name = m.group(1), m.group(2)

        # Find the most recent library definition before this Object
        current_lib = "<unknown>"
        for lib_pos, lib_name in lib_positions:
            if lib_pos < pos:
                current_lib = lib_name
            else:
                break

        objects.append(
            ObjectEntry(
                name=name,
                status=_STATUS_MAP[status_str],
                library=current_lib,
            )
        )

    return objects


def get_object_map(configure_path: Path) -> dict[str, ObjectEntry]:
    """Return a dict mapping object name -> ObjectEntry."""
    return {obj.name: obj for obj in parse_configure_py(configure_path)}


def get_status_counts(objects: list[ObjectEntry]) -> dict[ObjectStatus, int]:
    """Count objects by status."""
    counts: dict[ObjectStatus, int] = {s: 0 for s in ObjectStatus}
    for obj in objects:
        counts[obj.status] += 1
    return counts
