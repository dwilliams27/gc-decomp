# North Star: Taking Untouched Files from 0% to 100% Match

## Context

Our decomp agent (gc-decomp) has matched ~100 functions and gotten PR #2217 merged, but reviewer feedback indicated the output quality was "mid" — too much raw pointer arithmetic, unnecessary casts, magic numbers instead of enums, missing macros/idioms. The current system operates purely at the per-function tactical level on files that already have some code.

**Goal:** Completely cover 3 truly untouched mn/ files — mnevent (14 functions), mnitemsw (10 functions), mngallery (2 functions) — from zero to fully matched and linked. Then scale to more mn/ files.

**Why these files:**
- All 3 are empty stubs or near-empty (1-44 lines of C code), truly untouched
- No recent substantive activity — no risk of merge conflicts during our work
- Same module as 3 matched files (mndeflicker, mnhyaku, mnlanguage) we can study
- 26 total functions, ~6,600 lines of assembly — ambitious but achievable in weeks
- PR story: "Complete 3 untouched menu files from nothing to fully matched"

**Key insight:** This requires two new capability layers:
1. **Strategic layer** — Holistic understanding: structs, patterns, function ordering, cross-function context
2. **Quality layer** — Expert conventions: proper macros, meaningful names, correct m2c flags, idiomatic patterns

---

## What Experts Do (Distilled from PR Reviews & Git History)

### How Areas Go 0 → 100%

From studying the db/ module (went 0→100% in one coordinated PR), ft/ module (months of infrastructure then rapid matching), and mn/ matched files:

**Phase 1: Infrastructure** (invisible but critical)
- Study assembly to identify structs, enums, shared types
- Create/extend headers with proper struct definitions
- Identify inline functions (repeated line numbers across disasm)
- Determine correct m2c flags per file (--union-field, --void-field-type)
- Understand dependencies (what other modules does this code call?)

**Phase 2: Easy wins** (build momentum and context)
- Start with smallest, simplest functions
- Each match reveals struct fields, calling conventions, patterns
- Propagate discoveries back to headers

**Phase 3: Core functions** (the hard middle)
- Tackle medium functions with full context from Phase 2
- Use reference functions (similar already-matched code) as templates

**Phase 4: Hard functions + cleanup** (the last mile)
- Large/complex functions with all context available
- Style cleanup pass: enums, macros, names, doc comments
- Ensure no collateral damage

### What Maintainers Require (from reviewing PRs #2217, #2138, #2143, #2172, #2154, #2128, #2072)

**Will block merge:**
- Raw pointer arithmetic (`(u8*)ptr + 0xNN`) — must use struct fields or M2C_FIELD
- Wrong union members (m2c defaults to wrong variant)
- Regressions in other functions
- Fake/hacked matches

**Will generate review comments:**
- Magic numbers → use enums (`FTKIND_KIRBY` not `4`, motion state enums not `0x179`)
- Unnecessary casts (Claude generates many — reviewers specifically called this out)
- Bad variable names (`var` → `i`/`j` for indices, meaningful names otherwise)
- Missing macros (`GET_ITEM(gobj)`, `GET_FIGHTER(gobj)`, `ABS()`)
- `1`/`0` → `true`/`false` for bool returns
- Field-by-field struct copy → single assignment (`*pos = attrs->x4`)
- Separate zero assignments → chain (`x = y = z = 0.0F`)
- Match % comments (noise — objdiff shows this)
- TODOs must be `/// @todo` doc comments (not `// @TODO`)
- `gobj->user_data` → use GET_* macro inline pattern
- Missing helper functions (e.g., `itResetVelocity` instead of manual zeroing)
- Struct definitions in wrong headers

### Key m2c Techniques (from lukechampine's PR #2076 and Claude Skills)

lukechampine matched entire files at ~95% automation using:
- `--union-field Item_ItemVars:leadead` — correct union member selection
- `--void-field-type Article.x4_specialAttributes:itLeadeadAttributes` — correct void* casting
- `--stack-structs` — infer stack struct types (Vec3 copies become single assignments)
- `--globals=none --no-casts` — cleaner starting point
- Domain-specific skills (items vs fighters vs stages have different idioms)
- Mismatch knowledge base documenting common diff patterns and their fixes:
  - Stack size mismatch → PAD_STACK or variable reuse
  - Field-by-field copy (lfs/stfs vs lwz/stw) → single struct assignment
- Opcode sequence matching to find similar already-decompiled reference functions
- Updating function declarations in headers (UNK_RET/UNK_PARAMS → actual types) before running m2c

---

## Execution Plan

### Sub-Plan 1: Expert Decomp Guide + Quality Checker
**Priority: Do first.**

