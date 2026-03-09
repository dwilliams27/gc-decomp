# Reverse-Engineering MWCC Register Allocator & Instruction Selector

## Context

Two functions in mngallery.c are stuck at 89-90% match after exhausting ALL source-level approaches:
- **fn_802590C4 (90.3%)**: Register coloring puts `jobj` in r27 (target: r31). 3 opcode diffs (`addi` vs `add` vs `li`).
- **mnGallery_80259868 (89.5%)**: Compiler emits `addi+lbz` (2 insns) where target uses `lbzu` (1 insn).

We tested every MWCC pragma (20+), all 720 declaration orderings, 80+ structural variants, and dozens of FAKE MATCH tricks. Nothing moves the needle. The remaining diffs are compiler-internal decisions in the register allocator (Coloring.c) and instruction selector (InstrSelection.c).

**Goal:** Reverse-engineer enough of MWCC's internals to either (a) find the source-level lever that produces the target codegen, or (b) definitively prove no such lever exists.

**Budget:** ~40 hours across phases. Each phase produces standalone value.

**Broader impact:** Understanding MWCC internals helps ALL GameCube/Wii decomp projects (25+ active), not just these 2 functions. No one has published the register coloring algorithm — we'd be first.

---

## Phase 0: Test `#pragma dumpir on` (3-4 hours)

**CRITICAL SHORTCUT.** The binary contains strings `"Dumping function %s after %s"` and `"Dumps for pass=%d"`, suggesting a hidden `dumpir` pragma that dumps IR between optimization passes. If this works, it bypasses most Ghidra RE.

### Tasks
1. Test `#pragma dumpir on` on a trivial function with Melee flags (`-O4,p -proc gekko`)
2. If output appears, test on a micro-benchmark of the `mn_804A04F0` access pattern
3. Test on fn_802590C4's loop to see register coloring state
4. Document the dump format and which passes are visible

### Success criteria
- Dump shows IR before/after register coloring with physical register assignments
- Dump shows instruction selection decisions (lbzu vs lbz patterns)

### If `dumpir` works
Skip to Phase 3 micro-benchmarks, informed by dump data. This compresses the entire plan to ~15 hours.

### If `dumpir` doesn't work (broken/stripped)
Try it on newer versions (GC/2.7, GC/3.0a3). Then proceed to Phase 1.

---

## Phase 1: Ghidra Project Setup & Structural Mapping (6-8 hours)

### Setup
- **Binary:** `melee/build/compilers/GC/1.2.5n/mwcceppc.exe`
- **Ghidra:** `~/Library/ghidra/ghidra_12.0.2_PUBLIC`
- 1.6MB PE32 (x86), contains 89 source file name references, rich debug strings
- Check retrowin32 Issue #20 for pre-configured .gzf project with data types

### Tasks
1. Import into Ghidra PE/COFF loader
2. Find all xrefs to `"Coloring.c"` — these are `__FILE__` assertions pinpointing coloring functions
3. Find all xrefs to pass marker strings (`"AFTER REGISTER COLORING"`, `"AFTER INSTRUCTION SCHEDULING"`, etc.)
4. Identify the main compiler pipeline function (calls pass markers in sequence)
5. Label pass entry points: the function called before each AFTER marker
6. Map data structures via `"fCoalesced"`, `"fCoalescedInto"`, `"fSpilled"` xrefs
7. Find register name table via `"gpr0"` through `"gpr31"` xrefs

### Known pass order (from binary strings)
```
CSE → Copy Prop → Add Prop → Value Numbering → Code Motion → Strength Reduction
→ Loop Transforms → Constant Prop → Load Deletion → Array→Register → Constant Prop 2
→ Value Numbering 2/3 → Code Motion 2 → Load Deletion 2
→ Instruction Scheduling 1 → Peephole Forward → REGISTER COLORING
→ Epilogue/Prologue → Peephole Optimization → Final Instruction Scheduling
```

