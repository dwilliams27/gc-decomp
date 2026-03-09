# mnGallery_80259868 -- Attempt Analysis Report

**Generated:** 2026-03-08
**Source file:** `melee/mn/mngallery.c`
**Function size:** 388 bytes (98 instructions)
**Current match:** 87.95% (fuzzy), 91.28% (structural)
**Status:** pending (not matched)
**Total attempts:** 4

---

## Function Overview

`mnGallery_80259868` is an initialization/setup function in the Gallery menu module. It:

1. Sets menu state fields (`cooldown`, `prev_menu`, `cur_menu`, `hovered_selection`)
2. Loads archive sections via `lbArchive_LoadSections()` with 8 section pairs (array + base offsets)
3. Creates GObj structures and attaches process callbacks
4. Calls `mnGallery_8025963C()` (a sibling init function, now 100% matched)
5. Creates an HSD_Text widget with specific font sizes and positioning
6. Creates a camera GObj and configures it

---

## Attempt History

### Attempt 1 (ID=20863) -- Run: legacy (no run_id)
- **Date:** 2026-03-07 05:27
- **Model:** claude-code-headless
- **Warm start:** No
- **Before match:** 0.0% | **Best match:** 0.0%
- **Iterations:** 30 (hit max_iterations)
- **Elapsed:** 129s
- **Final code:** None (no code produced)
- **Notes:** This was likely the first attempt when the function was still a stub (`/// #mnGallery_80259868`). The agent ran 30 iterations but produced no compilable code. Session `4a6a286c`, tokens: 4,335 output. The very low token count and zero match suggest the agent struggled to even get started -- possibly couldn't compile at all, or the stub replacement machinery wasn't working yet.

### Attempt 2 (ID=20874) -- Run 1 (file-mode)
- **Date:** 2026-03-07 20:06
- **Model:** claude-code-headless
- **Warm start:** No
- **Before match:** 0.0% | **Best match:** 0.0%
- **Iterations:** 0
- **Termination:** "matched" (misleading -- this was a file-mode run)
- **Final code:** None
- **Notes:** This was part of a file-mode session (Run 1) targeting all of mngallery.c. The run completed 100 iterations across the whole file and spent ~825s / 24.5K output tokens. The "matched" termination reason and 0 iterations for this specific function means the file-mode agent likely focused on other functions and never touched `mnGallery_80259868`, or briefly looked at it but didn't improve it. This run is the one that matched several sibling functions.

### Attempt 3 (ID=21129) -- Run 9 (function-mode, best result)
- **Date:** 2026-03-08 07:05
- **Model:** claude-code-headless
- **Warm start:** No
- **Before match:** 64.0% | **Best match:** 87.95%
- **Iterations:** 31 (hit max_iterations)
- **Elapsed:** 1,379s (~23 min)
- **Tokens:** 67.8K output, 2.6M cached
- **Final code:** 1,559 chars (full implementation -- see below)
- **Notes:** The function entered at 64% (from prior work, possibly from the file-mode run or manual edits) and the agent pushed it to 87.95%. This was a dedicated function-mode run. The agent clearly identified the correct structure: extern arrays, `lbArchive_LoadSections` call pattern, GObj creation, HSD_Text setup, camera creation. But it plateaued at ~88% and couldn't close the remaining gap within 31 iterations.

### Attempt 4 (ID=21131) -- Run 11 (function-mode, warm start, REGRESSION)
- **Date:** 2026-03-08 08:29
- **Model:** claude-code-headless
- **Warm start:** Yes (fed the 87.95% code from attempt 3)
- **Before match:** 87.95% | **Best match:** 39.80%
- **Iterations:** 81 (hit max_iterations)
- **Elapsed:** 3,123s (~52 min)
- **Tokens:** 158.4K output, 8.2M cached
- **Final code:** 1,565 chars (nearly identical to attempt 3)
- **Notes:** **Major regression.** The warm-start run was given the 87.95% code as a starting point but somehow ended at 39.80%. The final code in the DB is nearly identical to attempt 3's code (only difference: added array size annotations `[0x200]` and `[8]`), so the 39.80% was likely measured at an intermediate point where the agent had destructively experimented. The agent spent 81 iterations and 52 minutes -- more than 3x the resources of attempt 3 -- yet ended worse. This suggests the agent went down a wrong path and couldn't recover.

---

## Current Code in Source (lines 562-618)

The code currently committed to the melee repo matches the best attempt (attempt 3 / 87.95%):

