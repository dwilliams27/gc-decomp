# Automated Melee Decompilation System

## Context
The Super Smash Bros. Melee decompilation (github.com/doldecomp/melee) is ~53% complete at the function level (~69% at the file level: 668 Matching, 299 NonMatching, 3 Equivalent out of 970 translation units). Each function has a **binary pass/fail verification**: compile with CodeWarrior, compare output byte-for-byte against original DOL via objdiff. This makes it an ideal target for AI automation — GPT 5.3 can iterate on C code in a loop until it achieves a 100% match, with zero ambiguity about success.

We will build a system that:
1. Picks unmatched functions from the melee decomp
2. Feeds target assembly + context to GPT 5.3
3. Lets the agent iterate: write C → compile → see diff → fix → repeat
4. Verifies matches with the existing toolchain (objdiff-cli)
5. Tracks progress across all functions toward completing the decomp

## Architecture Overview

```
┌─────────────────────┐
│   Orchestrator       │  Picks functions, manages queue, tracks progress
│   (CLI + DB)         │
└──────┬──────────────┘
       │
┌──────▼──────────────┐     ┌──────────────┐
│   Agent Loop         │────▶│  OpenAI API  │
│   (tool dispatch)    │◀────│  (GPT 5.3)   │
└──────┬──────────────┘     └──────────────┘
       │
┌──────▼──────────────┐
│   Tool Layer         │
│                      │
│  ┌─────────────────┐ │
│  │ Ghidra Headless  │ │  Initial decompilation, type info, disassembly
│  └─────────────────┘ │
│  ┌─────────────────┐ │
│  │ m2c              │ │  PowerPC assembly → C (matching-focused)
│  └─────────────────┘ │
│  ┌─────────────────┐ │
│  │ Build + Verify   │ │  ninja build → objdiff-cli → match report
│  └─────────────────┘ │
│  ┌─────────────────┐ │
│  │ decomp-permuter  │ │  Automated permutation for near-matches
│  └─────────────────┘ │
│  ┌─────────────────┐ │
│  │ File I/O         │ │  Read/write .c and .h files in the melee repo
│  └─────────────────┘ │
│  ┌─────────────────┐ │
│  │ Context Builder  │ │  Gather headers, structs, nearby matched funcs
│  └─────────────────┘ │
└─────────────────────┘
```

## Tech Stack
- **Python 3.12+** with **uv**
- **Dependencies**: `openai`, `pydantic`, `sqlmodel`, `click`, `rich`, `structlog`
- **External tools** (already in melee repo or installable):
  - CodeWarrior GC/1.2.5n (via Docker or Wine/WiBo)
  - decomp-toolkit (dtk) v1.6.2
  - objdiff-cli v3.0.0
  - m2c (pip: `git+https://github.com/matt-kempster/m2c.git`)
  - decomp-permuter
  - Ghidra 11+ with GameCube loader + pyhidra
  - ninja build system

## Project Structure

```
gc-decomp/
├── pyproject.toml
├── config/
│   └── default.toml                  # Paths, model config, agent params
├── src/decomp_agent/
│   ├── __init__.py
│   ├── cli.py                        # Click CLI: run, status, queue, export
│   ├── config.py                     # Config loading + validation
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── registry.py               # Tool registration + OpenAI schema gen
│   │   ├── schemas.py                # Pydantic models for all tool params
│   │   ├── ghidra.py                 # Ghidra headless: decompile function, get types
│   │   ├── m2c_tool.py               # Run m2c on target assembly
│   │   ├── build.py                  # ninja build + objdiff-cli match check
│   │   ├── permuter.py               # Run decomp-permuter on near-matches
│   │   ├── source.py                 # Read/write C source files in melee repo
│   │   └── context.py                # Build context: headers, structs, nearby funcs
│   │
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── loop.py                   # Core OpenAI tool-calling loop
│   │   ├── prompts.py                # System prompt for decomp workflow
│   │   └── context_mgmt.py           # Conversation context truncation
│   │
│   ├── orchestrator/
│   │   ├── __init__.py
│   │   ├── queue.py                  # Function queue: pick next, prioritize
│   │   ├── runner.py                 # Run agent on a function, handle lifecycle
│   │   └── batch.py                  # Batch mode: iterate through queue
│   │
│   ├── melee/
│   │   ├── __init__.py
│   │   ├── project.py                # Parse melee repo: configure.py, symbols, splits
│   │   ├── functions.py              # Extract function list + match status from objdiff report
│   │   └── report.py                 # Generate/parse objdiff report.json
│   │
│   └── models/
│       ├── __init__.py
│       └── db.py                     # SQLModel: Function, Attempt, MatchResult
│
├── tests/
└── data/                             # Runtime (gitignored): decomp.db, logs/
```

