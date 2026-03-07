# Project: gc-decomp (Automated Melee Decompilation Agent)

## Critical Rules

- **Ask the human to install missing tools/software.** When you hit a blocker that requires installing a CLI tool, authenticating a service, setting an env var, or any other setup that the human can do in 30 seconds — stop and ask them immediately. Don't try to work around it, build from source, or find alternative approaches. The fastest path is always: tell the human exactly what to run.

- **NEVER add silent fallbacks or hardcoded workarounds.** If something essential is missing (a tool, a config value, a file), the code must fail hard with a clear error message. Silent fallbacks hide real issues and create a patchwork of bandaids that make debugging impossible. It is always better to crash with a useful error than to silently proceed with partial functionality.

- **Do not hide missing setup behind graceful degradation.** If Ghidra isn't configured, m2c isn't installed, or the build environment isn't ready — raise an error, don't return a stub result and pretend everything is fine. The caller (the agent loop) needs to know what's actually available.

- **ALWAYS use the latest models.** Headless Claude Code agents MUST use `claude-opus-4-6` (the latest Opus). API agents should use `gpt-5.4` (latest GPT) or equivalent top-tier model. Never let a stale default regress us to an older model — this directly impacts match quality. Check model versions when debugging poor agent performance.

## Project Structure

- `src/decomp_agent/` — Main package
- `src/decomp_agent/agent/` — Agent loop, system prompts, m2c seed injection
  - `loop.py` — OpenAI Responses API agent loop (tool dispatch, token budget, warm-start)
  - `prompts.py` — System prompt template (CodeWarrior reference, quality rules, banned techniques)
  - `m2c_seed.py` — Prefetches m2c output into the first prompt for both API and headless agents
  - `context_mgmt.py` — Context window management
- `src/decomp_agent/tools/` — Tool implementations (build, source, context, m2c, ghidra, permuter)
  - `registry.py` — Tool dispatch + write guardrails (inline asm, placeholder, field access, C89, var names, match comments)
- `src/decomp_agent/melee/` — Melee repo integration (project parsing, report, functions)
- `src/decomp_agent/orchestrator/` — Batch execution and headless mode
  - `runner.py` — Per-function lifecycle (lock, backup, run agent, collateral damage check, auto-commit)
  - `batch.py` — Thread pool executor with budget/cost tracking
  - `headless.py` — Claude Code Docker-based agent backend (uses docker/system-prompt.md)
- `src/decomp_agent/models/` — Database models (SQLite tracking of attempts, matches, status)
- `src/decomp_agent/web/` — Web UI backend (FastAPI + WebSocket)
- `docker/system-prompt.md` — System prompt for headless Docker agent (must stay in sync with prompts.py)
- `docs/` — Documentation (PERMUTER.md, DOCKER.md, MN_MODULE_GUIDE.md, architecture ref)
- `tests/` — Test suite (test_phase1-5.py, test_cost.py, test_disasm.py, test_ctx_filter.py, test_e2e.py)
- `config/default.toml` — Default configuration
- Melee repo (fork): `/Users/dwilliams/proj/melee-fork/melee`

## Running the Dev Environment

Two processes needed — backend API server and frontend Vite dev server:

```bash
# Terminal 1: Backend (FastAPI + Uvicorn on port 8000)
cd /Users/dwilliams/proj/gc-decomp
decomp-agent serve --port 8000

# Terminal 2: Frontend (Vite dev server, proxies /api + /ws to port 8000)
cd /Users/dwilliams/proj/gc-decomp/web-ui
npm run dev
```