**1a. Write the Expert Decomp Guide** (memory file or docs/)
- [ ] Deep-read the 3 matched mn/ files (mndeflicker.c, mnhyaku.c, mnlanguage.c)
- [ ] Read mn/ headers to catalog structs, enums, types (types.h, forward.h, inlines.h)
- [ ] Catalog mn/ patterns: GObj lifecycle, input handling, animation, menu state
- [ ] Read 5+ more high-signal PRs for patterns we missed
- [ ] Distill into actionable guide:
  - mn/ module patterns (init/think/draw/destroy callbacks, HSD usage)
  - Quality checklist (every reviewer concern as pass/fail)
  - m2c flag reference for menu code
  - Common mismatch patterns and fixes

**1b. Build post-match quality checker** (integrate into registry.py guardrails)
- [ ] Magic number detector — flag bare integers matching known enum values
- [ ] Macro usage checker — detect `gobj->user_data` → should use GET_ITEM/GET_FIGHTER
- [ ] Cast auditor — flag unnecessary casts
- [ ] Bool return checker — flag `return 1`/`return 0` in bool functions
- [ ] Variable name checker — flag `var_*` names
- [ ] Struct copy checker — detect field-by-field Vec3/struct copies
- [ ] Match % comment stripper

**Critical files:**
- `src/decomp_agent/tools/registry.py` — Add checks to `_validate_write()` pipeline
- `src/decomp_agent/agent/prompts.py` — Inject quality rules into system prompt

### Sub-Plan 2: m2c Flag Support + Strategic Planner
**Priority: Interleave with early execution.**

**2a. Upgrade m2c integration**
- [ ] Add `--union-field`, `--void-field-type`, `--stack-structs`, `--globals=none`, `--no-casts` to m2c_tool.py
- [ ] Build flag inference for menu code
- [ ] Better m2c seeding with inferred flags

**2b. Build strategic area planner**
- [ ] Module analyzer: given untouched files, produce:
  - Functions sorted by size/difficulty
  - Dependency graph (which functions call which)
  - Required structs (scan assembly for field access patterns)
  - Suggested work order (easy → hard)
- [ ] Reference function finder: for each target, find similar matched functions
- [ ] Cross-function context propagation

**Critical files:**
- `src/decomp_agent/tools/m2c_tool.py` — Flag support
- New: `src/decomp_agent/melee/area_planner.py` — Strategic analysis

### Sub-Plan 3: Execute on Target (mnevent + mnitemsw + mngallery)
**Priority: Start after Sub-Plan 1, continue during Sub-Plan 2.**

**Target files (all in melee repo):**

| File | C Lines | Asm Lines | Functions | Status |
|------|---------|-----------|-----------|--------|
| `mn/mnevent.c` | 1 (empty) | 2,169 | 14 | Empty stub |
| `mn/mnitemsw.c` | 1 (empty) | 2,426 | 10 | Empty stub |
| `mn/mngallery.c` | 44 | 1,450 | 2 | Near-empty |
| **Total** | — | **6,045** | **26** | — |

**3a. Infrastructure setup**
- [ ] Study assembly for all 3 files
- [ ] Identify needed structs (menu state, callbacks, UI elements)
- [ ] Create/extend mn/ headers
- [ ] Determine m2c flags per function
- [ ] Find reference functions from matched mn/ files
- [ ] Write .c scaffolding (includes, forward declarations, static data)

**3b. Match functions (easy → hard)**
- [ ] Start with mngallery (2 functions, smallest)
- [ ] Then small mnitemsw/mnevent functions
- [ ] After each batch, review against quality checklist
- [ ] Propagate struct discoveries to headers
- [ ] Progress to larger functions with accumulated context

**3c. Quality pass + PR preparation**
- [ ] Run quality checker on all matched functions
- [ ] Fix flagged issues (enums, macros, names, casts)
- [ ] Verify no collateral damage
- [ ] Clean commits (one per file or logical unit)
- [ ] PR description with approach summary

### Sub-Plan 4: Scale to More mn/ Files
**Priority: After POC proves the system.**

- [ ] Apply lessons to remaining dormant mn/ files
- [ ] Focus on files with minimal existing code next
- [ ] PR in logical chunks (2-4 files per PR)
- [ ] Iterate on reviewer feedback

---

## Verification

- **Sub-Plan 1 done:** Quality checker retroactively catches all issues from PR #2217 review
- **Sub-Plan 2 done:** Agent auto-determines correct m2c flags for mn/ functions; area planner produces sensible work order
- **Sub-Plan 3 done:** All 26 functions in 3 files matched, pass quality checker, PR merged with minimal friction
- **Sub-Plan 4 done:** Additional mn/ files completed and merged
