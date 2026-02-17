"""Build a minimal fixture melee repo for E2E integration tests.

Creates the directory structure, configure.py, source file, assembly,
context, and report.json that the real tool pipeline expects.
"""

from __future__ import annotations

import json
from pathlib import Path

from decomp_agent.config import Config, MeleeConfig


# ---------------------------------------------------------------------------
# configure.py template
# ---------------------------------------------------------------------------

_CONFIGURE_PY = """\
from lib import *

config = ProjectConfig()
config.version = "GALE01"

config.libs = [
    MeleeLib(
        "test (Library)",
        [
            Object(NonMatching, "melee/test/testfile.c"),
        ],
    ),
]
"""

# ---------------------------------------------------------------------------
# C source file with 3 small functions
# ---------------------------------------------------------------------------

_SOURCE_C = """\
#include <dolphin/types.h>

void simple_init(s32* buf, s32 count) {
    s32 i;
    for (i = 0; i < count; i++) {
        buf[i] = 0;
    }
}

s32 simple_add(s32 a, s32 b) {
    return a + b;
}

s32 simple_loop(s32 n) {
    s32 result = 0;
    s32 i;
    for (i = 0; i < n; i++) {
        result += i;
    }
    return result;
}
"""

# ---------------------------------------------------------------------------
# dtk-style assembly for all 3 functions
# ---------------------------------------------------------------------------

_ASM = """\
.include "macros.inc"

.section .text, "ax"

.global simple_init
simple_init:
    li      r5, 0
    mtctr   r4
    cmpwi   r4, 0
    blelr
.loop_init:
    stw     r5, 0(r3)
    addi    r3, r3, 4
    bdnz    .loop_init
    blr

.global simple_add
simple_add:
    add     r3, r3, r4
    blr

.global simple_loop
simple_loop:
    li      r4, 0
    li      r5, 0
    mtctr   r3
    cmpwi   r3, 0
    blelr
.loop_sum:
    add     r4, r4, r5
    addi    r5, r5, 1
    bdnz    .loop_sum
    mr      r3, r4
    blr
"""

# ---------------------------------------------------------------------------
# Minimal preprocessed context
# ---------------------------------------------------------------------------

_CTX = """\
typedef signed long s32;
typedef unsigned long u32;
typedef signed short s16;
typedef unsigned short u16;
typedef signed char s8;
typedef unsigned char u8;
"""

# ---------------------------------------------------------------------------
# report.json helpers
# ---------------------------------------------------------------------------


def _build_report(
    match_overrides: dict[str, float] | None = None,
) -> dict:
    """Build a report.json dict with optional per-function match overrides.

    Default match percentages (initial state):
      simple_init: 55.0
      simple_add:  60.0
      simple_loop: 50.0
    """
    defaults = {
        "simple_init": 55.0,
        "simple_add": 60.0,
        "simple_loop": 50.0,
    }
    if match_overrides:
        defaults.update(match_overrides)

    functions = []
    addresses = {"simple_init": 0x800A0000, "simple_add": 0x800A0040, "simple_loop": 0x800A0080}
    sizes = {"simple_init": 40, "simple_add": 8, "simple_loop": 48}

    for name in ["simple_init", "simple_add", "simple_loop"]:
        pct = defaults[name]
        functions.append({
            "name": name,
            "size": sizes[name],
            "fuzzy_match_percent": pct,
            "metadata": {"virtual_address": hex(addresses[name])},
        })

    return {
        "measures": {
            "total_code": 96,
            "matched_code": 0,
            "matched_code_percent": 0.0,
            "total_functions": 3,
            "matched_functions": 0,
        },
        "units": [
            {
                "name": "main/melee/test/testfile",
                "functions": functions,
            }
        ],
        "categories": [],
    }


def write_report(
    repo_path: Path,
    match_overrides: dict[str, float] | None = None,
    version: str = "GALE01",
) -> Path:
    """Write a report.json with the specified match percentages.

    Used by the run_in_repo mock side effect to simulate ninja report output.
    """
    report_path = repo_path / "build" / version / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_data = _build_report(match_overrides)
    report_path.write_text(json.dumps(report_data, indent=2))
    return report_path


def create_fake_repo(tmp_path: Path) -> tuple[Path, Config]:
    """Build a minimal fixture melee repo and return (repo_path, config).

    Creates the full directory structure:
      {tmp_path}/
        configure.py
        src/melee/test/testfile.c
        build/GALE01/
          asm/melee/test/testfile.s
          src/melee/test/testfile.ctx
          report.json
    """
    repo = tmp_path / "melee_repo"

    # configure.py
    repo.mkdir(parents=True)
    (repo / "configure.py").write_text(_CONFIGURE_PY)

    # Source file
    src_dir = repo / "src" / "melee" / "test"
    src_dir.mkdir(parents=True)
    (src_dir / "testfile.c").write_text(_SOURCE_C)

    # Assembly
    asm_dir = repo / "build" / "GALE01" / "asm" / "melee" / "test"
    asm_dir.mkdir(parents=True)
    (asm_dir / "testfile.s").write_text(_ASM)

    # Context
    ctx_dir = repo / "build" / "GALE01" / "src" / "melee" / "test"
    ctx_dir.mkdir(parents=True)
    (ctx_dir / "testfile.ctx").write_text(_CTX)

    # Initial report.json (50-60% matches)
    write_report(repo)

    # Build the Config
    config = Config(
        melee=MeleeConfig(repo_path=repo),
    )

    return repo, config
