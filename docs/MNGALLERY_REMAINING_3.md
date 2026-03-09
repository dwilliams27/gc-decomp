# mngallery.c: Remaining 2 Functions — Analysis & Next Steps

**Status as of 2026-03-08**: 10/12 matched, 2 remaining.

| Function | Match | Committed | Blocker |
|----------|-------|-----------|---------|
| ~~mnGallery_80258A08~~ | ~~100%~~ | ~~Yes~~ | ~~SOLVED~~ |
| fn_802590C4 | 90.3% | Yes | Register alloc + opcode in loop |
| mnGallery_80259868 | 89.5% | Yes | Prologue scheduling (lbzu vs addi+lbz) |

---

## SOLVED: mnGallery_80258A08 — 100% Match

### Solution
`#pragma opt_propagation off` + `f32 near_val = zero` variable + declaration order `(top, bottom, left, right)`.

### What Was Tried (16+ approaches before solution)
See git history. Key insight: studying sobjlib.c's 99.84% camera function revealed the `near_val` pattern.

---

## fn_802590C4 (90.3%) — Register Allocation + Opcode Diffs

### The Problem (at 90.3%)
11 differing instructions in a 2-iteration do-while loop:

**Register swaps** (jobj wrong register, cascades):
```
Target: jobj=r31, ud_load=r27
Ours:   jobj=r27, ud_load=r30
```

**Opcode/scheduling diffs** (loop init sequence):
```
Target: addi r26, r27, 0   (copy ud → write_ptr)
        addi r30, r29, 0   (zero = i, register copy)
        add  r27, r27, r0  (advance store ptr IN-PLACE)

Ours:   add  r31, r30, r0  (compute store = ud + offset, NEW register)
        addi r30, r31, 0   (copy back)
        li   r26, 0        (zero = literal 0, NOT register copy)
```

Key: target advances store IN-PLACE in same register as ud, and copies `zero = i` as register move. Our code creates a new register for store and loads zero as literal.

### What Was Tried (EVERYTHING below failed to beat 90.3%)

**Declaration order (exhaustive):**
- All 720 permutations tested
- Best: 90.3% with `(store, i, data, jobj, ud, zero)` — 24 orderings tied
- Previous: 82.3% with `(store, ud, data, i, zero, jobj)`

**Pragmas (tested at both 82.3% and 90.3% baselines):**
- opt_strength_reduction off → 48% | opt_lifetimes off → 82%
- opt_propagation + lifetimes → 90.3% (no change from propagation alone)
- opt_dead_code/loop_invariants/unroll_loops/cse off → no effect
- optimization_level 0/1/2 → 47-56% | peephole off → 63-83%
- schedule off → 82% | No pragmas → 82%
- **NEW (all at 90.3%, no effect):** opt_dead_assignments, opt_strength_reduction_strict, defer_codegen, side_effects, dont_inline, fp_contract, opt_common_subs, pool_data, pool_strings, float_constants, global_optimizer, auto_inline, function_align
- **disable_registers on/off/gpr27** → 81.8% (recognized but harmful)
- **CONCLUSION: ALL pragmas in MWCC binary exhausted. No pragma can fix this.**

**FAKE MATCH tricks (all at 90.3% baseline):**
- `(0, expr)` comma operators → CATASTROPHIC (37-52%)
- `data = data = x` self-assignment → no effect (90.3%)
- `jobj = jobj = x` → 37.5% (catastrophic)
- `!jobj;` / `!data;` no-ops → no effect
- `if (gobj && gobj) {}` / `if (store && store) {}` → no effect
- `new_var = gobj->user_data; ud=new_var; store=new_var` → 90.3% (shifted regs but not enough)
- store for both read+write → 53.7%

**Type/cast/structural:**
- Typed struct* for store/ud → no effect
- Reversed copy direction → 41.5-82.3%
- NULL instead of zero → 40%
- Explicit pointer variables → 46%
- Single pointer, no store/ud split → 57%
- Using `data` for loop → 74%
- volatile zero → 59%

**Loop structure:**
- while/for/do-while → all 90.3%
- Unrolled → 50%
- i != 2 condition → 81%

**External:** No community solutions. No upstream attempts. Same compiler flags everywhere.

---

## mnGallery_80259868 (89.5%) — Prologue Scheduling

### The Problem
32 diffs in first ~31 instructions. Last 67 instructions match 100%.