## How the Melee Build System Works (what we're wrapping)

The melee repo already has a complete build pipeline:
1. `python configure.py` → generates `build.ninja` + `objdiff.json`
2. `ninja` → compiles all .c files with CodeWarrior, links into main.dol
3. `objdiff-cli report generate` → produces `report.json` with per-function match data
4. `ninja diff` → SHA-1 comparison of built DOL vs original

**Key files in melee repo:**
- `configure.py` — lists every .c file as `Matching`, `NonMatching`, or `Equivalent`
- `config/GALE01/symbols.txt` — all known symbol names + addresses
- `config/GALE01/splits.txt` — how DOL is split into translation units
- `tools/easy_funcs.py` — finds unmatched functions sorted by size/match%
- `tools/m2ctx/m2ctx.py` — generates context headers for decomp.me

## Tool Definitions (10 tools exposed to the agent)

### Analysis Tools
| Tool | Purpose | Implementation |
|------|---------|----------------|
| `get_target_assembly` | Get the original PowerPC assembly for a function | Extract from dtk-generated disassembly or objdiff report |
| `get_ghidra_decompilation` | Get Ghidra's auto-decompiled C for a function | pyhidra headless, `DecompInterface` on DOL |
| `get_m2c_decompilation` | Get m2c's matching-focused C output | Run m2c on the function's assembly |
| `get_context` | Get headers, structs, typedefs, and nearby matched functions | Concatenate relevant headers + m2ctx.py output |

### Source Editing Tools
| Tool | Purpose | Implementation |
|------|---------|----------------|
| `read_source_file` | Read a .c or .h file from the melee repo | Direct file read |
| `write_function` | Write/replace a function's C code in the source file | Parse file, find function boundaries, replace |

### Verification Tools
| Tool | Purpose | Implementation |
|------|---------|----------------|
| `compile_and_check` | Build the file and check if the function matches | `ninja <object>` + `objdiff-cli diff` on that object |
| `get_diff` | Get detailed assembly diff showing what doesn't match | `objdiff-cli diff` with detailed output |
| `run_permuter` | Run decomp-permuter on a near-match to find correct permutation | subprocess decomp-permuter with timeout |

### Progress Tool
| Tool | Purpose | Implementation |
|------|---------|----------------|
| `mark_complete` | Record that a function is verified matching | Update DB, optionally update configure.py status |

## Agent Workflow (what the system prompt teaches)

```
For each function:
1. GET ORIENTATION
   - get_target_assembly(func_name) → see what we're matching
   - get_context(func_name) → see headers, types, related code
   - Optionally: get_ghidra_decompilation, get_m2c_decompilation for starting points

2. WRITE INITIAL ATTEMPT
   - Study the assembly, understand control flow
   - Use Ghidra/m2c output as starting point, clean it up
   - write_function(file, func_name, c_code)

3. COMPILE AND CHECK
   - compile_and_check(file) → get match result
   - If 100% match → mark_complete, done!
   - If not → get_diff to see what's wrong

4. ITERATE
   - Read the diff: instruction mismatches, register allocation, reordering
   - Adjust C code: variable types, operation order, cast insertion, temp vars
   - write_function with updated code
   - compile_and_check again
   - Repeat until match or max iterations

5. IF CLOSE (>90% match)
   - run_permuter to automatically try permutations
   - If permuter finds match, apply it

6. IF STUCK
   - Try different approach: different variable types, different loop structure
   - Read nearby matched functions for patterns
   - Give up after max iterations, record best match % achieved
```

