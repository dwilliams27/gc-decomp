# mngallery.c: Remaining 2 Functions — Analysis & Next Steps

**Status as of 2026-03-08**: 10/12 matched, 2 remaining.

| Function | Byte Match | Insn Match | Committed | Blocker |
|----------|-----------|------------|-----------|---------|
| fn_802590C4 | 92.1% | 82.3% (51/62) | Yes | Register coloring + constant folding in loop |
| mnGallery_80259868 | ~89.5% | — | Yes | Instruction selection (lbzu vs addi+lbz) |

---

## fn_802590C4 (92.1% byte match) — COMPILER FLOOR CONFIRMED

### Current Best Code
- **Declaration order:** `store, data, zero, i, jobj, ud` → gives jobj=r31, ud=r27 (matches target)
- **No pragmas** (all tested, none help)
- **Loop structure:** do-while (for/while produce identical output)
- **Source file:** `melee/src/melee/mn/mngallery.c` lines 342-396

### The 11 Remaining Diffs (all in loop setup, lines 33-44)

**3 instruction-level differences:**
```
Line 35: target=[addi r26, r27, 0x0] compiled=[add r30, r27, r0]    ← copy ud vs compute ud+offset
Line 36: target=[addi r30, r29, 0x0] compiled=[addi r29, r30, 0x0]  ← copy i→zero vs copy read→write
Line 37: target=[add r27, r27, r0]   compiled=[li r27, 0x0]          ← ud+offset vs literal zero
```

**8 register-naming differences** (consequences of above):
- Target: i=r29, zero=r30, read_ptr=r27, write_ptr=r26
- Compiled: i=r26, zero=r27, read_ptr=r30, write_ptr=r29
- data=r28 and jobj=r31 match perfectly

**Root cause:**
1. **Constant folding:** Compiler sees `i=0; zero=i` and folds to `li zero, 0` instead of keeping register copy `addi zero, i, 0`. This happens BEFORE register coloring and persists even with `#pragma opt_propagation off`.
2. **Scheduling:** Compiler computes read_ptr (add ud+offset) before write_ptr (copy ud). Target does the reverse: copy ud first, then advance in-place.
3. **Register coloring consequence:** Different scheduling creates different interference graph, leading to different register assignments for loop variables.

### Exhaustive Testing Summary (2026-03-08)

#### A. Declaration Order (720 permutations — ALL tested)
- Best: `store,data,zero,i,jobj,ud` → 11 diffs, jobj=r31, ud=r27
- Original: `store,i,data,jobj,ud,zero` → 12 diffs, jobj=r27 (wrong register)
- All 720 tested via automated sweep script. Multiple tied orderings.

#### B-H. Structural Variants (25+ variants — ALL tested with best decl order)
| Variant | Diffs | Notes |
|---------|-------|-------|
| BASELINE (best decl order) | 11 | Reference |
| E2_STORE_TYPED (struct* store) | 11 | Same pattern |
| V2_FOR_LOOP | 11 | Identical to do-while |
| V3_WHILE_LOOP | 11 | Identical to do-while |
| V4_VOID_ZERO (cast) | 11 | Cast doesn't affect codegen |
| V7_TEMP_IN_LOOP | 11 | Local temp optimized away |
| V10_DATA_FOR_WRITE | 11 | data ptr for write side |
| V11_ZERO_LITERAL (zero=0) | 11 | Same as zero=i |
| V12_PTR_WRITE | 11 | Raw pointer arithmetic |
| C_INDEP_LOADS | 12 | Separate gobj->user_data loads |
| E_REVERSED (store=ud swap) | 12 | Changes ud register |
| V14_UD_TYPED | 12 | Typed ud pointer |
| F_ZERO_BEFORE_CALL | 13 | Worse scheduling |
| F2_IZERO_BEFORE_CALL | 13 | Worse scheduling |
| COMBO_REVERSED_ZERO_BEFORE | 13 | Worse scheduling |
| COMBO_INDEP_ZERO_BEFORE | 13 | Worse scheduling |
| V9_ZERO_EARLY | 13 | Worse scheduling |
| B_NULL | 21 | Breaks register pressure |
| B_ZERO_LITERAL | 21 | Breaks register pressure |
| V8_VOID_I_CAST | 21 | Cast chain breaks allocation |
| V6_STORE_FIRST | 30 | Wrong semantics |
| V1_SINGLE_PTR | 39 | Wrong instruction count |
| H_DATA_PTR_LOOP | 40 | Wrong instruction count |
| V13_UNROLLED | 57 | Completely different codegen |

