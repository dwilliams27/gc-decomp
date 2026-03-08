# mngallery.c: Remaining 3 Functions — Analysis & Next Steps

**Status as of 2026-03-08**: 9/12 matched, 3 remaining.

| Function | Match | Committed | Blocker |
|----------|-------|-----------|---------|
| mnGallery_80258A08 | 96.0% | Yes | MWCC f0 scratch register |
| fn_802590C4 | 82.3% | Yes | Register allocation swap in loop |
| mnGallery_80259868 | 87.9% | Yes | Prologue instruction scheduling |

---

## mnGallery_80258A08 (96.0%) — f0 Scratch Register

### The Problem
MWCC uses `f0` as a scratch register when a float variable is immediately copied:
```
// Target (what we want):
lfs f31, zero@sda21(r0)     // load directly into f31
fmr f29, f31                // copy to f29
fmr f27, f31                // copy to f27

// Our code (what we get):
lfs f0, zero@sda21(r0)      // load into f0 scratch!
fmr f31, f0                 // copy to f31
fmr f29, f0                 // copy to f29
fmr f27, f0                 // copy to f27
```

The `f0` pattern is triggered by `top = zero; left = zero;` — the immediate copy assignments make MWCC route through f0 instead of loading directly into the callee-saved register.

### What Was Tried
1. **Declaration order search (240 permutations)**: Found `(zero, far, left, top, bottom, right)` at 96.0% — best possible without fixing f0. All other orders <= 96%.
2. **`register` keyword on zero**: Tried `register f32 zero = mnGallery_804DC360;` — doesn't fix f0 but doesn't hurt.
3. **Late copy pattern**: Move `top = zero; left = zero;` after function calls. Works in micro-benchmarks (avoids f0) but drops real function to 36% because Vec3 copies + struct assignments interact differently.
4. **Inline zero in SetOrtho args**: Replace `top` and `left` with `zero` directly in `HSD_CObjSetOrtho(cobj, zero, bottom, zero, right)`. Still 36%.
5. **No-copy pattern**: Use 4 separate float variables without any copies. Micro-benchmark shows `lfs f31` directly. But in the real function, the 6+ float variables cause MWCC to spill differently.
6. **Headless agent (61 turns)**: Couldn't improve past 93.7%.
7. **Permuter**: Ran ~1800 iterations, no improvement found.

### Key Micro-Benchmark Findings
```c
// This triggers f0 scratch (BAD):
f32 zero = load_float();
f32 top = zero;       // immediate copy → f0
f32 left = zero;      // immediate copy → f0

// This avoids f0 (GOOD — in isolation):
f32 zero = load_float();
// ... function calls ...
f32 top = zero;       // late copy → lfs f31 directly

// This also avoids f0 (GOOD — in isolation):
f32 zero = load_float();
// Don't copy at all, use zero directly
```

### Next Steps for 80258A08
- **Try removing top/left entirely**: Pass `zero` directly to SetOrtho for top and left args. Need to check if MWCC still routes through f0 when `zero` is used in more places.
- **Try volatile or other qualifiers** on zero to force register allocation.
- **Try splitting the function**: Move the float setup into a separate inline or static function.
- **Permuter with longer run** (8+ hours, 50K+ iterations) — the f0 pattern might have a rare permutation that avoids it.
- **Study other matched mn/ functions** for similar camera setup patterns (mnGallery_80258BC4 uses HSD_CObj too).
- **Check decomp.me** for community solutions to similar f0 scratch patterns.

---

## fn_802590C4 (82.3%) — Register Allocation Swap

### The Problem
Register assignments in a 2-iteration loop are swapped:
```
Target:  store=r27, i=r29, ud=r26, zero=r30
Ours:    store=r29, i=r27, zero=r26, ptr=r30
```

Additionally, the target generates TWO `addi rX, rY, 0` copy instructions before the `add` (pointer computation), while our code generates `add` first, then one `addi` copy, then `li 0`:
```
Target: addi r26, r27, 0  (ud = store copy)
        addi r30, r29, 0  (zero = i copy)
        add  r27, r27, r0 (store += offset)

Ours:   add  r30, r29, r0 (ptr = store + offset)
        addi r29, r30, 0  (copy)
        li   r26, 0       (literal zero)
```

### What Was Tried
1. **All 24 declaration order permutations** of (store, ud, i, zero): Best was 83.9% but regressed `data` from r28 to r26, breaking the rest of the function.
2. **Copy direction swap**: `store = user_data; ud = store` vs `ud = user_data; store = ud` — no effect.
3. **Variable types**: void*, typed struct*, HSD_GObj* for zero — no effect.
4. **Loop forms**: do-while, while, for — all 82.3%.
5. **Unrolled loop**: Drops to 75.4%.
6. **Using `data` for writes**: Still 82.3%.
7. **Two separate loads from user_data**: Still 82.3%.
8. **i++ before store**: 82.7% (tiny improvement, not significant).
9. **NULL instead of zero variable**: 82.3%.

