# gc-decomp Architecture Reference

Overview of the codebase for implementing new features.

---

## Core Pipeline

```
LLM tool call (JSON)
  ↓ registry.dispatch()
  ├─ Validate JSON → Parse into Pydantic schema
  ├─ Normalize paths (strip src/, etc.)
  ├─ Call handler function
  └─ Return result string for LLM

write_function request
  ↓ _handle_write_function()
  ├─ [Validators: inline_asm, placeholder, field_access, c89_decl]
  ├─ read_source_file → replace_function → write_source_file
  ├─ check_match (compile & verify)
  ├─ failure → restore from backup
  └─ return match results
```

## Key Files & Extension Points

### Validation Pipeline — `src/decomp_agent/tools/registry.py`

Current write guardrails (regex-based validators):
- `_check_inline_asm()` — Rejects multi-instruction `asm {}` blocks
- `_check_placeholder_stubs()` — Rejects `NOT_IMPLEMENTED` placeholders
- `_check_field_access_style()` — Rejects raw `(u8*)ptr + 0xNN` pointer arithmetic
- `_check_c89_declarations()` — Rejects C99-style loop declarations

**To add a new validator:**
1. Write `_check_xxx(code: str) -> str | None` function
2. Add call in `_handle_write_function()` before line 362
3. Return error message string on failure, None on pass

### System Prompts — `src/decomp_agent/agent/prompts.py`

- `SYSTEM_PROMPT` — Template with `{ghidra_orient}` and `{ghidra_tool}` placeholders
- `build_system_prompt()` — Assembles final prompt + function assignment
- Add new sections as static text or `{placeholder}` for conditional content

### m2c Integration — `src/decomp_agent/tools/m2c_tool.py`

Current flags: `--knr --pointer left --target ppc-mwcc-c --context --function`

**To add new m2c flags:** Modify `run_m2c()` around lines 245-256.

### Melee Repo Integration — `src/decomp_agent/melee/`

| File | Purpose |
|---|---|
| `project.py` | Parse `configure.py` → `ObjectEntry`, `ObjectStatus` |
| `report.py` | Parse `objdiff report.json` → `FunctionReport`, `UnitReport` |
| `functions.py` | Unified view → `FunctionInfo`, `get_candidates()` |

### Agent Loop — `src/decomp_agent/agent/loop.py`

```
run_agent(function_name, source_file, config)
  ├─ build_system_prompt() + build_registry()
  ├─ OpenAI API with previous_response_id for multi-turn
  └─ Loop: call API → dispatch tools → check match → repeat
```

Features: token budget tracking, warm-start support, auto-nudge, m2c seed injection.

### Runner — `src/decomp_agent/runner.py`

Per-function lifecycle: lock file → save backup → run agent → check collateral damage → revert if unmatched → auto-commit if matched.

### Batch — `src/decomp_agent/batch.py`

Thread pool with budget/cost tracking, per-file locking, warm-start boost.

## Tools Directory

| Tool | Lines | Purpose |
|---|---|---|
| `registry.py` | 492 | Dispatch & validation (guardrails) |
| `disasm.py` | 666 | DTK disassembly + diff analysis |
| `permuter.py` | 635 | Auto-search code permutations |
| `ghidra.py` | 479 | Ghidra decompilation (optional) |
| `ctx_filter.py` | 322 | Smart context/header filtering |
| `source.py` | 290 | Source file manipulation |
| `m2c_tool.py` | 292 | m2c decompiler runner |
| `build.py` | 134 | Ninja compilation |
| `context.py` | 212 | Gather function context |
| `schemas.py` | 117 | Pydantic tool parameter schemas |

## Config Structure

```
Config
  ├─ MeleeConfig: repo_path, version, build_dir
  ├─ AgentConfig: model, max_iterations, max_tokens_per_attempt
  ├─ DockerConfig: enabled, container_name, image
  ├─ GhidraConfig: enabled, install_dir, project_path
  ├─ ClaudeCodeConfig: enabled, timeout, max_turns
  ├─ OrchestrationConfig: db_path, max_function_size, batch_size, workers, budget
  └─ PricingConfig: cost calculation
```

## Path Conventions

| Element | Format | Example |
|---|---|---|
| Source file | Object name | `melee/lb/lbcommand.c` |
| Report unit | Prefixed path | `main/melee/lb/lbcommand` |
| Build target | Ninja path | `build/GALE01/src/melee/lb/lbcommand.o` |

## What Doesn't Exist Yet

- **Area planner** — No strategic module for planning multi-function file coverage. Would go in `src/decomp_agent/melee/area_planner.py` or `src/decomp_agent/orchestrator/planner.py`.
- **Quality checker** — Only 4 regex validators exist. No semantic checks for enums, macros, naming, struct copies.
- **m2c flag inference** — No logic to auto-determine flags like `--union-field` or `--stack-structs`.