Key insight: Register coloring happens BETWEEN two scheduling passes. The `lbzu` pattern is almost certainly decided during InstrSelection or peephole, NOT during coloring. The two stuck functions involve TWO DIFFERENT subsystems.

### Deliverable
- Annotated Ghidra project with labeled functions for all compiler passes
- "MWCC Internal Architecture Map" document (addresses, call graph, data structures)

---

## Phase 2: Register Coloring Algorithm (10-12 hours)

Focus on Coloring.c. Answer four key questions:

### Q1: Coloring order
What order are variables assigned to physical registers? Chaitin simplifies low-degree nodes first, then assigns in reverse. MWCC may use lifetime as primary criterion (per our empirical finding from 720-permutation search).

### Q2: Coalescing heuristic
When does MWCC merge copy-connected variables? This directly affects fn_802590C4: target coalesces `zero = i` into a register copy (`addi r30, r29, 0`), our code constant-propagates to `li r26, 0`.

### Q3: Spill metric
What decides which variable spills? Palanciuc & Badea 2004 paper describes 3 heuristic functions for Metrowerks StarCore — check if PPC version uses similar.

### Q4: Source mapping
How does declaration order map to interference graph construction order?

### Micro-benchmarks
```c
// A: Declaration order vs register assignment
void test1(void) { int a, b; a = f(); b = g(); use(a, b); }
void test2(void) { int b, a; b = g(); a = f(); use(a, b); }

// B: Lifetime length vs register priority
void test3(void) { int long_lived = get(); int short = get(); use1(short); use2(long_lived); }

// C: Coalescing trigger (THE critical test for fn_802590C4)
void test4(void) { int x = get(); int y = x; use(y); }      // should coalesce
void test5(void) { int i = 0; int zero = i; use(zero); i++; } // does it coalesce?

// D: fn_802590C4 reduced case
void test6(void* gobj) {
    int* ud = ((int**)gobj)[0xB];
    int* jobj = ((int**)gobj)[0xA];
    destroy(gobj);
    int i = 0; int zero = i;
    do { destroy(((int**)ud)[7+i]); ((int**)ud)[7+i] = (int*)zero; i++; } while (i < 2);
    use(jobj);
}
```

### Deliverable
- Pseudocode of the coloring algorithm with all heuristic details
- Answers to Q1-Q4 validated against micro-benchmarks

---

## Phase 3: Instruction Selection / lbzu Analysis (8-10 hours)

Can run IN PARALLEL with Phase 2.

### Tasks
1. Find all xrefs to `"LBZU"` / `"lbzu"` in the binary
2. Reverse-engineer the pattern matching in InstrSelection.c
3. Reverse-engineer both peephole passes (one runs before coloring, one after)
4. Determine the decision boundary for `lbzu` vs `addi+lbz`

### Micro-benchmarks
```c
// E: lbzu trigger vs register pressure
extern struct { u8 cur; u8 prev; u16 hovered; } state;
void simple(void) { state.prev = state.cur; state.cur = 5; }  // should get lbzu
void pressure(int a, int b, int c, int d, int e) {
    state.prev = state.cur; state.cur = 5; use(a,b,c,d,e);    // may not get lbzu
}

// G: Incremental reproduction — start from mnHyaku pattern (gets lbzu),
//    progressively add mnGallery_80259868's complexity until lbzu breaks:
//    1. Add more locals  2. Add PAD_STACK  3. Add lbArchive_LoadSections
//    4. Add arr/base setup before state assignments
```

### Deliverable
- Decision tree: "when does MWCC emit `lbzu` vs `addi+lbz`?"
- Specific source change to push mnGallery_80259868 toward `lbzu`

---

## Phase 4: Validation & Broader Application (6-8 hours)

1. Validate models against all 8 matched mngallery.c functions
2. Apply to stuck functions and achieve 100% (or prove impossible)
3. Build a "register prediction" Python script
4. Write up findings for decomp community (decomp.wiki, GitHub)
5. Cross-version comparison: diff Coloring.c between GC/1.2.5n and GC/2.7