### Root Cause Analysis
The fundamental issue is that MWCC's strength reduction of `store->gobjs[i]` produces different initialization code depending on internal heuristics we can't directly control. The compiler decides whether to emit `addi` copies before or after the `add` computation based on its own scheduling. No C-level change we tried affected this ordering.

### Next Steps for 802590C4
- **Permuter**: This is the best candidate for random search. The register swap might have a specific variable/statement ordering that triggers the right allocation. Run permuter for 8+ hours with 8 workers.
- **Pragma experiments**: `#pragma opt_strength_reduction off` might prevent the loop optimization and produce different code.
- **Manual loop unrolling with pointer arithmetic**: Instead of `gobjs[i]`, use explicit pointer advancement.
- **Different struct layout**: Change the padding or member order in the local struct definition.
- **Study the `#pragma opt_propagation off` that's already on this function** — it was put there for a reason. Maybe other pragmas interact.
- **Two separate loops**: One for GObjPLink calls, one for zeroing. This changes the loop structure fundamentally.

---

## mnGallery_80259868 (87.9%) — Prologue Scheduling

### The Problem
The compiler delays computing `arr = mnGallery_804A0BA0` and generates an extra `mr` instruction:
```
Target: lis r4, arr@ha
        ...  (2 instructions)
        addi r4, r4, arr@l    // directly into r4

Ours:   lis r4, arr@ha
        ...  (10+ instructions)
        addi r0, r4, arr@l    // into r0 (temp!)
        mr r4, r0             // extra copy back to r4
```

The gap between `lis` and `addi` is too large — the compiler loses the register pairing and uses r0 as a temporary. Additionally, several `addi` offset computations (r7, r6, r11, r8 = arr offsets for lbArchive_LoadSections arguments) are scheduled differently.

### What Was Tried
1. **Statement order search (24 variants)**: Tried all permutations of (cooldown, prev/cur_menu, hovered_selection) with archive before/after, and arr declared first/second. Best: 89.5% with `arr first, state_order=(hovered, prev_cur, cooldown), archive before`. Most variants at 87.9%.
2. **Moving archive load before state assignments**: Drops to 72.3%.
3. **Moving all state assignments after archive**: 79.4%.
4. **Agent's pragma stack** (`opt_lifetimes off`, `opt_strength_reduction off`, etc.): Still 87.9%.
5. **Agent's `&arr[N]` syntax** instead of `arr + N`: Still 87.9%.

### Root Cause Analysis
MWCC's instruction scheduler in the prologue interleaves register saves, address computations, and state assignments. The target's scheduling places `lis r4 + addi r4` with only 2 instructions between them (before the stack frame), while our code delays the `addi` by 10+ instructions. The C statement order influences initial scheduling but the scheduler's heuristics make the final decision.

### Next Steps for 80259868
- **Apply the 89.5% variant** (`arr first, hovered before cooldown, archive first`) and commit.
- **Try computing arr offsets as separate variables** before the state assignments — force the compiler to need arr early.
- **Try using mnGallery_804A0BA0 directly in the call** (no local variable) — might change how the compiler handles the address.
- **Permuter**: Can try random statement reorderings and expression variants.
- **Split the prologue**: Move state assignments into a separate inline function.
- **Check if `inner` struct definition affects prologue scheduling** — the large PAD_STACK(0x38) and struct might influence register allocation.
- **Try different PAD_STACK sizes** — stack frame size affects prologue instruction ordering.

---

## General Strategy Notes

### What Works
- **Exhaustive declaration order search**: Found 96% for 80258A08 (from 93.7%) and 89.5% for 80259868 (from 87.9%). Always try this first.
- **Understanding the exact diff**: Every diff tells a story about what the compiler did differently. Read each instruction.

### What Doesn't Work
- **Headless agent warm-start on near-matched functions**: Agents tend to regress rather than make tiny targeted changes. The 80259868 agent went from 87.9% to 39.8% before recovering.
- **Brute-force code rewriting**: At 80%+ match, the structure is right — only micro-level changes matter (declaration order, statement order, type choices).
- **Assuming micro-benchmark results transfer to real functions**: The f0 scratch pattern fix works in isolation but fails in the real function due to interaction with other variables and function calls.

### Permuter Is Our Best Shot
For all 3 remaining functions, the issues are register allocation / instruction scheduling that C-level changes barely influence. The permuter does random equivalent transformations that might stumble on the right pattern. Need:
- Long runs (8+ hours, ~50K+ iterations with 8 workers)
- Fix the permuter's output reading bug first (see plan file for details)
- Run one function at a time to avoid file collisions

### Headless Agent Guidelines (If Used Again)
- **NEVER let agents edit header files or add pragmas** — scope their prompt to ONLY use write_function
- **Kill agents before manual work** to avoid file collisions
- **Commit working code first** so git checkout can always restore it
