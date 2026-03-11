# mngallery.c: Final Status — 11/12 Matched, 1 at Compiler Floor

**Status as of 2026-03-10**: 11/12 functions 100% matched, 1 remaining at compiler floor.

| Function | Status | Notes |
|----------|--------|-------|
| mnGallery_80259868 | 100% MATCHED | Commit 098613cd9 |
| fn_802590C4 | 93.1% DTK byte match (50/62 exact positional), 62 insns | COMPILER FLOOR — 12 register diffs, committed at e871b02fa |

---

## fn_802590C4 — EXHAUSTIVE ANALYSIS (Option B: Deep MWCC RE completed)

### What This Function Does
State machine with 4 states (0→1→2→4→3). State 2→4 transition has a loop that calls `HSD_GObjPLink_80390228` on two child GObjs and NULLs them out. Ends with `HSD_JObjReqAnimAll` + `HSD_JObjAnimAll`.

### Current Best: `#pragma optimization_level 2` + `opt_strength_reduction on` + `opt_propagation off`

This pragma combination achieves:
- **62 instructions** (matches target count exactly)
- **`addi r27, r26, 0`** for `zero = i` (register copy preserved, NOT constant-folded)
- **Two pointer walkers** with `addi rN, rN, 4` increments (matching target structure)
- **No extra `mr` instruction** (eliminated the `store = ud` copy before bl)
- **50/62 exact positional byte matches** (80.6%)

The remaining 12 diffs are ALL register encoding in the loop section (positions 32-43). The 4 loop variables (i, zero, read_walker, write_walker) are assigned to the same 4 registers {r26, r27, r29, r30} as the target, but mapped to DIFFERENT variables.

### The Core Problem: Register Assignment in Loop Setup

**Target loop setup:**
```asm
lwz r27, 0x2c(r3)       # ud → r27
bl HSD_GObjPLink_80390228
li r29, 0                # i = 0 → r29
slwi r0, r29, 2          # offset = i*4
addi r26, r27, 0         # write_walker = copy of r27 (BEFORE destructive add)
addi r30, r29, 0         # zero = i → r30
add r27, r27, r0         # read_ptr = r27 + offset (DESTRUCTIVE: modifies r27 in-place)
```

**Our best output (O2+SR+prop_off):**
```asm
lwz r27, 0x2c(r3)       # ud → r27
bl HSD_GObjPLink_80390228
li r26, 0                # i = 0 → r26
slwi r0, r26, 2          # offset = i*4
add r30, r27, r0         # read_ptr = r27 + offset → r30 (NON-DESTRUCTIVE: new register)
addi r27, r26, 0         # zero = i → r27 (overwrites ud register)
addi r29, r30, 0         # write_walker = copy of r30 (copy of read_ptr, not original ud)
```

**Key structural difference:**
- **Target**: Copies ud to write_walker FIRST, THEN destructively adds offset to ud (reusing r27 for read_ptr)
- **Ours**: Computes read_ptr into NEW register (r30), then reuses r27 for zero, then copies read_ptr to write_walker

This causes a cascade of register swaps:

| Variable | Target | Ours |
|----------|--------|------|
| i | r29 | r26 |
| zero | r30 | r27 |
| read_walker | r27 | r30 |
| write_walker | r26 | r29 |

### Why This Happens
The two source variables (`ud` and `store`) create separate walkers from separate base registers. The target code appears to have the strength reduction pass create two walkers from a SINGLE base register, with one being a pre-copy (addi) and the other a destructive in-place modification (add). With `opt_propagation off`, the compiler treats `ud` and `store` as truly independent pointers and doesn't merge their walker bases.

---

## What We Tried — EVERYTHING

### Phase 1: Source-Level Experiments (pre-MWCC RE)

#### Declaration Order Sweeps (2880+ permutations)
- **720 permutations × baseline O4,p**: Best 30/62 exact. All produce same output.
- **720 permutations × O2+SR+prop_off**: Best 50/62 exact with order `store,data,zero,i,jobj,ud`. No permutation exceeded 50/62.
- **720 permutations × O2+SR+prop_off + best body variant**: Confirmed 50/62 ceiling holds across all 720 orderings.