```c
void mnGallery_80259868(void)
{
    extern char mnGallery_803F0570[];
    extern void* mnGallery_804A0BA0[];

    char* base = mnGallery_803F0570;
    void** arr = mnGallery_804A0BA0;
    HSD_Archive* archive;
    HSD_GObj* gobj;
    HSD_GObjProc* proc;
    HSD_Text* text;
    HSD_GObj* cam_gobj;
    struct { ... }* inner;
    PAD_STACK(0x38);

    mn_804D6BC8.cooldown = 5;
    mn_804A04F0.prev_menu = mn_804A04F0.cur_menu;
    mn_804A04F0.cur_menu = 0x1A;
    mn_804A04F0.hovered_selection = 0;
    archive = mn_804D6BB8;

    lbArchive_LoadSections(archive, arr,
        base + 0x60, arr + 1, base + 0x78,
        arr + 2, base + 0x94, arr + 3, base + 0xB4,
        arr + 4, base + 0xD8, arr + 5, base + 0xF4,
        arr + 6, base + 0x114, arr + 7, base + 0x138,
        0);

    gobj = GObj_Create(0, 1, 0x80);
    proc = HSD_GObjProc_8038FD54(gobj, (HSD_GObjEvent) fn_80258ED0, 0);
    proc->flags_3 = HSD_GObj_804D783C;
    mnGallery_8025963C();

    inner = ((HSD_GObj*) mnGallery_804D6C88)->user_data;
    text = HSD_SisLib_803A5ACC(0, 1, -9.5F, 9.1F, 17.0F, 364.68332F, 38.38772F);
    text->font_size.x = 0.0521F;
    text->font_size.y = 0.0521F;
    HSD_SisLib_803A6368(text, 0xC7);
    inner->text = text;

    cam_gobj = GObj_Create(6, 3, 0x80);
    mnGallery_80258A08(cam_gobj, 0x280, 0x1E0, 1);
    cam_gobj->gxlink_prios = 0x100;
    inner->gobj4 = cam_gobj;
}
```

---

## Assembly Diff Analysis (the 12% gap)

The diff shows **19 differing instructions out of 98** (6 phantom diffs filtered). The mismatches are concentrated entirely in the **function prologue / variable setup region** (first ~35 instructions). The last 65 instructions match perfectly.

### Root cause: instruction scheduling in the prologue

The target binary interleaves variable loads and stores in a specific order that the compiler chose for pipeline optimization. The compiled code produces the same instructions but in a different order. This is a classic **CodeWarrior instruction scheduling** problem.

**Target order (simplified):**
```
lis r4, mnGallery_804A0BA0@ha     # load arr address (high)
addi r4, r4, mnGallery_804A0BA0@l # load arr address (low)
li r0, 0x5                        # cooldown = 5
lis r3, mn_804A04F0@ha            # load menu state addr
li r31, 0x0                       # hovered_selection = 0
addi r5, r10, 0xb4                # base + 0xB4
addi r7, r4, 0x10                 # arr + 4
addi r6, r4, 0x14                 # arr + 5
sth r0, mn_804D6BC8               # store cooldown
addi r9, r10, 0x114               # base + 0x114
addi r11, r4, 0x1c                # arr + 7
lbzu r0, mn_804A04F0@l(r3)        # load cur_menu
addi r8, r4, 0x8                  # arr + 2
stb r0, 0x1(r3)                   # store prev_menu
...
sth r31, 0x2(r3)                  # store hovered_selection
```

**Compiled order (what we get):**
```
li r0, 0x5                        # cooldown = 5
lis r3, mn_804A04F0@ha            # load menu state addr
li r31, 0x0                       # hovered_selection = 0
lis r4, mnGallery_804A0BA0@ha     # load arr address (high) -- LATE
addi r5, r10, 0xb4                # base + 0xB4
addi r9, r10, 0x114               # EARLY -- target has this later
sth r0, mn_804D6BC8               # store cooldown
lbzu r0, mn_804A04F0@l(r3)        # load cur_menu
stb r0, 0x1(r3)                   # store prev_menu
...
addi r0, r4, mnGallery_804A0BA0@l # DIFFERENT: uses r0 then mr
mr r4, r0                         # EXTRA instruction -- target does addi r4,r4 directly
sth r31, 0x2(r3)                  # store hovered_selection -- moved
addi r7, r4, 0x10                 # LATE -- target has these earlier
...
```

Key differences:
1. **`arr` (r4) address loading is split differently.** Target loads `lis r4` + `addi r4,r4` early (before the prologue stores). Compiled code loads `lis r4` later and uses an `addi r0,r4` + `mr r4,r0` sequence (1 extra instruction).
2. **Array offset computations (`arr+N`) are interleaved differently.** Target computes `addi r7/r6/r11/r8` (arr offsets) early, interleaved between the menu state setup. Compiled code delays them until after the menu state stores.
3. **`sth r31, 0x2(r3)` (store hovered_selection=0) placement differs.** Target has it late (after the `li r0, 0x1a` / `stb r0`), compiled code moves it earlier.

---

## Patterns and Observations

