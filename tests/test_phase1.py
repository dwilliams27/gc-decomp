"""Tests for Phase 1: Melee repo integration."""

import json
import tempfile
from pathlib import Path

from decomp_agent.melee.project import (
    ObjectEntry,
    ObjectStatus,
    get_object_map,
    get_status_counts,
    parse_configure_py,
)
from decomp_agent.melee.report import Report, parse_report
from decomp_agent.melee.functions import FunctionInfo, get_candidates


MELEE_REPO = Path("/Users/dwilliams/proj/melee")


# --- project.py tests ---


def test_parse_configure_py():
    objects = parse_configure_py(MELEE_REPO / "configure.py")
    assert len(objects) > 900  # Should be ~970
    counts = get_status_counts(objects)
    assert counts[ObjectStatus.MATCHING] > 600
    assert counts[ObjectStatus.NON_MATCHING] > 200
    assert counts[ObjectStatus.EQUIVALENT] >= 1


def test_object_map():
    obj_map = get_object_map(MELEE_REPO / "configure.py")
    # Spot-check known objects
    assert "melee/lb/lbcommand.c" in obj_map
    assert obj_map["melee/lb/lbcommand.c"].status == ObjectStatus.MATCHING


def test_library_assignment():
    objects = parse_configure_py(MELEE_REPO / "configure.py")
    # All objects should have a library assigned
    for obj in objects:
        assert obj.library != "<unknown>", f"Object {obj.name} has no library"


def test_object_properties():
    obj = ObjectEntry(name="test.c", status=ObjectStatus.MATCHING, library="test")
    assert obj.is_matching
    assert not obj.is_non_matching
    assert obj.source_path == "test.c"


# --- report.py tests ---


SAMPLE_REPORT = {
    "measures": {
        "total_code": "1000",
        "matched_code": "500",
        "matched_code_percent": "50.0",
        "total_data": "200",
        "matched_data": "100",
        "matched_data_percent": "50.0",
        "total_functions": "10",
        "matched_functions": "5",
        "complete_code": "500",
        "complete_code_percent": "50.0",
        "total_units": "3",
        "complete_units": "1",
    },
    "categories": [
        {
            "id": "game",
            "name": "Game Code",
            "measures": {
                "total_code": "800",
                "matched_code": "400",
                "matched_code_percent": "50.0",
            },
        }
    ],
    "units": [
        {
            "name": "main/melee/lb/lbcommand",
            "functions": [
                {
                    "name": "lbCommand_Init",
                    "size": "64",
                    "fuzzy_match_percent": "100.0",
                    "metadata": {"virtual_address": "0x80005940"},
                },
                {
                    "name": "lbCommand_Process",
                    "size": "128",
                    "fuzzy_match_percent": "75.5",
                    "metadata": {"virtual_address": "0x80005980"},
                },
            ],
        },
        {
            "name": "main/melee/ft/fighter",
            "functions": [
                {
                    "name": "Fighter_Init",
                    "size": "256",
                    "fuzzy_match_percent": "0.0",
                    "metadata": {"virtual_address": "0x800A0000"},
                },
                None,  # objdiff can produce null entries
            ],
        },
    ],
}


def test_parse_report():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_REPORT, f)
        f.flush()
        report = parse_report(Path(f.name))

    assert report.measures.total_code == 1000
    assert report.measures.matched_code == 500
    assert report.measures.matched_code_percent == 50.0
    assert len(report.units) == 2
    assert "game" in report.categories


def test_report_unit_functions():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_REPORT, f)
        f.flush()
        report = parse_report(Path(f.name))

    unit = report.get_unit("melee/lb/lbcommand")
    assert unit is not None
    assert unit.total_functions == 2
    assert unit.matched_functions == 1

    func = report.get_function("lbCommand_Init")
    assert func is not None
    assert func.is_matched
    assert func.size == 64
    assert func.virtual_address == 0x80005940


def test_report_null_function_entries():
    """Null entries in function lists should be skipped."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_REPORT, f)
        f.flush()
        report = parse_report(Path(f.name))

    unit = report.get_unit("melee/ft/fighter")
    assert unit is not None
    assert unit.total_functions == 1  # null entry skipped


def test_unmatched_functions():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(SAMPLE_REPORT, f)
        f.flush()
        report = parse_report(Path(f.name))

    unmatched = report.unmatched_functions()
    assert len(unmatched) == 1  # only 0.0% function (75.5% > 0.0 default)

    # With max_match_percent=99 to get partially matched too
    partial = report.unmatched_functions(max_match_percent=99.0)
    assert len(partial) == 2  # 75.5% and 0.0%

    # Filter by size
    small = report.unmatched_functions(max_match_percent=99.0, max_size=200)
    assert len(small) == 1  # only 128-byte function (256 is too big)


# --- functions.py tests ---


def test_get_candidates():
    functions = [
        FunctionInfo(
            name="fn_matched",
            address=0x80000000,
            size=64,
            fuzzy_match_percent=100.0,
            unit_name="melee/lb/lbcommand",
            source_file="melee/lb/lbcommand.c",
            object_status=ObjectStatus.MATCHING,
            library="lb",
        ),
        FunctionInfo(
            name="fn_candidate",
            address=0x80000100,
            size=32,
            fuzzy_match_percent=50.0,
            unit_name="melee/lb/lbcollision",
            source_file="melee/lb/lbcollision.c",
            object_status=ObjectStatus.NON_MATCHING,
            library="lb",
        ),
        FunctionInfo(
            name="fn_big_candidate",
            address=0x80000200,
            size=1024,
            fuzzy_match_percent=0.0,
            unit_name="melee/ft/fighter",
            source_file="melee/ft/fighter.c",
            object_status=ObjectStatus.NON_MATCHING,
            library="ft",
        ),
    ]

    candidates = get_candidates(functions)
    assert len(candidates) == 2
    assert candidates[0].name == "fn_candidate"  # smaller first

    small_candidates = get_candidates(functions, max_size=512)
    assert len(small_candidates) == 1
    assert small_candidates[0].name == "fn_candidate"