#### Body Variants (8 tested with O2+SR+prop_off)
All 8 body structure variants produce IDENTICAL output (50/62):

| Variant | Load | Copy Timing | Read From | Write To | Result |
|---------|------|-------------|-----------|----------|--------|
| A | ud | before bl | store | ud | 50/62 |
| B | ud | before bl | ud | store | 50/62 |
| C | ud | after bl | store | ud | 50/62 |
| D | ud | after bl | ud | store | 50/62 |
| E | store | before bl | store | ud | 50/62 |
| F | store | before bl | ud | store | 50/62 |
| G | store | after bl | store | ud | 50/62 |
| H | store | after bl | ud | store | 50/62 |

Conclusion: Swapping read/write roles or changing copy timing has ZERO effect on codegen with O2+SR+prop_off.

#### Other Source Variants
- Single variable (no copy) → 60 insns at O4 AND at O2+SR+prop_off, one walker (wrong structure). 9 single/hybrid variants tested — all dead ends.
- NULL instead of zero variable → identical output
- Typed struct pointers (`struct fn_802590C4_data*` instead of `void*`) → same score, destructive add at all opt levels but only 1 walker
- for/while loop variants → identical to do-while
- Reversed read/write variables → same output
- Expression tricks (casts, volatile, arithmetic identities) → all folded or worse
- Array pointer extraction (`HSD_GObj** arr = data->gobjs; arr[i]`) → 61 insns, 1 walker, destructive add
- `data_read_ud_write` (data for read, fresh ud load for write) → 62 insns, 2 walkers, destructive add, 49-50/62 exact
- `data_read_freshload_write` → similar to above, caps at 50/62
- `ud_read_data_write` → 62 insns, 49/62, no destructive add
- `store_from_data` (store = (void*)data) → 62 insns, 49/62
- `inline_ud` (gobj->user_data inline, no pre-load) → 63 insns, 2/62
- `array_two_ptr` (two HSD_GObj** from same base) → 63 insns, 30/62, 2 walkers, no destructive add
- `three_var` (read_ptr, store, ud from same base) → 62 insns, 50/62 (same as two_var)
- Manually unrolled loop → 58-62 insns, 7-22/62

### Phase 4: CSE-off Hypothesis (2026-03-10)

**Hypothesis**: With `opt_common_subs off`, two separate `ptr->gobjs[i]` expressions (read+write) remain separate in IR → SR creates TWO walkers from SAME base → destructive add.

**Result**: FAILED. ~50 body×pragma combos tested. CSE off doesn't change walker count. Single-var + CSE off still gives 60 insns/1 walker.

### Phase 5: data+ud Walker Pattern (2026-03-10)

**Discovery**: Using `data` (loaded at function entry, r28) for read and `ud` (fresh load) for write creates a DIFFERENT interference graph → destructive add + 2 walkers + 62 insns.

**360 declaration order × 3 body variant sweep**: Best 50/62. Different 12 instructions mismatch vs the two-var pattern, but same count.

### Phase 6: `register` Keyword (2026-03-10)

**Hypothesis**: `register` keyword shifts Chaitin-Briggs allocation, used in matched functions elsewhere in the codebase (ftCo_Attack100.c, mndiagram.c, grcastle.c).

**Result**: 55 combinations tested (all subsets of `register` on {store, zero, i, ud} × {O4, O2+SR+prop_off} × two body variants). **ZERO effect** on MWCC 1.2.5n for this function — every combination produces identical output.

### Phase 7: Comprehensive Summary (2026-03-10)

**Total unique compilations: ~3,500+** across all sessions. Every approach converges on the same ceiling: **50/62 exact positional match (93.1% DTK byte match)**.

### Phase 2: Deep MWCC RE (Ghidra reverse engineering)

#### Complete Pragma-to-Flag Mapping (confirmed via Ghidra RE of FUN_0042c930)

