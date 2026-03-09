# MWCC Register Coloring Algorithm - Reverse Engineering Analysis

## Source: Ghidra decompilation of mwcceppc.exe (GC/1.2.5n)

## High-Level Architecture

### Compiler Pass Order (from `FUN_004351c0` — Backend Pipeline)
```
1. Instruction Selection (FUN_004be990)
2. Instruction Scheduling 1 (FUN_004c6100) — "BEFORE SCHEDULING" → "AFTER INSTRUCTION SCHEDULING"
3. Peephole Forward (FUN_004ccae0) — "AFTER PEEPHOLE FORWARD"
4. Register Coloring (FUN_004cdef0) — "AFTER REGISTER COLORING"
5. Epilogue/Prologue (FUN_004c62f0?) — "AFTER GENERATING EPILOGUE, PROLOGUE"
6. Peephole Optimization (FUN_004c60b0) — "AFTER PEEPHOLE OPTIMIZATION"
7. Final Instruction Scheduling (FUN_004c6100 again) — "AFTER INSTRUCTION SCHEDULING" (2nd)
```

**Key insight:** Register coloring runs AFTER the first instruction scheduling pass and peephole forward. The `lbzu` pattern decision is NOT in coloring — it's in instruction selection or peephole.

## Register Coloring Driver (`FUN_004cdef0` @ Coloring.c)

Three register classes colored sequentially:
1. **GPR** (class 9): `DAT_0058849a` = num available GPRs
2. **FPR** (class 0): `DAT_0058846e` = num available FPRs
3. **CR** (class 1): `DAT_0058846c` = num available CRs

Each class uses the same **Chaitin-Briggs iterated coloring**:
```
do:
  BuildInterferenceGraph(function, regClass, numRegs)   // FUN_00530a00
  Simplify(regClass)                                     // FUN_004ce5f0/4ce850/4ce710
  colorResult = Color(regClass, numAvail, numRegs)       // FUN_004ce400
  success = Check(regClass, colorResult)                 // FUN_004ce2d0
  if !success:
    SpillAndRetry(regClass, numRegs)                     // FUN_00531800
while !success
```

## Interference Graph Builder (`FUN_00530a00`)

Five sub-phases:
1. **InitNodes** (`FUN_005301b0`) — Allocate node array, mark return-value/parameter registers
2. **AddEdges** (`FUN_00530a80`) — Walk instructions, add interference from live ranges
3. **Coalesce** (`FUN_00531290`) — Aggressive coalescing with interference check
4. **ComputeDegrees** (`FUN_00530e00`) — Count neighbors for each node
5. **ComputeCosts** (`FUN_00530c00`) — Allocate interference graph node structures, build neighbor lists

## Node Structure (from `FUN_00530c00`)

Each node in `DAT_00587e3c[i]` is a struct:
```c
struct ColorNode {
    /* 0x00 */ ColorNode *next;        // linked list for simplify stack
    /* 0x04 */ int varRef;             // reference to the variable/temp
    /* 0x08 */ int cost;               // spill cost (used in spill metric)
    /* 0x0C */ short nodeIndex;        // = i (the node's index)
    /* 0x0E */ short degree;           // current degree (edges count)
    /* 0x10 */ short color;            // assigned physical register (-1 = unassigned)
    /* 0x12 */ byte flags;             // bit0=spilled, bit1=simplified, bit2=coalesced, bit4=hi_reg, bit5=lo_reg
    /* 0x14 */ short numNeighbors;     // number of neighbors
    /* 0x16 */ short neighbors[];      // variable-length neighbor list
};
```

## Simplify Phase (Chaitin's Algorithm)

**`FUN_004ce400` (Color_Assign)** — Combined Simplify + Select with spill heuristic:

### Phase 1: Iterative Simplification
```
Loop until no more progress:
  For each node i >= 32 (skip physical registers):
    if node[i].flags & 0x06 == 0 (not simplified or coalesced):
      if node[i].degree < K:
        // Low degree → push to simplify stack
        for each neighbor j of node[i]:
          neighbor[j].degree -= 1
        node[i].flags |= 0x02 (simplified)
        push node[i] to stack
      else:
        // High degree → add to spill candidate list
```

### Phase 2: Spill Selection
```
While spill candidates remain:
  Find candidate with MINIMUM (cost / degree)    ← SPILL METRIC
  Push that candidate onto stack
  Then run iterative simplification again (may enable more low-degree removals)
```

