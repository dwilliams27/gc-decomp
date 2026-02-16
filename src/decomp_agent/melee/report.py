"""Parse objdiff-cli report.json for per-unit and per-function match data."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FunctionReport:
    """Per-function match data from objdiff report."""

    name: str
    size: int
    fuzzy_match_percent: float
    virtual_address: int
    unit_name: str  # e.g. "main/melee/lb/lbcommand"

    @property
    def is_matched(self) -> bool:
        return self.fuzzy_match_percent == 100.0

    @property
    def source_name(self) -> str:
        """Strip 'main/' prefix to get the path relative to src/."""
        name = self.unit_name
        if name.startswith("main/"):
            name = name[5:]
        return name


@dataclass
class UnitReport:
    """Per-translation-unit data from objdiff report."""

    name: str  # e.g. "main/melee/lb/lbcommand"
    functions: list[FunctionReport] = field(default_factory=list)

    @property
    def source_name(self) -> str:
        name = self.name
        if name.startswith("main/"):
            name = name[5:]
        return name

    @property
    def total_functions(self) -> int:
        return len(self.functions)

    @property
    def matched_functions(self) -> int:
        return sum(1 for f in self.functions if f.is_matched)

    @property
    def match_percent(self) -> float:
        if not self.functions:
            return 0.0
        return sum(f.fuzzy_match_percent for f in self.functions) / len(self.functions)


@dataclass
class Measures:
    """Global or per-category progress measures."""

    total_code: int = 0
    matched_code: int = 0
    matched_code_percent: float = 0.0
    total_data: int = 0
    matched_data: int = 0
    matched_data_percent: float = 0.0
    total_functions: int = 0
    matched_functions: int = 0
    complete_code: int = 0
    complete_code_percent: float = 0.0
    total_units: int = 0
    complete_units: int = 0


@dataclass
class Report:
    """Parsed objdiff report."""

    measures: Measures
    units: list[UnitReport]
    categories: dict[str, Measures] = field(default_factory=dict)

    def get_unit(self, name: str) -> UnitReport | None:
        """Find a unit by name (with or without 'main/' prefix)."""
        for unit in self.units:
            if unit.name == name or unit.source_name == name:
                return unit
        return None

    def get_function(self, func_name: str) -> FunctionReport | None:
        """Find a function by name across all units."""
        for unit in self.units:
            for func in unit.functions:
                if func.name == func_name:
                    return func
        return None

    def unmatched_functions(
        self,
        max_match_percent: float = 0.0,
        max_size: int | None = None,
        min_size: int = 0,
    ) -> list[FunctionReport]:
        """Get unmatched functions, optionally filtered by size and match %."""
        results = []
        for unit in self.units:
            for func in unit.functions:
                if func.fuzzy_match_percent > max_match_percent:
                    continue
                if func.size < min_size:
                    continue
                if max_size is not None and func.size > max_size:
                    continue
                results.append(func)
        return results

    @property
    def all_functions(self) -> list[FunctionReport]:
        return [f for u in self.units for f in u.functions]


def _parse_measures(data: dict) -> Measures:
    """Parse measures dict, converting string numbers to proper types."""

    def to_int(v: str | int) -> int:
        return int(v) if isinstance(v, str) else v

    def to_float(v: str | float) -> float:
        return float(v) if isinstance(v, str) else v

    return Measures(
        total_code=to_int(data.get("total_code", 0)),
        matched_code=to_int(data.get("matched_code", 0)),
        matched_code_percent=to_float(data.get("matched_code_percent", 0)),
        total_data=to_int(data.get("total_data", 0)),
        matched_data=to_int(data.get("matched_data", 0)),
        matched_data_percent=to_float(data.get("matched_data_percent", 0)),
        total_functions=to_int(data.get("total_functions", 0)),
        matched_functions=to_int(data.get("matched_functions", 0)),
        complete_code=to_int(data.get("complete_code", 0)),
        complete_code_percent=to_float(data.get("complete_code_percent", 0)),
        total_units=to_int(data.get("total_units", 0)),
        complete_units=to_int(data.get("complete_units", 0)),
    )


def _parse_function(data: dict, unit_name: str) -> FunctionReport:
    metadata = data.get("metadata", {})
    return FunctionReport(
        name=data["name"],
        size=int(data.get("size", 0)),
        fuzzy_match_percent=float(data.get("fuzzy_match_percent", 0)),
        virtual_address=int(metadata.get("virtual_address", "0"), 0),
        unit_name=unit_name,
    )


def _parse_unit(data: dict) -> UnitReport:
    unit_name = data["name"]
    functions = [
        _parse_function(f, unit_name)
        for f in data.get("functions", [])
        if f is not None
    ]
    return UnitReport(name=unit_name, functions=functions)


def parse_report(report_path: Path) -> Report:
    """Parse a report.json file into structured data."""
    with open(report_path) as f:
        data = json.load(f)

    measures = _parse_measures(data.get("measures", {}))
    units = [_parse_unit(u) for u in data.get("units", [])]
    categories = {
        cat["id"]: _parse_measures(cat["measures"])
        for cat in data.get("categories", [])
    }

    return Report(measures=measures, units=units, categories=categories)


def generate_report(config: object) -> Path:
    """Run ninja to generate report.json in the melee repo.

    Args:
        config: A Config instance (from decomp_agent.config).

    Returns the path to the generated report.json.
    """
    from decomp_agent.config import Config
    from decomp_agent.tools.run import run_in_repo

    assert isinstance(config, Config)
    report_rel = f"{config.melee.build_dir}/{config.melee.version}/report.json"
    result = run_in_repo(["ninja", report_rel], config=config)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to generate report:\n{result.stdout}\n{result.stderr}"
        )
    return config.melee.report_path