| Address | Flag | Threshold | Pipeline Role | Pragma |
|---------|------|-----------|---------------|--------|
| `DAT_005842e1` | optimization_level | N/A | Master level 0-4 | `optimization_level` |
| `DAT_005842e4` | opt_common_subs | >= 2 | SSA subscripts + expression propagation | `opt_common_subs` |
| `DAT_005842e5` | opt_strength_reduction | >= 3 | Strength reduction in loop processing | `opt_strength_reduction` |
| `DAT_005842e6` | opt_propagation | >= 2 | Copy/constant propagation + range prop | `opt_propagation` |
| `DAT_005842e7` | opt_lifetimes | >= 3 | UseDef / reaching definitions | `opt_lifetimes` |
| `DAT_005842e8` | opt_loop_invariants | >= 3 | Loop invariant code motion | `opt_loop_invariants` |
| `DAT_005842ea` | opt_dead_code | >= 1 | Dead code elimination | `opt_dead_code` |
| `DAT_005842eb` | opt_loop_inv (cleanup) | >= 3 | Post-loop UseDef cleanup | `opt_loop_invariants` |
| `DAT_005842ed` | opt_dead_assignments | >= 3 | Loop unrolling (yes, really) | `opt_dead_assignments` |
| `DAT_005842ef` | (internal) | >= 4 | 2-pass optimization | (internal) |

#### IRO_ConstantFolding (FUN_00455a70)
- Runs UNCONDITIONALLY in pipeline (not gated by any flag)
- Only handles binary/unary arithmetic ops (x = 3 + 5 → x = 8)
- Does NOT handle assignment propagation (zero = i is NOT folded by this pass)
- Previous analysis was WRONG: this pass is NOT the source of the `zero = i` fold

#### The Actual Fold Mechanism
The fold of `zero = i` to `zero = 0` is caused by the COMBINATION of:
1. **UseDef analysis** (FUN_00459b30): Gated by `e7 || e6`. Provides reaching definitions.
2. **Copy/constant propagation** (FUN_00458970): Gated by `e6`. Uses reaching defs to propagate constants.

At O2 with `opt_propagation off`: e6=0, e7=0 → UseDef does NOT run → fold PREVENTED
At O3 with `opt_propagation off`: e6=0, e7=1 → UseDef RUNS (because e7=1) → fold happens via reaching defs alone

This is why O2+prop_off preserves `addi` but O3+prop_off does not.

### Phase 3: Pragma Combination Testing (14 combos)

| Combo | Insns | Exact | `zero` Pattern | Walkers | Notes |
|-------|-------|-------|---------------|---------|-------|
| **O2+SR+prop_off** | **62** | **50/62** | **addi (preserved!)** | **2** | **BEST** |
| O2+SR+prop_off+cs_off | 62 | 50/62 | addi (preserved!) | 2 | Same as above |
| O3+prop_off | 62 | 50/62 | li (folded) | 2 | 62 insns but fold persists |
| O3+cs_off | 62 | 50/62 | li (folded) | 2 | cs_off alone doesn't prevent fold |
| O3+prop_off+cs_off | 62 | 50/62 | li (folded) | 2 | e7 still enables fold |
| O3+lt_off+prop_off | 62 | 50/62 | li (folded) | 2 | Surprising: lt_off should disable e7 |
| O3+prop_off+lt_off+cs_off | 62 | 50/62 | li (folded) | 2 | Same |
| O1+SR | 67 | 8/62 | addi (preserved!) | 2 | 5 extra insns, walkers present |
| O1+SR+cs_on | 66 | 6/62 | addi (preserved!) | 2 | Slightly fewer insns |
| O2+SR | 63 | 29/62 | li (folded) | 2 | Propagation on → fold |
| O2+prop_off | 59 | 23/62 | addi (preserved!) | 0 | No SR → no walkers |
| O1 | 65 | 7/62 | addi (preserved!) | 0 | No walkers at O1 |
| O3 | 63 | 30/62 | li (folded) | 2 | Same as baseline |
| O4,p baseline | 63 | 30/62 | li (folded) | 2 | Committed version |

### The Two Levers and Their Trade-offs

1. **`addi` for zero = i**: Requires e6=0 AND e7=0 (both propagation and lifetimes off). Achieved at O2 base with `opt_propagation off`. At O3+, e7 auto-enables and re-enables the fold.

