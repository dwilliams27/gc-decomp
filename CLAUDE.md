# Project: gc-decomp (Automated Melee Decompilation Agent)

## Critical Rules

- **Ask the human to install missing tools/software.** When you hit a blocker that requires installing a CLI tool, authenticating a service, setting an env var, or any other setup that the human can do in 30 seconds — stop and ask them immediately. Don't try to work around it, build from source, or find alternative approaches. The fastest path is always: tell the human exactly what to run.

- **NEVER add silent fallbacks or hardcoded workarounds.** If something essential is missing (a tool, a config value, a file), the code must fail hard with a clear error message. Silent fallbacks hide real issues and create a patchwork of bandaids that make debugging impossible. It is always better to crash with a useful error than to silently proceed with partial functionality.

- **Do not hide missing setup behind graceful degradation.** If Ghidra isn't configured, m2c isn't installed, or the build environment isn't ready — raise an error, don't return a stub result and pretend everything is fine. The caller (the agent loop) needs to know what's actually available.

- **ALWAYS use the latest models.** Headless Claude Code agents MUST use `claude-opus-4-6` (the latest Opus). API agents should use `gpt-5.4` (latest GPT) or equivalent top-tier model. Never let a stale default regress us to an older model — this directly impacts match quality. Check model versions when debugging poor agent performance.

- **Run sub-agents in background, not foreground.** When launching multiple research/experiment agents, run them with `run_in_background: true` so the main thread stays responsive. Use a polling loop to check for completed agents, process each result as it finishes (synthesize, decide whether to send the agent back for more), and keep the user informed incrementally. NEVER block the main thread waiting for all agents at once — the user can't send messages while tool calls are pending. The pattern is: launch background agents → return to conversation → poll/process results as they arrive → synthesize when all done.

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

## Checking Match Status

**NEVER use `dtk` CLI directly to check function match percentages.** The dtk CLI has multiple subcommands with different argument formats that change across versions — it's easy to waste time guessing. Instead, use the project's Python tooling which wraps dtk correctly:

```python
from decomp_agent.config import load_config
from decomp_agent.tools.build import check_match

config = load_config()
result = check_match('melee/mn/mngallery.c', config)
for f in result.functions:
    print(f"{f.name}: {f.fuzzy_match_percent}% fuzzy, {f.structural_match_percent}% structural")
```

`check_match()` handles Docker delegation, dtk invocation, and output parsing. It returns a `CompileResult` with per-function `FunctionMatch` objects containing `fuzzy_match_percent`, `structural_match_percent`, and `mismatch_type`.

To compile first then check: use `compile_object()` from the same module.

## Building (IMPORTANT)

**NEVER run `ninja`, `configure.py`, or `dtk` directly on the host.** Always use the Docker container via `run_in_repo()` or `docker exec`. The host has macOS ARM binaries in `build/tools/` which don't work in the Linux x86_64 container, and vice versa.

**CRITICAL: NEVER run `configure.py` on the host to generate `build.ninja` for the container.** The host's Python path gets embedded in `build.ninja` rules, which breaks inside the container. If you need to regenerate `build.ninja`, run `configure.py` **inside the container** with explicit tool paths:
```bash
docker exec docker-worker-1 bash -c "cd /Users/dwilliams/proj/melee-fork/melee && python3 configure.py --map --dtk /usr/local/bin/dtk --compilers build/compilers --wrapper build/tools/wibo --sjiswrap build/tools/sjiswrap.exe"
```
The `--dtk`, `--compilers`, `--wrapper`, and `--sjiswrap` flags prevent download rules from being generated, so the container doesn't need network access. Without these flags, `build.ninja` will contain `download_tool` rules that fail because the container has no internet.

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

## Container Delegation

**Prefer running investigation/experiment work inside the Docker container** by delegating to a Claude Code agent inside `docker-worker-1` (which has `--dangerously-skip-permissions`). The container has NO NETWORK ACCESS — if the delegated agent needs something from the internet, it must stop and report back so the host can fetch it. Anything that needs host-side commands (Ghidra, git, web fetches) should run directly on the host.

Pattern: host agent launches container agent for compile/test/binary-analysis tasks → container agent works autonomously → reports results back.

**Container OAuth refresh:** Claude Code credentials expire periodically. When the container agent fails with `401 authentication_error / OAuth token has expired`:
1. Ask the user to run `claude auth login` on the host (interactive browser flow)
2. Copy fresh credentials into the container:
   ```bash
   security find-generic-password -s "Claude Code-credentials" -a "dwilliams" -w > /tmp/creds.json
   docker cp /tmp/creds.json docker-worker-1:/home/decomp/.claude/.credentials.json
   rm /tmp/creds.json
   ```
3. Re-launch the container agent — credentials are now valid

**Container Claude Code update:** npm can't reach the registry from the container (no network). Update process:
```bash
# On host:
npm pack @anthropic-ai/claude-code@latest
docker cp anthropic-ai-claude-code-*.tgz docker-worker-1:/tmp/
docker exec -u root docker-worker-1 npm install -g /tmp/anthropic-ai-claude-code-*.tgz
```

## Source File Contention

**NEVER have multiple agents modify the same source file simultaneously.** When running parallel experiments (permuter, declaration sweeps, expression tests), each agent must work in isolation:

- **Permuter**: Use the decomp-permuter tool which works on preprocessed copies — it never modifies the real source file.
- **Sub-agents doing source experiments**: Launch with `isolation: "worktree"` to give each agent its own git worktree copy of the repo. This prevents agents from fighting over the same file.
- **Sweep scripts**: Use a backup/restore pattern — copy source to `/tmp/` backup before sweeping, restore after each iteration. But this still blocks other agents from using the file.

If you need to run experiments in parallel, use worktree isolation or the permuter tool — never have two agents directly editing the melee source tree at the same time.

## Key Conventions

- All paths to source files use the "object name" format from configure.py: `"melee/lb/lbcommand.c"`
- Report unit names use: `"main/melee/lb/lbcommand"` (no .c extension, "main/" prefix)
- Build targets: `"build/GALE01/src/melee/lb/lbcommand.o"`
- Config uses Pydantic models; tools accept `Config` objects
- Docker support via `config.docker.enabled` + `run_in_repo()` in `tools/run.py`
- Python 3.10 compatibility required (tomllib fallback to tomli)