Core issue: compiler generates `addi r5, r4, mn_804A04F0@l` + `lbz r0, 0(r5)` (2 instructions) where target uses `lbzu r0, mn_804A04F0@l(r3)` (1 instruction that combines load + address update). The extra instruction cascades scheduling through the entire prologue.

### What Was Tried (~80 variants, ALL failed to beat 89.5%)

**Statement order:** All permutations of state assignments with archive before/after. Best 89.5% with `(archive, hovered, prev_cur, cooldown)`.

**Pragmas:** opt_propagation/lifetimes/strength_reduction/scheduling/peephole/global_optimizer/use_lmw_stmw/optimize_for_size/optimization_level/dont_reuse_strings off — none helped, many catastrophic.
- **NEW (all 89.5%, no effect):** opt_dead_assignments, opt_strength_reduction_strict, defer_codegen, side_effects, fp_contract, opt_common_subs, pool_data, pool_strings, float_constants, auto_inline, function_align, opt_lifetimes
- **scheduling off → 39.6% (catastrophic)**
- **CONCLUSION: ALL pragmas in MWCC binary exhausted. No pragma can fix this.**

**FAKE MATCH tricks:** comma operators (87.4%), self-assignment arr (67.1%), !arr no-op (76.4%), volatile state_addr (87.4%).

**Structural:** Different PAD_STACK sizes (0x28-0x40), no PAD_STACK, arr offset variables, global access directly, char** cast, inner struct variations, block scoping tricks.

**Declaration order:** ~6 permutations of key variables. No improvement.

**Reference function:** mnHyaku_8024CD64 (matched, same pattern) gets `lbzu` because it's simpler with different register pressure. That order applied here drops to 87.9%.

### Root Cause
The `lbzu` vs `addi+lbz` decision is MWCC's instruction selector reacting to register pressure at the point of first `mn_804A04F0` access. In simpler functions (mnhyaku), there's pressure to combine operations. In our larger function with more locals and PAD_STACK, the compiler eagerly computes the address.

---

## Remaining Attack Vectors

### 1. Permuter (overnight run)
Best remaining systematic option. With fixed tooling: 8 workers, ~200 iter/min, 8 hours = ~96,000 iterations. The permuter does random equivalent transformations (expression rewriting, statement reordering, variable renaming) that might stumble on patterns we haven't tried.

### 2. Micro-benchmark isolation
Strip each function to JUST the problematic code section, compile in isolation, and systematically test what triggers the desired codegen. Then gradually add back surrounding context to find exactly where it breaks.

### 3. Reverse-engineer MWCC register allocator
MWCC (mwcceppc.exe) is a PE binary we run under wibo. Could load into Ghidra and study the register allocation / instruction scheduling heuristics. Extreme effort but would give definitive answers.

### 4. MWCC undocumented pragmas (SCANNED — key findings)
Binary scan of mwcceppc.exe revealed untested pragmas:
- **`disable_registers`** — Can disable specific register usage! Could force jobj off r27.
- **`opt_dead_assignments`** — Separate from opt_dead_code. Never tested. Could affect `zero = i`.
- **`opt_strength_reduction_strict`** — Stricter variant, never tested.
- **`defer_codegen`** — Defers codegen to end of TU. Could change optimization decisions.
- **`side_effects`** — Side effect tracking.
- **Optimizer pass order**: Copy prop → Const fold → CSE → Code motion → Strength reduction → Copy prop 2 → Loop unroll → Peephole → **Register coloring (graph coloring with coalescing)** → Scheduling.

### 5. Key analysis of 24 tied orderings (fn_802590C4)
- `store`, `ud`, `zero` positions DON'T MATTER — produce identical binary
- Only constraint: `i` before `data` before `jobj`
- **UPDATE (2026-03-08):** Ghidra RE + micro-benchmarks confirmed declaration order `(store, i, data, jobj, ud, zero)` produces jobj=r31 matching target. The 90.3% code already has correct register assignments. Remaining diffs are scheduling/copy-propagation, not register coloring:
  - Target does `addi` (copy) then `add` (advance IN-PLACE); ours does `add` (new reg) then `addi` (copy back)
  - Target treats `zero = i` as register copy (`addi r30, r29, 0`); ours optimizes to `li r26, 0` despite opt_propagation off

### 6. Binary verification
Swap register fields in our compiled .o bytes to verify the code is semantically identical.

### 7. Deferred ideas
- Reverse-engineer MWCC register allocator in Ghidra
- Custom permuter with FAKE MATCH transformations
- Cross-function register pressure manipulation
- Submit PR with 10/12 matched + Equivalent status for remaining 2
