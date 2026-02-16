"""Unified function list combining configure.py status and report.json match data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .project import ObjectEntry, ObjectStatus, get_object_map
from .report import Report, parse_report

if TYPE_CHECKING:
    from decomp_agent.config import Config


@dataclass
class FunctionInfo:
    """Complete information about a function in the melee decomp."""

    name: str
    address: int
    size: int
    fuzzy_match_percent: float
    unit_name: str  # e.g. "melee/lb/lbcommand" (no .c extension)
    source_file: str  # e.g. "melee/lb/lbcommand.c"
    object_status: ObjectStatus  # Matching/NonMatching/Equivalent from configure.py
    library: str  # Library name from configure.py

    @property
    def is_matched(self) -> bool:
        return self.fuzzy_match_percent == 100.0

    @property
    def is_decompiled(self) -> bool:
        """True if the containing object has any C source (NonMatching or Matching)."""
        return self.object_status != ObjectStatus.EQUIVALENT

    @property
    def is_candidate(self) -> bool:
        """True if this function is a good candidate for AI decompilation.

        Must be unmatched and in a NonMatching object (has existing C source).
        """
        return (
            not self.is_matched
            and self.object_status == ObjectStatus.NON_MATCHING
        )


def _match_unit_to_object(
    unit_source_name: str, object_map: dict[str, ObjectEntry]
) -> ObjectEntry | None:
    """Match a report unit name to a configure.py object entry.

    Report unit names are like "melee/lb/lbcommand" (no .c extension).
    Object names are like "melee/lb/lbcommand.c".
    """
    # Try with .c extension
    key = unit_source_name + ".c"
    if key in object_map:
        return object_map[key]

    # Try with .cpp extension
    key = unit_source_name + ".cpp"
    if key in object_map:
        return object_map[key]

    return None


def get_functions(
    config: Config | None = None,
    report: Report | None = None,
    *,
    melee_repo: Path | None = None,
) -> list[FunctionInfo]:
    """Get the unified function list combining configure.py and report.json data.

    Args:
        config: Project configuration (preferred). Provides repo path and version.
        report: Pre-parsed report (if None, loads from default path)
        melee_repo: Path to the melee repository (deprecated, use config instead)
    """
    if config is not None:
        repo = config.melee.repo_path
        version = config.melee.version
    elif melee_repo is not None:
        repo = melee_repo
        version = "GALE01"
    else:
        raise ValueError("Either config or melee_repo must be provided")

    configure_path = repo / "configure.py"
    object_map = get_object_map(configure_path)

    if report is None:
        report_path = repo / "build" / version / "report.json"
        if not report_path.exists():
            raise FileNotFoundError(
                f"report.json not found at {report_path}. "
                f"Run 'ninja build/{version}/report.json' in the melee repo first."
            )
        report = parse_report(report_path)

    functions: list[FunctionInfo] = []

    for unit in report.units:
        source_name = unit.source_name
        obj_entry = _match_unit_to_object(source_name, object_map)

        # Default to NonMatching if we can't match to configure.py
        obj_status = obj_entry.status if obj_entry else ObjectStatus.NON_MATCHING
        obj_library = obj_entry.library if obj_entry else "<unknown>"
        source_file = obj_entry.name if obj_entry else source_name + ".c"

        for func in unit.functions:
            functions.append(
                FunctionInfo(
                    name=func.name,
                    address=func.virtual_address,
                    size=func.size,
                    fuzzy_match_percent=func.fuzzy_match_percent,
                    unit_name=source_name,
                    source_file=source_file,
                    object_status=obj_status,
                    library=obj_library,
                )
            )

    return functions


def get_candidates(
    functions: list[FunctionInfo],
    max_size: int | None = None,
    min_size: int = 0,
) -> list[FunctionInfo]:
    """Get functions that are candidates for AI decompilation, sorted by size."""
    candidates = [
        f
        for f in functions
        if f.is_candidate
        and f.size >= min_size
        and (max_size is None or f.size <= max_size)
    ]
    candidates.sort(key=lambda f: (f.size, f.address))
    return candidates


def print_summary(functions: list[FunctionInfo]) -> None:
    """Print a summary of function match status."""
    total = len(functions)
    matched = sum(1 for f in functions if f.is_matched)
    candidates = sum(1 for f in functions if f.is_candidate)

    by_status: dict[ObjectStatus, int] = {}
    for f in functions:
        by_status[f.object_status] = by_status.get(f.object_status, 0) + 1

    print(f"Total functions: {total:,}")
    print(f"Matched:         {matched:,} ({matched / total * 100:.1f}%)")
    print(f"Candidates:      {candidates:,}")
    print()
    print("By object status:")
    for status in ObjectStatus:
        count = by_status.get(status, 0)
        print(f"  {status.value:15s}: {count:,}")