---

## Critical Files

| File | Purpose |
|------|---------|
| `melee/build/compilers/GC/1.2.5n/mwcceppc.exe` | RE target (1.6MB PE32, x86) |
| `~/Library/ghidra/ghidra_12.0.2_PUBLIC` | Ghidra installation |
| `melee/src/melee/mn/mngallery.c` | Both stuck functions |
| `melee/src/melee/mn/mnhyaku.c` | Reference for lbzu pattern (mnHyaku_8024CD64) |
| `melee/configure.py` | Compiler flags for micro-benchmarks |
| `gc-decomp/docs/MNGALLERY_REMAINING_3.md` | Tracking doc for all attempts |

## Key Resources

| Resource | URL/Location |
|----------|-------------|
| Palanciuc & Badea 2004 (spill code) | academia.edu/8317691 |
| EpochFlame/mwcceppc-re | github.com/EpochFlame/mwcceppc-re |
| retrowin32 Issue #20 (Ghidra .gzf) | github.com/evmar/retrowin32/issues/20 |
| CodeWarrior Ref Manual | picture.iczhiku.com/resource/eetop/WhidAIWdoswLhBVN.pdf |
| MWCC 2.4.7 help (mattbruv gist) | gist.github.com/mattbruv/ab8ab3ab4f86ce94cadcd4b9348c0de9 |

## Effort Summary

| Phase | Hours | Cumulative | Standalone Value |
|-------|-------|-----------|------------------|
| 0: dumpir | 3-4 | 3-4 | May shortcut everything. Documents hidden pragma regardless. |
| 1: Ghidra | 6-8 | 9-12 | Navigable compiler map for entire decomp community. |
| 2: Coloring | 10-12 | 19-24 | First public MWCC PPC register allocator description. |
| 3: InstrSel | 8-10 | 27-34 | Unlocks lbzu-pattern functions across all GC/Wii decomps. |
| 4: Validation | 6-8 | 33-42 | Matched functions + prediction tool + published findings. |

## Risk Mitigation

- **dumpir broken:** Try newer compiler versions, then proceed to Ghidra
- **Ghidra decompilation quality low:** Binary has rich strings for anchoring; Coloring.c is relatively simple C
- **Coloring too complex for 10-12 hours:** Focus narrowly on callee-save allocation order + coalescing
- **Source-level control impossible:** Submit 10/12 with Equivalent status — still a strong PR

---

## Progress Log

### Phase 0: `#pragma dumpir` — DEAD END (2026-03-08)
- Tested on GC/1.2.5n, 2.7, 3.0a3, 3.0a3.4 with `-O4,p -proc gekko`
- Tried `#pragma dumpir on`, `#pragma opt dumpir`, env vars, output file checks
- **Result:** Dead code in all retail versions. Format strings exist (`"Dumping function %s after %s"`) but output code paths are compiled out (likely `#ifdef INTERNAL`)
- No shortcut available. Proceeded to Phase 1.

### Phase 1: Ghidra Structural Mapping — COMPLETE (2026-03-08)
- Imported mwcceppc.exe (GC/1.2.5n) into Ghidra, ran auto-analysis
- Project saved at `ghidra_projects/mwcc_re/`
- Mapped all compiler passes via string xrefs:
  - Backend pipeline at `004351c0`, IR optimizer at `0042cd10`
  - 108 source file references found in binary
  - All pass entry points identified and labeled
- Created 5 Ghidra decompilation scripts (ExtractMWCCPasses, DecompileMWCC 1-3, DecompilePeephole)
- Output saved to `/tmp/ghidra_mwcc_*.txt` files

### Phase 2: Register Coloring — SUBSTANTIALLY COMPLETE (2026-03-08)
- Reverse-engineered full Chaitin-Briggs algorithm. Documented in `docs/MWCC_COLORING_ANALYSIS.md`
- Key findings:
  - Three-class sequential coloring: GPR → FPR → CR
  - Callee-saved registers allocated r31 downward via GetReservedGPR
  - Simplify picks lowest-degree nodes first; spill metric is min(cost/degree)
  - Coloring assigns LOWEST available register bit (first set bit in bitmask)
  - Coalescing: aggressive for physical+virtual, conservative for virtual+virtual (both must be callee-save range)
