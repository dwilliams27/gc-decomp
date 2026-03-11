# Codex Spike Findings (2026-03-10)

## Scope

This spike was run locally against the installed `codex` CLI to answer four immediate implementation questions:

1. What does `codex exec --json` look like?
2. Does Codex require outbound network access?
3. What is the minimum worker auth bootstrap?
4. How should MCP be configured for workers?

These findings are meant to guide implementation of the Codex container worker path.

---

## Findings

## 1. `codex exec` is the correct non-interactive entrypoint

The CLI supports:

- `codex exec`
- `--json`
- `--ephemeral`
- `--dangerously-bypass-approvals-and-sandbox`
- `-s/--sandbox`
- `-C/--cd`

Notably, `codex exec` does **not** accept a top-level `-a/--ask-for-approval` flag. That flag exists on the interactive CLI entrypoint, not on `exec`.

Implication:

- the Codex headless runner should use `codex exec`
- approval behavior should be controlled by either:
  - `--dangerously-bypass-approvals-and-sandbox`, or
  - the selected sandbox mode

For container workers, the intended mode remains:

```bash
codex exec \
  --json \
  --ephemeral \
  --dangerously-bypass-approvals-and-sandbox \
  -C <worker-repo-path> \
  "<prompt>"
```

---

## 2. `--json` emits JSONL event records

Observed event types during failed runs included:

- `thread.started`
- `turn.started`
- `error`
- `item.completed`
- `turn.failed`

Observed examples:

```json
{"type":"thread.started","thread_id":"..."}
{"type":"turn.started"}
{"type":"error","message":"Reconnecting... 2/5 (...)"}
{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Falling back from WebSockets to HTTPS transport. ..."}}
{"type":"turn.failed","error":{"message":"stream disconnected before completion: ..."}}
```

Implication:

- the runner should parse a JSONL event stream, not a single final JSON blob
- result extraction should be event-driven
- failure handling should map `turn.failed` and repeated `error` events into structured termination reasons

---

## 3. Codex requires outbound network access for inference

Observed endpoints during local execution:

- `wss://chatgpt.com/backend-api/codex/responses`
- `https://chatgpt.com/backend-api/codex/responses`
- `https://chatgpt.com/backend-api/codex/models?client_version=0.113.0`

When run in the current restricted environment, the CLI failed with DNS/network errors and retried repeatedly before emitting `turn.failed`.

Implication:

- Codex workers cannot use the same "no outbound network" assumption as the current Claude container flow
- worker containers will need controlled outbound access to ChatGPT/OpenAI endpoints
- the current proxy allowlist is insufficient

Current allowlist in [docker/squid.conf](/Users/dwilliams/proj/gc-decomp/docker/squid.conf) only includes:

- `.anthropic.com`
- `.claude.ai`

Minimum next step:

- extend the worker network policy to allow the required Codex domains, or
- build a Codex-specific proxy profile

---

## 4. `auth.json` alone is enough for basic worker bootstrap

Using an alternate `HOME` with only:

- `~/.codex/auth.json`

was enough for:

- `codex login status`
- `codex exec` startup

The CLI reported:

- `Logged in using ChatGPT`

even when the temp home contained only `auth.json`.

Implication:

- workers do not need a full shared copy of host `~/.codex`
- the initial bootstrap can likely be:
  - copy `auth.json`
  - generate a worker-local `config.toml`

This is good news for isolation.

---

## 5. Worker-private state is still required

Even with `--ephemeral`, Codex attempted to use worker-local state during execution.

Observed behavior:

- startup succeeded with only `auth.json`
- a shell snapshot file briefly appeared in the temp home during execution
- after failed completion, the temp home returned to containing only `auth.json`

By contrast, the normal host `~/.codex` contains mutable shared state such as:

- `history.jsonl`
- `logs_*.sqlite`
- `state_*.sqlite`
- `models_cache.json`
- `shell_snapshots/*`

Implication:

- do not mount host `~/.codex` RW into all workers
- each worker still needs a private `.codex` directory
- `auth.json` should be treated as the shared seed, not the shared home

---

## 6. MCP can be configured dynamically by the worker

`codex mcp add` works and writes TOML config under the worker home.

A test command:

```bash
HOME=/tmp/codex-mcp codex mcp add decomp-tools \
  --env DECOMP_CONFIG=/app/config/container.toml \
  -- python -m decomp_agent.mcp_server
```

produced the following config:

```toml
[mcp_servers.decomp-tools]
command = "python"
args = ["-m", "decomp_agent.mcp_server"]

[mcp_servers.decomp-tools.env]
DECOMP_CONFIG = "/app/config/container.toml"
```

Implication:

- workers can be bootstrapped either by:
  - pre-writing `config.toml`, or
  - running `codex mcp add` at startup
- we do **not** need to rely on a pre-existing global host Codex config

Recommended direction:

- generate a worker-local `config.toml` explicitly during startup
- avoid mutating shared host Codex config

---

## 7. There is some startup noise unrelated to the core contract

Observed during local runs:

- Rust panic messages related to `system-configuration`
- OTEL exporter initialization panic/noise

Despite that noise, the CLI still proceeded into normal run startup and emitted JSON events.

Implication:

- these messages are worth cleaning up later if possible
- they do not currently look like blockers for the container-worker design

---

## What This Means For Implementation

## Decisions now justified by evidence

These design decisions now have direct local validation behind them:

1. Use `codex exec`, not the OpenAI API path.
2. Parse JSONL events rather than expecting one final JSON result.
3. Give each worker a private `.codex` home.
4. Seed workers from `auth.json`, not from a shared full `~/.codex`.
5. Configure MCP in the worker-local config.
6. Plan for controlled outbound network access to ChatGPT/OpenAI endpoints.

## Remaining unknowns

There are still a few things the spike did not settle:

- the exact final successful event sequence for a completed turn
- token/accounting fields available on successful runs
- the full set of domains Codex may need in a containerized environment
- whether there is a cleaner way to disable the OTEL/startup noise

These can be addressed during the first single-worker implementation pass.

---

## Recommended Next Implementation Step

Start building the single-worker Codex path with the following assumptions:

- host creates an isolated worker checkout
- host creates a worker-private `.codex`
- worker bootstrap copies `auth.json`
- worker bootstrap writes a local Codex `config.toml` with the decomp MCP server
- worker runs `codex exec --json --ephemeral --dangerously-bypass-approvals-and-sandbox`
- worker container has controlled outbound access to required Codex domains

Once that path is working for one worker, parallel worker orchestration can be added on top.