## Function Queue and Prioritization

The orchestrator picks functions in this order:
1. **Smallest unmatched functions first** — higher chance of success, builds momentum
2. **Functions with existing partial matches** — NonMatching files already have C code
3. **Functions in mostly-matched files** — matching the last few functions in a file flips it to Matching
4. **Skip functions others are working on** — check scratches.txt for active decomp.me work

Source of truth: `objdiff-cli report generate` → `report.json`, which lists every function with:
- Function name and address
- Match percentage (fuzzy match score)
- Which object file it belongs to
- Size in bytes

## Data Model (SQLite)

```python
class Function(SQLModel, table=True):
    """Tracks every function in the melee decomp."""
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)           # e.g., "ftFox_SpecialLw_Enter"
    address: int                             # Address in DOL
    size: int                                # Size in bytes
    object_file: str                         # e.g., "melee/ft/chara/ftFox/ftFox_SpecialLw.c"
    initial_match_pct: float                 # Match % before any AI attempts
    current_match_pct: float                 # Best match % achieved
    status: str = "pending"                  # pending, in_progress, matched, failed, skipped
    attempts: int = 0                        # Number of agent attempts
    total_tokens: int = 0                    # Total tokens spent
    matched_at: datetime | None = None

class Attempt(SQLModel, table=True):
    """Each agent run on a function."""
    id: int | None = Field(default=None, primary_key=True)
    function_id: int = Field(foreign_key="function.id")
    started_at: datetime
    completed_at: datetime | None = None
    iterations: int = 0
    best_match_pct: float = 0.0
    tokens_used: int = 0
    success: bool = False
    final_code: str | None = None           # The C code that matched (or best attempt)
    error: str | None = None
```

## Ghidra Integration

**Setup**: Run Ghidra headless once to create a project with the Melee DOL analyzed:
```
analyzeHeadless /path/to/ghidra_project MeleeProject \
  -import orig/GALE01/main.dol \
  -processor PowerPC:BE:32:Gekko_Broadway \
  -postScript ApplySymbols.java  # Apply symbols.txt names
```

**Per-function decompilation** (via pyhidra in Python):
```python
import pyhidra
pyhidra.start()
# Open existing project, get function by name/address, decompile
```

This runs once at setup. The analyzed project is cached. Per-function decompilation is fast (~1-2 seconds) since analysis is already done.

**Alternative**: If Ghidra setup is too heavy, m2c alone is a good starting point. Ghidra adds value for type inference and struct identification but isn't strictly required.

## Build Environment (Docker)

The melee project provides `ghcr.io/doldecomp/build-melee:main` with everything pre-installed (CodeWarrior, dtk, WiBo, etc.). Our Python runs on the host, shells into the container for compilation and verification:
```
docker exec melee-build ninja build/GALE01/src/melee/ft/ftFox_SpecialLw.c.o
docker exec melee-build objdiff-cli diff ...
```

The melee repo is mounted into the container so file edits from the host are immediately visible to builds. Source editing happens directly on the host filesystem; only compilation/diffing runs in Docker.

**Scope**: Melee only. Hardcode Melee-specific paths, conventions, and module structure. Generalization to other decomp projects is a future concern.

## Implementation Order

### Phase 1: Melee Repo Integration
1. `melee/project.py` — Parse configure.py to get list of all objects + their Matching/NonMatching status
2. `melee/functions.py` — Run `objdiff-cli report generate`, parse report.json to get per-function match data
3. `melee/report.py` — Utilities for reading/querying the report