### Phase 3: Coloring (in `FUN_004ce2d0`)
```
Pop nodes from stack (reverse simplification order):
  For each node:
    Build bitmask of colors used by already-colored neighbors
    available = ~usedByNeighbors & availableRegisters
    if available != 0:
      color = FIRST SET BIT of available (lowest register number)     ← CRITICAL
    else:
      try GetReservedRegister()
      if that fails: mark as SPILLED
```

## Key Finding: Register Assignment Order

**The coloring assigns the LOWEST AVAILABLE register number first.**

This means:
- Variables simplified LAST (high degree / long lifetime) get colored FIRST → get lower register numbers
- Variables simplified FIRST (low degree / short lifetime) get colored LAST → get higher register numbers
- **For fn_802590C4**: Micro-benchmarks confirmed that declaration order `(store, i, data, jobj, ud, zero)` produces jobj=r31, matching the target. The existing 90.3% code already has the correct register assignments. The remaining diffs are scheduling/copy-propagation issues (store=ud placement before/after call, zero=i register copy vs zero=0 literal), NOT register coloring problems.

## Coalescing Algorithm (`FUN_00531290`)

The coalescing happens in the `BuildInterferenceGraph` phase, using Union-Find:

```
DAT_0058308c = interference bit matrix  (triangular: n*(n-1)/2 + m)
DAT_0058308c = union-find parent array  (separate allocation)

For each basic block:
  For each instruction that is a COPY (opcode == local_18):
    src = find(instruction.src_reg)
    dst = find(instruction.dst_reg)
    if src == dst: delete instruction (already coalesced)
    if !interfere(src, dst):
      if either is a physical register (< 32):
        coalesce (merge into lower-numbered)
      else:
        // Both are virtual: check if both in callee-save range
        if src in [DAT_005882da..DAT_005882e2]:  // GPR callee-save range
          if dst in same range: coalesce
        // Similar for FPR and CR ranges
```

### Coalescing Heuristic Details:
1. **Physical + Virtual**: Always coalesce if no interference (the virtual gets the physical's register)
2. **Virtual + Virtual**: Only coalesce if BOTH are in the callee-save range
   - GPR callee-save: `DAT_005882da` to `DAT_005882e2`
   - FPR callee-save: `DAT_005882dc` to `DAT_005882e0`
3. **Merge direction**: Lower-numbered node absorbs higher-numbered node

### Implications for fn_802590C4:
The coalescing heuristic is CONSERVATIVE for non-callee-save registers. If `zero = i` involves two virtual regs that are NOT both in callee-save range, they WON'T be coalesced. The target code shows coalescing (`addi r30, r29, 0`), which means the original compiler saw both as callee-save candidates.

## Spill Metric

```c
spill_metric = cost / degree
```
Spill the variable with LOWEST cost/degree ratio. Variables with many neighbors (high degree) relative to their cost are spilled first — they free up the most registers.

`cost` (offset 0x08) is computed during `ComputeDegrees` and relates to how many times the variable is used.

## Available Register Tracking

- `DAT_00581350[32]` = GPR availability array (0 = available, nonzero = reserved)
- `DAT_00581310[32]` = FPR availability array
- `DAT_00581330[32]` = CR availability array
- Init functions copy these to a working set before each coloring attempt

## Function Index

| Address | Name | Purpose |
|---------|------|---------|
| `004351c0` | BackendPipeline | Main backend driver with all pass markers |
| `0042cd10` | IROPipeline | IR optimizer pipeline |
| `004cdef0` | Coloring_Main | Register coloring driver (3 reg classes) |
| `004ce400` | Color_Assign | Simplify + spill selection |
| `004ce2d0` | Color_Check | Color assignment (select phase) |
| `004ce5f0` | GPR_PreColor | Pre-color GPR physical registers |
| `004ce850` | FPR_PreColor | Pre-color FPR physical registers |
| `004ce710` | CR_PreColor | Pre-color CR field registers |
| `004ce1a0` | Color_Commit | Write colors back to instructions |
| `00530a00` | BuildIG | Interference graph builder (5 sub-phases) |
| `005301b0` | IG_InitNodes | Allocate and initialize IG nodes |
| `00530a80` | IG_AddEdges | Add interference edges from liveness |
| `00531290` | IG_Coalesce | Aggressive coalescing with union-find |
| `00530e00` | IG_ComputeDegrees | Count neighbors per node |
| `00530c00` | IG_ComputeCosts | Build node structs with neighbor lists |
| `00531800` | SpillAndRetry | Insert spill code and retry coloring |
| `004ccae0` | Peephole | Peephole optimization pass |
| `004c6100` | InstrSchedule | Instruction scheduling pass |