**Conclusion: ALL variants with correct semantics produce exactly 11 diffs with identical diff pattern.**

#### Pragmas (14 tested — ALL at 11 diffs with best decl order)
| Pragma | Diffs | Notes |
|--------|-------|-------|
| opt_propagation off | 11 | Does NOT prevent zero=i constant fold |
| optimization_level 3 | 11 | No effect on loop |
| opt_peephole off | 11 | No effect |
| opt_common_subexpressions off | 11 | No effect |
| opt_lifetimes off | 11 | No effect |
| opt_dead_assignments off | 11 | No effect |
| opt_propagation + opt_cse off | 11 | No effect |
| opt_loop_invariants off | 11 | No effect |
| opt_propagation + opt_peephole off | 11 | No effect |
| opt_dead_code off | 11 | No effect |
| opt_propagation + opt_lifetimes off | 11 | No effect |
| opt_unroll_loops off | 11 | No effect |
| scheduling off | 19 | Worse (expected) |
| opt_strength_reduction off | 40 | Catastrophic |

**Additional pragmas tested at old baseline (from prior sessions):**
opt_dead_assignments, opt_strength_reduction_strict, defer_codegen, side_effects, dont_inline, fp_contract, opt_common_subs, pool_data, pool_strings, float_constants, global_optimizer, auto_inline, function_align, disable_registers, use_lmw_stmw, optimize_for_size

**CONCLUSION: ALL known MWCC pragmas exhausted. None affect the 3 core instruction differences.**

---

## mnGallery_80259868 (~89.5%) — Instruction Selection

### The Problem
Compiler generates `addi r5, r4, mn_804A04F0@l` + `lbz r0, 0(r5)` (2 instructions) where target uses `lbzu r0, mn_804A04F0@l(r3)` (1 instruction). Cascades through entire prologue scheduling.

### What Was Tried (~80 variants, ALL failed)
See previous sessions. Statement ordering, pragmas, fake match tricks, structural variants, PAD_STACK sizes, declaration order permutations. Reference function mnHyaku_8024CD64 gets lbzu in simpler context.

### Root Cause
lbzu decision is MWCC instruction selector reacting to register pressure. More locals = compiler eagerly computes address in 2 insns instead of combining.

---

## Next Steps (Prioritized)

### Option A: Accept 92.1% and Submit PR
- Mark fn_802590C4 and mnGallery_80259868 as `Equivalent` status
- Submit PR "Complete mn/mngallery.c (10/12 matched, 2 equivalent)"
- Community standard accepts Equivalent for compiler-limit functions
- **Effort: 1-2 hours. Guaranteed outcome.**

### Option B: Reverse-Engineer MWCC Internals (Phase 0-3 from plan)
- Phase 0: Test `#pragma dumpir on` (hidden pragma, 3-4 hours)
- Phase 1: Ghidra structural mapping of mwcceppc.exe (6-8 hours)
- Phase 2: Reverse-engineer register coloring algorithm (10-12 hours)
- Phase 3: Reverse-engineer instruction selector for lbzu (8-10 hours)
- **Effort: 30-40 hours. Uncertain outcome. Community-wide value.**

### Option C: Permuter Overnight Run
- 8 workers × 8 hours = ~96K iterations of random equivalent transforms
- Unlikely to beat compiler floor but worth running overnight
- **Effort: 0 active hours (overnight). Low probability of improvement.**

### Option D: Move to Other mn/ Targets
- mnstagesel.c: 1 function at 99.2%
- mnmain.c: 6 functions at ~98%
- mnsound.c: 3 functions at ~93%
- **Effort: 4-8 hours per file. High probability of new matches.**
