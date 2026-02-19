# Project: gc-decomp (Automated Melee Decompilation Agent)

## Critical Rules

- **NEVER add silent fallbacks or hardcoded workarounds.** If something essential is missing (a tool, a config value, a file), the code must fail hard with a clear error message. Silent fallbacks hide real issues and create a patchwork of bandaids that make debugging impossible. It is always better to crash with a useful error than to silently proceed with partial functionality.

- **Do not hide missing setup behind graceful degradation.** If Ghidra isn't configured, m2c isn't installed, or the build environment isn't ready — raise an error, don't return a stub result and pretend everything is fine. The caller (the agent loop) needs to know what's actually available.

## Project Structure

- `src/decomp_agent/` — Main package
- `src/decomp_agent/tools/` — Tool implementations (build, source, context, m2c, ghidra, permuter)
- `src/decomp_agent/melee/` — Melee repo integration (project parsing, report, functions)
- `tests/` — Test suite (test_phase1.py, test_phase2.py, test_phase3.py)
- `config/default.toml` — Default configuration
- Melee repo (fork): `/Users/dwilliams/proj/melee-fork/melee`

## Testing Standard

- **Always validate new features by running them the way the agent would.** Don't just run unit tests — call the actual tool functions through `registry.dispatch()` (or the underlying function directly) with real data and verify the output is what you'd want the LLM to see. Unit tests with synthetic fixtures are not sufficient; real data from the melee build catches issues that synthetic data misses.

## Key Conventions

- All paths to source files use the "object name" format from configure.py: `"melee/lb/lbcommand.c"`
- Report unit names use: `"main/melee/lb/lbcommand"` (no .c extension, "main/" prefix)
- Build targets: `"build/GALE01/src/melee/lb/lbcommand.o"`
- Config uses Pydantic models; tools accept `Config` objects
- Docker support via `config.docker.enabled` + `run_in_repo()` in `tools/run.py`
- Python 3.10 compatibility required (tomllib fallback to tomli)