- **KEY DISCOVERY (fn_802590C4):** Micro-benchmarks in container confirmed declaration order `(store, i, data, jobj, ud, zero)` produces jobj=r31 matching the target. The existing 90.3% code already has correct register assignments — the remaining diffs are scheduling/copy-propagation issues (store=ud placement, zero=i copy vs zero=0 literal), NOT register coloring.
- Answers to planned questions:
  - Q1 (coloring order): Reverse simplification order. Low-degree first to simplify, high-degree colored first → get lower register numbers.
  - Q2 (coalescing): Conservative for non-callee-save virtuals. `zero = i` must have both in callee-save range to coalesce.
  - Q3 (spill metric): cost/degree, minimum spilled first.
  - Q4 (source mapping): Declaration order affects IG node numbering which affects simplification order and thus register assignment.

### Phase 3: Instruction Selection / lbzu — SUBSTANTIALLY COMPLETE (2026-03-08)

#### Key Findings

1. **lbzu is a POST-COLORING PEEPHOLE optimization**, not instruction selection:
   - `Peep_UpdateFormCombine` at 004c94d0 (1510 bytes) is the handler
   - Registered in `PeepholeOpt_Init` (FUN_004c6320) for ~35 opcode combinations
   - Dispatched by `PeepholeOpt_PerBlock2` (FUN_004c7a30) per basic block
   - Combines `addi rX, rX, imm` + `lbz rY, 0(rX)` → `lbzu rY, imm(rX)`
   - Requires: same base register, no intervening defs, compatible sizes, no blocking flags (0x28400)

2. **SDA-addressed globals NEVER get lbzu**:
   - SDA uses `lis+addi` for non-SDA, but SDA21 relocation gives direct `lbz r0, offset(r13)`
   - No `lis+addi` pair → no peephole combining opportunity
   - Only non-SDA globals (lis+addi pattern) can trigger lbzu

3. **Statement ordering directly controls lbzu generation**:
   - When READS come before WRITES for the same struct, the compiler can defer materializing
     the full address via `lis+addi`, leaving the `addi` adjacent to the `lbz` for peephole combining
   - When WRITES come first, the compiler materializes the address early (via `addi`) for the store,
     then uses plain `lbz` later since the address is already in a register