### What works
- The overall structure is correct: extern declarations, local variable types, function call sequence, argument values, floating-point constants.
- The last ~65 instructions (from `lbArchive_LoadSections` call onward) match perfectly.
- The inner struct layout and field accesses are correct.

### What doesn't work
- The prologue instruction scheduling is wrong. This is the only remaining issue.
- The compiler generates an extra `mr r4, r0` instruction that the target doesn't have, suggesting the variable declaration or initialization order affects how CodeWarrior allocates the `arr` pointer to a register.

### Warm-start regression
- Attempt 4 regressed from 87.95% to 39.80% despite starting with the best code. Warm starts on scheduling problems are counterproductive -- the agent tends to make large structural changes trying to fix small ordering issues, which breaks things that were already correct.

---

## Recommendations for Next Attempt

### 1. Focus exclusively on variable declaration and initialization order

The only remaining issue is instruction scheduling in the first ~30 instructions. CodeWarrior's instruction scheduler is sensitive to:
- **Order of local variable declarations** -- this affects register allocation
- **Order of assignments before the first function call** -- the compiler interleaves loads/stores based on source order
- **Whether `arr` is assigned from the extern before or after other variables**

The agent should try reordering the variable declarations and the initial assignment block. Specifically:

### 2. Try loading `arr` address earlier

The target loads `mnGallery_804A0BA0` into r4 very early (before the stack frame setup). The current code assigns `arr` after `base`, which may cause the compiler to delay the load. Try:
- Declaring `arr` before `base`
- Assigning `arr` before `base`
- Using `arr` in a computation before the menu state assignments

### 3. Try reordering the menu state assignments

The target interleaves the `arr+N` offset computations with the menu state stores. The current code does all menu state assignments first, then the `lbArchive_LoadSections` call. Try:
- Computing some arr offsets before the menu state stores
- Assigning `mn_804A04F0.hovered_selection = 0` later (after `cur_menu`)
- Moving the `archive = mn_804D6BB8` assignment to a different position

### 4. Try eliminating the extra `mr r4, r0`

The compiled code generates `addi r0, r4, lo` + `mr r4, r0` instead of `addi r4, r4, lo`. This suggests the compiler is using a temporary because r4 is still live from something else. The agent should try:
- Not using `arr` as an intermediate -- pass `mnGallery_804A0BA0` directly to `lbArchive_LoadSections`
- Using a different variable ordering so r4 is free when the low-address addi is emitted

### 5. Do NOT use warm start

The warm-start attempt was a catastrophic regression. For scheduling problems, the agent should start fresh each time. The code structure is correct; only the ordering needs to change. A fresh agent that reads the diff will naturally focus on the ordering issue without being biased by the existing (wrong-order) code.

### 6. Study matched sibling functions

Several sibling functions in `mngallery.c` are now 100% matched:
- `mnGallery_8025963C` (100%) -- called by this function; likely has similar setup patterns
- `mnGallery_802591BC` (100%) -- another complex function with GObj creation
- `mnGallery_80258BC4` (100%) -- has `lbArchive_LoadSections` calls

The agent should study these matched functions to see how they declare variables and order their prologue assignments. The same patterns that worked for those functions likely apply here.

### 7. Consider the permuter

If manual reordering doesn't work after 2-3 more attempts, this is a good candidate for the permuter tool (available in the API agent backend). The permuter can systematically try different variable orderings and find the one that matches.

---

## File-Level Context

| Function | Match % | Status |
|----------|---------|--------|
| mnGallery_80258940 | 100% | matched |
| mnGallery_8025896C | 100% | matched |
| mnGallery_80258A08 | 93.7% | pending |
| mnGallery_80258BC4 | 100% | matched |
| mnGallery_80258D50 | 100% | matched |
| mnGallery_80258DBC | 100% | matched |
| fn_80258ED0 | 100% | matched |
| fn_802590C4 | 82.3% | pending |
| mnGallery_802591BC | 100% | matched |
| mnGallery_80259604 | 100% | matched |
| mnGallery_8025963C | 100% | matched |
| **mnGallery_80259868** | **87.9%** | **pending** |

8 of 12 functions are at 100%. This function and `fn_802590C4` (82.3%) and `mnGallery_80258A08` (93.7%) are the remaining blockers for file completion.

---

## Resource Summary

| Attempt | Elapsed | Output Tokens | Cached Tokens | Result |
|---------|---------|--------------|---------------|--------|
| 1 | 129s | 4.3K | 441K | 0% (no code) |
| 2 | 825s* | 24.5K* | 4.5M* | 0% (file-mode, skipped) |
| 3 | 1,379s | 67.8K | 2.6M | **87.95%** |
| 4 | 3,123s | 158.4K | 8.2M | 39.80% (regression) |

*Run 2 totals are for the entire file-mode session, not just this function.

Total dedicated time on this function: ~77 minutes across attempts 1, 3, 4.
Total tokens consumed: ~230K output tokens.