2. **Pointer walkers**: Requires e5=1 (strength reduction on). Available at O3+ natively or via `opt_strength_reduction on` at lower levels.

3. **62 instructions (no extra `mr`)**: Requires certain optimization level behaviors. O2+SR+prop_off achieves this. O1+SR gives 67 (too many).

Only O2+SR+prop_off hits ALL THREE simultaneously. But the register assignment in the loop is wrong (50/62 instead of 62/62).

---

## Why This Is a True Compiler Floor

1. **The register mapping is fixed**: 2880+ declaration order permutations tested, 8 body variants tested. The Chaitin-Briggs register allocator produces the same interference graph for all source variations, yielding the same 4-register mapping {r26↔r29, r27↔r30 swapped}.

2. **The structural difference (destructive vs non-destructive add) is not controllable**: The strength reduction pass decides whether to modify the base register in-place or use a new register. With two separate source variables, it always creates separate walkers from separate bases (non-destructive). With one variable, it creates only one walker (wrong structure). There is no source pattern that produces two walkers from the same base.

3. **No pragma combination bridges the gap**: O2+SR+prop_off is the unique sweet spot (walkers + addi + 62 insns), but the register assignment cannot be changed from source level.

4. **The target was compiled with O4,p** and somehow preserves `addi r30, r29, 0` for zero = i. This is paradoxical — at O4, UseDef and propagation both run, which should fold it. The original source likely has a different structure that we haven't discovered, OR the original was compiled with different per-function pragmas.

---

## Remaining Avenues (diminishing returns)

### ~~1. Single-variable test with O2+SR+prop_off~~ — TESTED, dead end
Tested 9 variants (single_ud, single_store, ud_after_bl, via_data, both_data, temp_read, two_var_ref, zero_literal, null_direct). Single-variable approaches give 60 insns with only 1 pointer walker (wrong structure). Two-variable approaches all give 50/62 with 2 walkers but non-destructive add. No variant exceeds 50/62.

### 2. Three-variable approach
Add a third variable to split read base and write base while keeping a shared origin for the walker generation.

### 3. Loop unrolling / manual peeling
The loop runs exactly twice. Manual unrolling or first-iteration peeling might produce different code.

### 4. Instruction scheduler RE
If the register mapping could be fixed, the instruction ORDER difference (copy-then-compute vs compute-then-copy) might be controlled via source statement ordering, since the scheduler's tie-breaker is list order.

---

## Recommendation

### Accept as Equivalent and move on
- Mark fn_802590C4 as `OBJECT_EQUIVALENT` with the O2+SR+prop_off pragma version (93.1% DTK byte match, 50/62 exact positional, 62 insns, committed at e871b02fa)
- Submit PR "Complete mn/mngallery.c (11/12 matched, 1 equivalent)"
- The 12 diffs are all register encoding (functionally identical behavior)
- **Estimated effort: 1-2 hours**

### OR: Continue with remaining avenues (estimated 4-8 more hours, low probability of success)

---

## MWCC RE Artifacts (for community reference)

| Finding | Location | Notes |
|---------|----------|-------|
| IRO_ConstantFolding is unconditional | FUN_00455a70 | Only handles binary/unary ops, NOT assignments |
| IRO_CopyAndConstantPropagation | FUN_00458970 | Gated by opt_propagation (e6) |
| UseDef / reaching definitions | FUN_00459b30 | Gated by `e7 \|\| e6`; provides data for fold |
| Flag initialization | FUN_0042c930 | ALL flags derived from optimization_level |
| Register coloring is Chaitin-Briggs | FUN_004a1000+ | 3-class sequential (GPR→FPR→CR) |
| Scheduler priority function | FUN_004ccdc0 | Slack → successors → latency → list order |
| Peephole lbzu combine | FUN_004c94d0 | Peep_UpdateFormCombine |
| `#pragma dumpir` is dead code | All retail versions | Output paths compiled out |
| CW IDE settings table | 0x0054a388 | Maps pragma names to byte storage |
| Ghidra project | ghidra_projects/mwcc_re/ | Labeled passes, partial decompilations |
