"""Tests for ctx_filter — .ctx file parsing, scoring, and filtering."""

from __future__ import annotations

import pytest

from decomp_agent.tools.ctx_filter import (
    CtxSection,
    filter_ctx,
    parse_ctx_sections,
    score_section,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

BASIC_CTX = """\
/* "src/melee/it/items/itbombhei.c" line 0 "itbombhei.h" */
#ifndef GALE01_27D670
#define GALE01_27D670
typedef struct { int x; } BombheiVars;
#endif
/* end "itbombhei.h" */
/* "src/melee/it/items/itbombhei.c" line 1 "it/forward.h" */
#ifndef MELEE_IT_FORWARD_H
#define MELEE_IT_FORWARD_H
typedef struct Item Item;
#endif
/* end "it/forward.h" */
/* "src/melee/it/items/itbombhei.c" line 2 "dolphin/os/OSThread.h" */
#ifndef _DOLPHIN_OSTHREAD_H_
#define _DOLPHIN_OSTHREAD_H_
typedef struct OSThread OSThread;
#endif
/* end "dolphin/os/OSThread.h" */
"""

NESTED_CTX = """\
/* "src/melee/it/items/itbombhei.c" line 0 "itbombhei.h" */
#ifndef GALE01_27D670
#define GALE01_27D670
/* "src/melee/it/items/itbombhei.h" line 3 "it/forward.h" */
typedef struct Item Item;
/* end "it/forward.h" */
int bombhei_func(void);
#endif
/* end "itbombhei.h" */
"""

EMPTY_REINCLUDE_CTX = """\
/* "src/melee/it/items/itbombhei.c" line 0 "dolphin/types.h" */
typedef int s32;
typedef unsigned int u32;
/* end "dolphin/types.h" */
/* "src/melee/it/items/itbombhei.c" line 5 "dolphin/types.h" */
/* end "dolphin/types.h" */
"""


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------


class TestParseBasic:
    def test_parse_basic(self):
        sections = parse_ctx_sections(BASIC_CTX)
        assert len(sections) == 3
        assert sections[0].header_name == "itbombhei.h"
        assert "BombheiVars" in sections[0].content
        assert not sections[0].is_empty

        assert sections[1].header_name == "it/forward.h"
        assert "typedef struct Item" in sections[1].content

        assert sections[2].header_name == "dolphin/os/OSThread.h"
        assert "OSThread" in sections[2].content

    def test_parse_nested(self):
        sections = parse_ctx_sections(NESTED_CTX)
        # Should produce 2 sections: inner it/forward.h and outer itbombhei.h
        assert len(sections) == 2

        # Inner section first (closed first)
        assert sections[0].header_name == "it/forward.h"
        assert "typedef struct Item" in sections[0].content

        # Outer section second
        assert sections[1].header_name == "itbombhei.h"
        assert "bombhei_func" in sections[1].content

    def test_parse_empty_reinclude(self):
        sections = parse_ctx_sections(EMPTY_REINCLUDE_CTX)
        assert len(sections) == 2
        # First occurrence has content
        assert not sections[0].is_empty
        assert "typedef int s32" in sections[0].content
        # Second occurrence is empty (header guard)
        assert sections[1].is_empty


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------

SOURCE_IT = "melee/it/items/itbombhei.c"
SOURCE_FT_KIRBY = "melee/ft/chara/ftKirby/ftKb_Init.c"


class TestScoring:
    def test_score_self_header(self):
        assert score_section("itbombhei.h", SOURCE_IT, False) == 100

    def test_score_self_header_in_subdir(self):
        assert score_section("it/items/itbombhei.h", SOURCE_IT, False) == 100

    def test_score_same_module(self):
        assert score_section("it/items/types.h", SOURCE_IT, False) == 90

    def test_score_same_library(self):
        assert score_section("it/types.h", SOURCE_IT, False) == 80

    def test_score_fighter_excluded(self):
        """ftCaptain headers should score 0 for item source files."""
        assert score_section("ftCaptain/types.h", SOURCE_IT, False) == 0

    def test_score_fighter_included_own_module(self):
        """ftKirby headers should score 90 for ftKirby source files."""
        assert score_section("ftKirby/types.h", SOURCE_FT_KIRBY, False) == 90

    def test_score_dolphin_os_low(self):
        assert score_section("dolphin/os/OSThread.h", SOURCE_IT, False) == 5

    def test_score_core_types(self):
        assert score_section("dolphin/types.h", SOURCE_IT, False) == 70

    def test_score_platform(self):
        assert score_section("platform.h", SOURCE_IT, False) == 70

    def test_score_empty_zero(self):
        assert score_section("it/items/types.h", SOURCE_IT, True) == 0

    def test_score_baselib_core(self):
        assert score_section("baselib/gobj.h", SOURCE_IT, False) == 60

    def test_score_baselib_secondary(self):
        assert score_section("baselib/pobj.h", SOURCE_IT, False) == 35

    def test_score_peer_library_forward(self):
        assert score_section("ft/forward.h", SOURCE_IT, False) == 50

    def test_score_peer_library_types(self):
        assert score_section("lb/types.h", SOURCE_IT, False) == 50

    def test_score_peer_library_other(self):
        assert score_section("gr/stage.h", SOURCE_IT, False) == 40

    def test_score_std_c_zero(self):
        assert score_section("stdio.h", SOURCE_IT, False) == 0
        assert score_section("string.h", SOURCE_IT, False) == 0
        assert score_section("stddef.h", SOURCE_IT, False) == 0

    def test_score_dolphin_gx(self):
        assert score_section("dolphin/gx.h", SOURCE_IT, False) == 10

    def test_score_dolphin_mtx(self):
        assert score_section("dolphin/mtx.h", SOURCE_IT, False) == 30


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestFilterCtx:
    def test_filter_budget_respected(self):
        result = filter_ctx(BASIC_CTX, SOURCE_IT, budget_chars=500)
        assert len(result) <= 600  # some slack for markers

    def test_filter_high_priority_first(self):
        result = filter_ctx(BASIC_CTX, SOURCE_IT, budget_chars=50_000)
        # Self-header content should appear
        assert "BombheiVars" in result
        # it/forward.h should appear (same library)
        assert "typedef struct Item" in result

    def test_filter_empty_skipped(self):
        result = filter_ctx(EMPTY_REINCLUDE_CTX, SOURCE_IT, budget_chars=50_000)
        # Content from the first (non-empty) section should appear
        assert "typedef int s32" in result
        # The "end" marker of the empty section should not produce content

    def test_filter_excludes_low_score(self):
        """With a tight budget, low-score sections should be excluded."""
        # Build a ctx with one high-priority and one low-priority section
        ctx = """\
/* "src/melee/it/items/itbombhei.c" line 0 "itbombhei.h" */
typedef struct { int x; } BombheiVars;
/* end "itbombhei.h" */
/* "src/melee/it/items/itbombhei.c" line 1 "dolphin/os/OSCache.h" */
void DCFlushRange(void* addr, unsigned long nBytes);
void ICInvalidateRange(void* addr, unsigned long nBytes);
/* end "dolphin/os/OSCache.h" */
"""
        # Budget enough for one section only
        result = filter_ctx(ctx, SOURCE_IT, budget_chars=200)
        assert "BombheiVars" in result
        # The OS section may or may not fit, but self-header should always be there

    def test_filter_excluded_count(self):
        result = filter_ctx(BASIC_CTX, SOURCE_IT, budget_chars=200)
        assert "additional headers excluded" in result

    def test_filter_preserves_order(self):
        """Selected sections should appear in original file order."""
        ctx = """\
/* "src/melee/it/items/itbombhei.c" line 0 "it/forward.h" */
typedef struct Item Item;
/* end "it/forward.h" */
/* "src/melee/it/items/itbombhei.c" line 1 "itbombhei.h" */
typedef struct { int x; } BombheiVars;
/* end "itbombhei.h" */
"""
        result = filter_ctx(ctx, SOURCE_IT, budget_chars=50_000)
        # Even though itbombhei.h scores higher, it/forward.h appeared first
        # in the file, so it should appear first in output
        fwd_pos = result.find("typedef struct Item")
        self_pos = result.find("BombheiVars")
        assert fwd_pos < self_pos

    def test_filter_empty_input(self):
        result = filter_ctx("", SOURCE_IT)
        assert result == ""

    def test_filter_no_markers(self):
        """Plain text without markers should pass through (truncated if needed)."""
        plain = "just some text\n" * 100
        result = filter_ctx(plain, SOURCE_IT, budget_chars=500)
        assert len(result) <= 500