### Phase 2: Build + Verify Tools
4. `tools/build.py` — Compile a single object file (`ninja <object>`), run objdiff-cli diff, parse results into structured match report
5. `tools/source.py` — Read/write functions in .c files (find function boundaries, replace body)
6. `tools/context.py` — Gather context for a function: run m2ctx.py, read relevant headers, find nearby matched functions

### Phase 3: Decompilation Tools
7. `tools/m2c_tool.py` — Extract target assembly, run m2c, return C output
8. `tools/ghidra.py` — pyhidra headless decompilation (can be deferred if complex to set up)
9. `tools/permuter.py` — Run decomp-permuter on a function, return best result

### Phase 4: Agent Loop
10. `tools/schemas.py` — All Pydantic models for tool params
11. `tools/registry.py` — Registration + OpenAI schema generation
12. `agent/prompts.py` — System prompt teaching the decomp workflow
13. `agent/context_mgmt.py` — Context window management
14. `agent/loop.py` — Core OpenAI Chat Completions tool-calling loop

### Phase 5: Orchestrator + Tracking
15. `models/db.py` — SQLModel definitions (Function, Attempt)
16. `orchestrator/queue.py` — Function queue, prioritization, status management
17. `orchestrator/runner.py` — Run agent on one function, handle lifecycle
18. `orchestrator/batch.py` — Batch mode: work through queue
19. `cli.py` — CLI commands: `run` (single func), `batch` (queue), `status` (progress), `export` (matched code)

### Phase 6: Project Setup
20. `pyproject.toml`, `.gitignore`, `config/default.toml`
21. Docker integration or native setup script

## CLI Interface

```bash
# Setup: point at melee repo, scan functions, populate DB
decomp-agent init --melee-repo /path/to/melee --docker

# Run on a single function
decomp-agent run ftFox_SpecialLw_Enter

# Batch: work through queue of unmatched functions
decomp-agent batch --max-functions 50 --max-attempts-per-func 3

# Check progress
decomp-agent status
# Output:
#   Total functions: 8,432
#   Matched (before AI): 4,470 (53.0%)
#   Matched (by AI):       127 (1.5%)
#   Failed:                 43
#   Remaining:           3,792
#   Tokens used: 12.4M
#   Cost: $XX.XX

# Export matched functions as a patch or PR
decomp-agent export --format patch > ai_matches.patch
decomp-agent export --format pr  # creates a git branch + PR
```

## Verification Plan
1. **Melee parsing**: Clone melee repo, run `python configure.py`, verify our code correctly parses the function list and match statuses
2. **Build tool**: Pick a known Matching function, verify compile_and_check reports 100%. Pick a NonMatching function, verify it reports < 100% with a meaningful diff.
3. **m2c tool**: Pick a small unmatched function, verify m2c produces compilable C output
4. **Agent loop**: Run on a small (< 50 bytes) unmatched function, observe the agent iterate until match or timeout
5. **End-to-end**: `decomp-agent batch --max-functions 5` — watch it pick 5 small functions and attempt to match them
6. **Regression**: After agent matches functions, run `ninja diff` to verify no existing matches were broken

## Key Risks + Mitigations

**Risk**: CodeWarrior compiler quirks make matching extremely difficult for some functions.
**Mitigation**: Start with smallest functions. Track success rate by function size. Use decomp-permuter for the last mile. Accept that some functions may be beyond current AI capability — mark as "failed" and move on.

**Risk**: Context window fills up with large assembly + diffs on complex functions.
**Mitigation**: For large functions (>500 instructions), provide assembly in chunks or summarize. Truncate diff output to focus on the first few mismatches. Cap iterations per function.

**Risk**: Agent edits break previously-matched functions (regressions).
**Mitigation**: The melee CI already detects this. Our compile_and_check tool should verify no regressions in the same object file before accepting a match. Use git stash/reset if a change causes regressions.

**Risk**: Ghidra setup complexity.
**Mitigation**: Ghidra is optional — m2c alone provides a solid starting point. Ghidra can be added later as an enhancement. The agent can also work from raw assembly without any decompiler output.