4. **mnGallery_80259868 result (~97% match)**:
   - Statement order `S1 S2 S4 S3 S5` (cooldown, prev=cur, hovered=0, cur=0x1A, archive):
     97 insns, lbzu present, base=r10 (matching target), self-updating `addi r4, r4`
   - Only remaining diff: 3-instruction scheduling swap at positions 25-27
     (`sth r31, 0x2(r3)` at pos 25 vs target's pos 27)
   - Tested ALL 60 valid permutations of the 5 statements exhaustively
   - Trade-off: `S1 S2 S3 S4 S5` gives correct stb/sth scheduling but adds `mr r4, r0` (98 insns)
   - No declaration order, PAD_STACK size, or local variable arrangement fixes this

5. **Peephole handler table structure**:
   - Opcode-indexed linked list at `DAT_005813b0`
   - ~35 handlers registered for various opcode combinations
   - Key handlers decompiled: `Peep_UpdateFormCombine`, `Peep_CheckLiveRegs` (004cc040),
     and 13 others via `CreateAndDecompHandlers.java` Ghidra script

#### Files
- Ghidra output: `/tmp/ghidra_mwcc_peep_handlers.txt` (1826 lines, all 15 handlers decompiled)
- Ghidra scripts: `/opt/homebrew/Cellar/ghidra/12.0.2/libexec/Ghidra/Features/Base/ghidra_scripts/CreateAndDecompHandlers.java`

---

## Phase 5: IR Constant Folding & Instruction Scheduler Deep-Dive

**Goal:** Close the last 2 functions in mngallery.c by reverse-engineering the two compiler subsystems responsible for the remaining diffs.

**Context:** Phases 0-3 exhaustively covered register coloring (Chaitin-Briggs), peephole/lbzu (Peep_UpdateFormCombine), and all 720 declaration orderings / 25+ structural variants / 30+ pragma combos. The remaining diffs are:

1. **IR Constant Folding** — folds `zero = i` (i=0) to `li 0` instead of keeping register copy `addi r30, r29, 0`
2. **Instruction Scheduler** — orders instructions differently (read_ptr before write_ptr, sth/stb/addi interleaving)

**Key discovery:** `FUN_00455a70` (IRO_ConstantFolding) runs UNCONDITIONALLY in the IR optimizer pipeline — it is NOT gated by `DAT_005842e6` (the `opt_propagation` flag). This is almost certainly why `#pragma opt_propagation off` doesn't prevent the folding.

---

### Track A: IR Constant Folding (fn_802590C4's core blocker)

#### A1. Map pragma names → global flags (2-3 hrs)

- Find the pragma handler in the front-end (search for string `"opt_propagation"` xrefs)
- Trace which `DAT_005842e*` globals each `#pragma opt_*` sets/clears
- **Critical question:** Is there ANY flag that gates `FUN_00455a70`?
- Check if `#pragma optimization_level 0` bypasses the entire IR optimizer
- Files: Ghidra project, look in CPrep.c / CParser.c area of the binary

#### A2. Decompile `FUN_00455a70` (IRO_ConstantFolding) (3-4 hrs)

- Understand what folding operations it performs
- Does it fold `x = y` when `y` is known constant? Or just `x = 3 + 5` → `x = 8`?
- Is there a condition check we missed that could gate it?
- Create Ghidra decompile script targeting this function + callees

#### A3. Decompile `FUN_00458970` (IRO_CopyAndConstantPropagation) (3-4 hrs)

- This IS gated by `opt_propagation` flag (`DAT_005842e6`)
- Understand: does it replace `zero = i` with `zero = 0` (value propagation)?
- Or does the standalone constant folder in A2 handle this?
- Source file: `IroPropagate.c` (string at `00553d3c`)

#### A4. Micro-benchmark validation (2-3 hrs)

Compile in Docker with Melee flags (`-O4,p -proc gekko`):
```c
// Test 1: constant copy — does it fold?
int i = 0; int z = i; use(z); i++;

// Test 2: every relevant #pragma combination
#pragma optimization_level 0  // does this bypass the entire optimizer?
#pragma opt_propagation off    // known to NOT prevent folding
#pragma opt_dead_code off      // etc.

// Test 3: non-constant prevents folding?
int i = func(); int z = i; use(z);  // non-constant i should preserve the copy

// Test 4: timing — before or after instruction selection?
// If folding is IR-level, it happens before isel. If machine-level, after.
```

#### A-Success Criteria

- **Win:** Find a pragma/flag that prevents `FUN_00455a70` from folding `zero = i`, getting `addi` instead of `li`
- **Partial win:** Confirm no pragma prevents it, but document the exact pass for the decomp community
- **Key insight:** If constant folding is truly unconditional, no source-level trick can prevent it. The 11-diff floor is absolute.

---

### Track B: Instruction Scheduler (both functions need this)

#### B1. Annotate `FUN_004ccdc0` (scheduler priority/SelectBest) (3-4 hrs)

This is the scheduler's core: selects which ready instruction to emit next.
Already partially decompiled in `/tmp/ghidra_mwcc_lbzu.txt` lines 827-889.

Confirm the priority cascade:
1. **Ready check**: predecessors done, earliest start ≤ current cycle
   - `*(short *)(node + 0x18) != 0` = pending predecessors
   - `param_2 < *(ushort *)(node + 0x12)` = earliest start > current cycle
2. **Schedulability check**: `(*(code **)(DAT_00581b80 + 0x10))(node[3])` — processor model callback
3. **Slack preference**: `*(ushort *)(node + 0x14)` — tighter slack wins (lower value = schedule first)
4. **Successor count**: count successors with `*(short *)(succ[1] + 0x18) == 1` — more ready successors = wins
5. **Latency**: `*(ushort *)(node + 0x16)` — higher latency starts earlier
6. **Tie-breaker: opcode class** — `(&DAT_005654b9)[opcode * 0x10]` — but ONLY if `DAT_00587648 != 0` (for GC/PPC, this is always 0, so this tie-breaker never fires)
7. **Final fallback: list order** — first found in linked list wins (= IR emission order)

Map all node struct field offsets:
| Offset | Type | Meaning |
|--------|------|---------|
| `+0x00` | ptr | next node in ready list |
| `+0x08` | ptr | successors list |
| `+0x0C` | ptr | instruction reference |
| `+0x12` | u16 | earliest start cycle |
| `+0x14` | u16 | slack (latest start - earliest start) |
| `+0x16` | u16 | latency |
| `+0x18` | s16 | pending predecessor count |

#### B2. Decode PPC750 processor model table (4-6 hrs)

Table at `DAT_00574d70` — contains function pointers for latency, schedulability.
- Decompile the `get_latency(instruction)` function pointer at `[2]` (offset +0x10 from table base)
- **Critical:** What are latencies for `li`, `addi`, `add`, `lwz`, `stw`, `sth`, `stb`?
- These specific opcodes are in our stuck functions' diff regions
- If `addi` and `add` have different latencies, that explains scheduling differences

Known table structure (from SelectBest decompilation):
```
DAT_00581b80 → processor model struct:
  +0x00: ???
  +0x04: ???
  +0x08: ???
  +0x0C: ???
  +0x10: is_schedulable(instr) function pointer — called during ready check
  +0x14: ???
```

#### B3. Trace source order → instruction list order (3-4 hrs)

- Instruction selection at `FUN_004be990` converts IR → machine instructions
- Does it preserve IR statement order in the instruction list?
- If the scheduler's tie-breaker is list order, and list order = source order, then **source statement reordering DIRECTLY controls scheduling ties**
- Trace: source statement → IR node → instruction selection → instruction list → scheduler input

#### B4. Micro-benchmark: scheduling order validation (3-4 hrs)

```c
// Test 1: Two independent addi — does source order = output order?
int a = get() + 1;
int b = get() + 2;
use(a, b);  // swap source order, check if output order swaps

// Test 2: lis+addi followed by li — does scheduler interleave?
extern char *arr;
int x = 5;
arr[0] = 'a';  // needs lis+addi for arr
// Does 'li r0, 5' appear before or after the lis+addi?

// Test 3: fn_802590C4's exact pattern
void *ud_copy = ud;
void *read_ptr = (char*)ud + offset;
// Swap source order → does output swap?

// Test 4: mnGallery_80259868's pattern
// lis r4, ha / addi r4, r4, lo / li r0, 5
// Vary source order of arr setup vs constant assignment
```

#### B5. Test `addi r4,r4,lo` vs `addi r0,r4,lo + mr r4,r0` (2-3 hrs)

mnGallery_80259868 has this extra `mr` instruction — when r4 is still live, compiler uses temp register.

```c
// Hypothesis: if arr assignment comes BEFORE other uses of the same register,
// the self-modifying `addi r4, r4, lo` should be possible.
// If arr comes AFTER, r4 is still live → compiler uses temp + mr.

// Test: change when arr is first referenced relative to other statements
// that use the same calling-convention register.
```

#### B-Success Criteria

- **Win:** Understand priority function well enough to predict scheduling. Design source code where tie-breaking by list order produces the target output.
- **Partial win:** Confirm scheduler behavior empirically with micro-benchmarks even without full RE.

---

### Track C: Apply Findings (after A and B)

#### C1. Fix fn_802590C4 (2-4 hrs)

- If A finds a way to prevent constant folding → apply it (the dependency graph changes, which may also fix scheduling)
- If only B gives results → try source reordering to exploit tie-breaking
- Note: if `zero = i` stays as `li 0`, the dependency graph differs from target, so scheduling priorities also differ. Constant folding fix is the primary lever.

#### C2. Fix mnGallery_80259868 (2-4 hrs)

- Use B5 findings to eliminate the extra `mr r4, r0`
- Use B4 findings to fix the 3-instruction scheduling swap
- If neither works, this function may also be at compiler floor

---

### Execution Strategy

**Parallel tracks:** A1+B1 first (both are Ghidra annotation work, 1 session). Then A2+B3 (both are decompilation, 1 session). Then micro-benchmarks A4+B4+B5 (validation, 1 session). Finally C1+C2 (application).

**Minimum viable:** If time is limited, do A1+A4 and B4+B5 only (pragma mapping + micro-benchmarks, ~8 hrs). This skips deep RE but may still find the lever through empirical testing.

**Estimated total:** 25-40 hours for full RE, or 8-12 hours for the empirical shortcut.

---

### Fallback: Accept as Equivalent

If RE confirms these are true compiler floors:
- Submit PR "Complete mn/mngallery.c" with 10/12 matched + 2 Equivalent
- Document compiler internals as the reason (first public MWCC constant folding + scheduler analysis)
- Move to other mn/ targets: mnstagesel.c (99.2%), mnmain.c (~98%), mnsound.c (~93%)

---

### Key Addresses (Phase 5)

| Address | Function | Subsystem |
|---------|----------|-----------|
| `0042cd10` | IROPipeline | IR optimizer driver |
| `00455a70` | IRO_ConstantFolding | **UNGATED** constant folder |
| `00458970` | IRO_CopyAndConstantPropagation | Gated by `DAT_005842e6` (opt_propagation) |
| `004582f0` | CopyPropagation engine | Gated by `DAT_005842e6` |
| `004ccdc0` | Scheduler_SelectBest | Priority/tie-breaking function |
| `004ccbf0` | Scheduler_Main | List scheduler loop |
| `004ccf10` | Scheduler_BuildDAG | Dependency graph construction |
| `00574d70` | PPC750_ModelTable | Processor latency model |
| `004c6100` | InstrSchedule | Scheduling pass entry point |
| `004be990` | InstrSelection | IR → machine instructions |

### Pragma → Global Flag Map (to be filled in A1)

| Pragma | Global | Default | Effect |
|--------|--------|---------|--------|
| `opt_propagation` | `DAT_005842e6` | on | Gates CopyAndConstantPropagation (00458970) + CopyPropagation (004582f0) |
| `opt_dead_code` | `DAT_005842ea` | on | Gates RemoveUnreachable in cleanup loop |
| `opt_common_subs` | `DAT_005842e4` | on | Gates CommonSubexpressions (0044f1c0, 0044ecc0, 0044df00) |
| `opt_loop_invariants` | `DAT_005842e5` | on | Gates FindLoops + LoopInvariant code motion |
| `opt_strength_reduction` | `DAT_005842e8` | on | Gates FindLoops (shared with loop_invariants) |
| `opt_unroll_loops` | `DAT_005842ed` | on | Gates LoopUnroller (0045fa80), also needs `DAT_005842e2 == 0` |
| `opt_lifetimes` | `DAT_005842e7` | on | Gates UseDef pass (00459b30, shared with propagation) |
| `optimization_level` | `DAT_005842e1` | >0 | Gates ENTIRE IR optimizer (all passes except flow graph build) |
| `opt_branch_folding` | `DAT_005842ef` | off | Controls loop iteration count (1 vs 2 passes through optimizer) |
| `opt_peephole` | TBD | on | Gates peephole passes (both pre- and post-coloring) |
| TBD | TBD | — | **Does anything gate FUN_00455a70 (ConstantFolding)?** |