The frontend Vite config (`web-ui/vite.config.ts`) proxies `/api/*` and `/ws/*` to `http://127.0.0.1:8000`. Open the URL Vite prints (usually http://localhost:5173/).

To build the frontend for production: `cd web-ui && npm run build` — output goes to `web-ui/dist/` which the backend auto-serves.

Web dependencies: `pip install 'decomp-agent[web]'` (FastAPI, Uvicorn, websockets).

## Building (IMPORTANT)

**NEVER run `ninja`, `configure.py`, or `dtk` directly on the host.** Always use the Docker container via `run_in_repo()` or `docker exec`. The host has macOS ARM binaries in `build/tools/` which don't work in the Linux x86_64 container, and vice versa.

The Docker container (`docker-worker-1`) has Linux build tools at:
- `/usr/local/bin/dtk` — decomp-toolkit (must be provisioned separately from host dtk)
- `build/tools/wibo` — Windows emulator (runs mwcceppc.exe)
- `build/tools/sjiswrap.exe` — shift-JIS wrapper (runs under wibo)
- `build/compilers/` — Metrowerks CodeWarrior compilers

When `build.ninja` needs regeneration, `build.py` auto-passes `--dtk /usr/local/bin/dtk --compilers build/compilers --sjiswrap build/tools/sjiswrap.exe --wrapper build/tools/wibo` to `configure.py` inside Docker.

If the container's dtk is missing or broken, provision it:
```bash
curl -L https://github.com/encounter/decomp-toolkit/releases/download/v1.8.3/dtk-linux-x86_64 -o /tmp/dtk-linux
chmod +x /tmp/dtk-linux
docker cp /tmp/dtk-linux docker-worker-1:/usr/local/bin/dtk
```

## Agent Pipeline

Two agent backends exist, both using the same tools and guardrails:

1. **API agent** (`agent/loop.py`) — Uses OpenAI Responses API with `previous_response_id` for multi-turn. Supports permuter tool.
2. **Headless agent** (`orchestrator/headless.py`) — Runs Claude Code in Docker via MCP server. No permuter yet.

Both backends:
- Prefetch m2c output into the first prompt (`agent/m2c_seed.py`) so the agent always starts from m2c scaffold
- Include warm-start support (inject prior best code + match % for retry attempts)
- Route writes through `registry.py` guardrails before compilation

**Write guardrails** (hard rejects in `registry.py`):
- Multi-instruction inline asm blocks
- `NOT_IMPLEMENTED` placeholders
- Raw pointer arithmetic `(u8*)ptr + 0xNN` (must use struct fields or M2C_FIELD)
- C99 for-loop declarations
- m2c artifact variable names (`var_r31`, `var1`)
- Match percentage comments (`// 95% match`)

**Prompt parity**: `prompts.py` (API agent) and `docker/system-prompt.md` (headless agent) must stay in sync. Both include CodeWarrior reference, quality rules, and banned techniques.

## Testing Standard

- **Always validate new features by running them the way the agent would.** Don't just run unit tests — call the actual tool functions through `registry.dispatch()` (or the underlying function directly) with real data and verify the output is what you'd want the LLM to see. Unit tests with synthetic fixtures are not sufficient; real data from the melee build catches issues that synthetic data misses.

## Current Goal: mn/ Module Ownership

We are taking the `mn/` (Menus) module from partial to fully matched, file by file. The goal is whole-file completion — every function in a file at 100% — so we can submit clean PRs ("Complete mn/mngallery.c" etc). See `docs/UNTOUCHED_FILES_PLAN.md` for the full strategy.

**Why mn/:** Untouched files with no active contributors = no merge conflict risk. Same module as already-matched files (mndeflicker, mnhyaku, mnlanguage) we can study for patterns.

**Current targets (in priority order):**
1. `melee/mn/mngallery.c` — 4/11 matched, 7 remaining. **Finish this first.**
2. `melee/mn/mnstagesel.c` — 1 function at 99.2%. Near-done.
3. `melee/mn/mnmain.c` — 6 functions all at ~98%. Near-done.
4. `melee/mn/mnsound.c` — 3 functions at ~93%. Close.
5. `melee/mn/mnsnap.c` — 11 functions at ~93%. Close.
6. `melee/mn/mnevent.c` — 14 functions, untouched (0%). Original plan target.
7. `melee/mn/mnitemsw.c` — 10 functions, untouched (0.6%). Original plan target.

**After closing near-done files**, push into untouched targets. Each completed file = a PR.

**Do NOT scatter runs across random libraries.** Stay focused on mn/ until the module is done.

## Key Conventions

- All paths to source files use the "object name" format from configure.py: `"melee/lb/lbcommand.c"`
- Report unit names use: `"main/melee/lb/lbcommand"` (no .c extension, "main/" prefix)
- Build targets: `"build/GALE01/src/melee/lb/lbcommand.o"`
- Config uses Pydantic models; tools accept `Config` objects
- Docker support via `config.docker.enabled` + `run_in_repo()` in `tools/run.py`
- Python 3.10 compatibility required (tomllib fallback to tomli)
