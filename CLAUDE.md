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
- `src/decomp_agent/orchestrator/` — Batch execution, isolated workers, and campaign mode
  - `runner.py` — Per-function lifecycle, isolated patch promotion, collateral damage checks
  - `batch.py` — Thread pool executor with budget/cost tracking
  - `headless.py` — Claude Code headless backend (shared-worker and isolated-worker modes)
  - `codex_headless.py` — Codex CLI headless backend
  - `headless_context.py` — Shared prompt/context provider for Claude and Codex headless runs
  - `campaign.py` — Campaign queueing, worker dispatch, cooldowns, supervisor loop
  - `campaign_orchestrator.py` — Campaign orchestrator session runner
  - `worker_launcher.py` — Per-worker worktree/container creation
  - `worktree.py` — Detached git worktree helpers
  - `worker_results.py` — Patch/artifact export and result capture
  - `codex_bootstrap.py` — Worker-local Codex auth/config seeding
- `src/decomp_agent/models/` — Database models (SQLite tracking of attempts, matches, status)
- `src/decomp_agent/web/` — Web UI backend (FastAPI + WebSocket)
- `docker/system-prompt.md` — Shared headless system prompt for Claude and Codex workers
- `docs/` — Documentation (PERMUTER.md, DOCKER.md, MN_MODULE_GUIDE.md, architecture ref)
- `tests/` — Test suite, including campaign/isolation coverage
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
- `/usr/local/bin/dtk` — decomp-toolkit (baked into the worker image)
- `build/tools/wibo` — Windows emulator (runs mwcceppc.exe)
- `build/tools/sjiswrap.exe` — shift-JIS wrapper (runs under wibo)
- `build/compilers/` — Metrowerks CodeWarrior compilers

When `build.ninja` needs regeneration, `build.py` auto-passes `--dtk /usr/local/bin/dtk --compilers build/compilers --sjiswrap build/tools/sjiswrap.exe --wrapper build/tools/wibo` to `configure.py` inside Docker.

If the container's dtk is missing or broken, rebuild the worker image first:
```bash
docker compose -f docker/docker-compose.yml build worker
docker compose -f docker/docker-compose.yml up -d worker
```

If you need to hot-fix a currently running container without rebuilding, provision it manually:
```bash
curl -L https://github.com/encounter/decomp-toolkit/releases/download/v1.8.3/dtk-linux-x86_64 -o /tmp/dtk-linux
chmod +x /tmp/dtk-linux
docker cp /tmp/dtk-linux docker-worker-1:/usr/local/bin/dtk
```

## Agent Pipeline

Three agent backends exist, all sharing the same tool surface and write guardrails:

1. **API agent** (`agent/loop.py`) — Uses OpenAI Responses API with `previous_response_id` for multi-turn. Supports permuter tool.
2. **Claude headless agent** (`orchestrator/headless.py`) — Runs Claude Code in Docker, either in the shared worker container or in an isolated per-task worker.
3. **Codex headless agent** (`orchestrator/codex_headless.py`) — Runs Codex CLI in Docker, typically in isolated per-task workers.

Both backends:
- Prefetch m2c output into the first prompt (`agent/m2c_seed.py`) so the agent always starts from m2c scaffold
- Include warm-start support (inject prior best code + match % for retry attempts)
- Route writes through `registry.py` guardrails before compilation

Claude and Codex headless runs now share the same assignment/system prompt shape through `headless_context.py`.

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

## Current Goal

The current focus is provider-agnostic, overnight-capable file campaigns:

- launch long-running campaigns against one source file
- use Claude or Codex for the orchestrator and workers
- run workers inside isolated containers/worktrees
- preserve partial progress as patches/artifacts
- only promote validated isolated patches back to the main checkout

Practical target-selection guidance:

- prefer files with substantial unmatched work remaining
- avoid files currently being edited upstream
- avoid files already modified locally in the melee checkout
- bias toward one-file campaigns with low merge-conflict risk

The current recommended overnight path is the `campaign` CLI, not ad hoc batch runs.
The standard lifecycle commands are:

```bash
decomp-agent --config config/default.toml campaign launch melee/mn/mnsnap.c --orchestrator-provider claude --worker-provider-policy claude --max-active-workers 1 --timeout-hours 8
decomp-agent --config config/default.toml campaign stop 5
```

`campaign launch` is the normal operator entrypoint. It creates the campaign, launches the orchestrator loop and worker loop in the background, and writes `campaign-processes.json`, `orchestrator.log`, and `worker.log` into the campaign artifact dir. `campaign stop` is the normal shutdown path; it stops those host loops, removes leftover isolated worker containers for the campaign source file, and marks the campaign/tasks stopped in the DB.

## Container Delegation

There are now two distinct container patterns:

1. **Shared worker container**
   - `docker-worker-1`
   - used for MCP server and some shared Claude orchestration flows

2. **Isolated worker containers**
   - one container per Claude/Codex task
   - one detached git worktree per task
   - one private agent home per task (`.claude` or `.codex`)
   - used for safe autonomous execution and same-file parallelism

The worker containers do not have general web access. They only have the connectivity needed for provider inference/auth plus local repo/build/MCP access. Prompts should assume there is no true internet research capability.

**Claude auth:** isolated Claude workers are bootstrapped from `CLAUDE_CODE_OAUTH_TOKEN`, typically via repo-root `.env`.

**Codex auth:** isolated Codex workers seed worker-local state from host `~/.codex/auth.json`, not a shared writable `~/.codex` mount.

**Container Claude/Codex update:** if the worker image tools drift, rebuild the worker image instead of hot-patching long-lived containers:
```bash
docker compose -f docker/docker-compose.yml build worker
docker compose -f docker/docker-compose.yml up -d worker
```

## Source File Contention

Never let multiple agents edit the main melee checkout directly.

Parallel same-file work is only acceptable when:

- each worker uses its own isolated git worktree
- each worker uses its own isolated container/home
- patch promotion back to the main checkout is serialized and validated

That is now the standard pattern for campaign workers. Shared-checkout editing should be treated as legacy/special-case behavior, not the default.

Permuter remains safe because it operates on generated/preprocessed copies rather than the real source tree.

## Key Conventions

- All paths to source files use the "object name" format from configure.py: `"melee/lb/lbcommand.c"`
- Report unit names use: `"main/melee/lb/lbcommand"` (no .c extension, "main/" prefix)
- Build targets: `"build/GALE01/src/melee/lb/lbcommand.o"`
- Config uses Pydantic models; tools accept `Config` objects
- Docker support via `config.docker.enabled` + `run_in_repo()` in `tools/run.py`
- Python 3.10 compatibility required (tomllib fallback to tomli)
