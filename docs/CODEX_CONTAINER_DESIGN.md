# Codex Container Worker Design

## Goal

Add a Codex-based execution mode that uses the ChatGPT/Codex CLI subscription path instead of the OpenAI API, while preserving the current decomp workflow and enabling many isolated workers to run in parallel.

The target outcome is:

- Codex runs non-interactively inside containers
- each worker is fully sandboxed by the container boundary
- workers can run without approval prompts inside that sandbox
- workers do not share mutable repo state, home directories, or scratch state
- the host orchestrator can launch many workers in parallel without them stepping on each other

This document is a design and rollout plan only. It does not change runtime behavior yet.

---

## Current State

The repo currently has two execution models:

### 1. OpenAI/API path

`run_agent()` in [loop.py](/Users/dwilliams/proj/gc-decomp/src/decomp_agent/agent/loop.py) uses the OpenAI Python client directly. This is not a CLI/subscription flow. It is host-side orchestration plus host-side model access.

Properties:

- Python agent runs on the host
- model calls go through the OpenAI API
- tools run partly on the host, partly in Docker depending on config
- not suitable for the "use subscription compute via coding tool" goal

### 2. Claude headless path

`run_headless()` in [headless.py](/Users/dwilliams/proj/gc-decomp/src/decomp_agent/orchestrator/headless.py) launches `claude` inside a Docker container via `docker exec`.

Properties:

- host process still orchestrates everything
- model CLI runs inside a container
- MCP tools are exposed by this repo and run inside the container
- Claude is invoked with `--dangerously-skip-permissions`
- current design assumes one long-lived worker container name

### 3. Tooling layer

The MCP tool layer in [mcp_server.py](/Users/dwilliams/proj/gc-decomp/src/decomp_agent/mcp_server.py) is generic and reusable. It already exposes the core decomp workflow:

- get assembly
- get context
- read/write source
- compile/check
- get diff
- mark complete

This is the main reason the Codex path should reuse MCP rather than invent a new tool surface.

### 4. Docker model today

The current compose file in [docker/docker-compose.yml](/Users/dwilliams/proj/gc-decomp/docker/docker-compose.yml) defines:

- one `worker` service
- one shared melee repo bind mount
- one shared auth mount for `~/.claude`
- one fixed worker container identity

This is enough for one delegated agent, but it is not an isolation model for many independent workers.

---

## Problems With The Current Setup

The current Claude container pattern is a useful prototype, but it does not satisfy the Codex parallel-worker goal.

### Problem 1: Shared repo state

Workers effectively operate on the same repo tree. The code already contains per-file locks in [runner.py](/Users/dwilliams/proj/gc-decomp/src/decomp_agent/orchestrator/runner.py) to stop same-file corruption, which is evidence that workers are not truly isolated today.

Implications:

- same-file runs serialize instead of parallelizing
- rollback logic can clobber another worker's changes without locking
- header edits and file-wide cleanup can interfere across workers

### Problem 2: Single-container assumption

[run_in_repo() in tools/run.py](/Users/dwilliams/proj/gc-decomp/src/decomp_agent/tools/run.py) assumes one configured container name when Docker is enabled.

Implications:

- compile/check is bound to one container identity
- there is no concept of per-worker container routing
- scaling means more contention, not more isolation

### Problem 3: Shared mutable CLI home state

Claude currently mounts a shared host auth directory into the worker container. That was acceptable for one worker, but Codex uses `~/.codex`, which contains much more than credentials:

- `auth.json`
- config
- history/session files
- sqlite state
- logs
- caches

Mounting one host `~/.codex` RW into many workers would create a concurrency and corruption risk.

### Problem 4: Host/container split leaks responsibility

Today the host still owns large parts of the run lifecycle while the container owns the model process. That split is manageable for a single delegated worker, but it creates ambiguity for scaled workers:

- where repo mutations happen
- where compile/diff happens
- where credentials live
- where run artifacts are produced
- where failures should be recovered

### Problem 5: Worker-side commit behavior does not fit isolation

`run_function()` auto-commits matched functions in the melee repo. That only makes sense when the worker is acting directly on the primary repo checkout. It is the wrong abstraction for isolated worktrees or disposable containers.

### Problem 6: Shared DB coupling

The orchestration DB in [db.py](/Users/dwilliams/proj/gc-decomp/src/decomp_agent/models/db.py) is SQLite and is currently host-oriented. Letting many containers share it directly would add unnecessary coupling.

The host should own orchestration state. Workers should return structured results.

### Problem 7: Current concurrency model is thread parallelism, not worker isolation

Batch execution in [batch.py](/Users/dwilliams/proj/gc-decomp/src/decomp_agent/orchestrator/batch.py) uses a thread pool within one host process. That is fine for scheduling, but not sufficient as the isolation boundary.

The real unit of isolation needs to be:

- one container
- one repo worktree
- one private home directory
- one task/run context

---

## Requirements

### Functional requirements

- Run Codex via CLI, not OpenAI API
- use the existing decomp workflow and tools
- support single-function mode and file mode
- support parallel execution across many workers
- allow workers to run without approval prompts inside their sandbox
- preserve logs, results, and failure information in host orchestration

### Isolation requirements

- no shared writable repo tree across workers
- no shared writable `~/.codex` across workers
- no shared temp directories
- no shared container identity
- no concurrent direct writes by workers to the primary melee checkout

### Operational requirements

- worker startup should be reproducible
- auth bootstrap should be explicit and support refresh
- failures should leave the host repo clean
- the system should work with the existing Docker-based MWCC environment
- rollout should start with one Codex worker before parallel fleet support

### Non-goals for the first implementation

- replacing the existing OpenAI/API path
- replacing the current Claude path
- merging or committing from inside workers
- solving internet access beyond what Codex auth/session startup requires
- adding distributed orchestration across multiple machines

---

## Design Principles

### 1. Reuse the MCP tool layer

The existing MCP server already captures the decomp workflow correctly. Codex should use the same tool interface so we preserve:

- `write_function` rollback semantics
- compile/check behavior
- diff tooling
- context gathering

This avoids duplicating domain logic in shell prompts.

### 2. Treat the container as the true trust boundary

Inside the worker container, Codex should run with no approval friction. The container is the sandbox. Human approval prompts inside it are counterproductive.

### 3. Isolate with disposable per-run worktrees

The correct unit of isolation is a disposable worker repo copy based on a git worktree or equivalent isolated checkout. This allows:

- parallel source edits
- file-local experimentation
- header changes without affecting sibling workers
- safe disposal on failure

### 4. Keep orchestration on the host

The host scheduler should:

- select candidates
- prepare worker inputs
- create worktrees
- launch containers
- collect results
- decide what to merge/apply

Workers should do decomp work, not global orchestration.

### 5. Return patches/results, not direct commits

Workers should emit structured outputs:

- final source diff or patch
- match results
- session/run metadata
- logs/artifacts

The host can then validate and apply accepted changes into the primary repo.

---

## Proposed Architecture

## Overview

Add a new headless provider path for Codex:

1. Host batch scheduler selects a task
2. Host creates an isolated worker repo worktree
3. Host creates a private worker state directory
4. Host launches a dedicated worker container for that task
5. Worker container starts Codex CLI non-interactively
6. Codex talks to this repo's MCP server inside the container
7. Worker performs all edits/builds/diffs inside its isolated checkout
8. Worker exits and emits a structured result
9. Host ingests the result and optionally applies the patch to the main repo

## Component model

### A. Host scheduler

The host scheduler remains the control plane.

Responsibilities:

- query candidates from DB
- avoid scheduling conflicting targets
- create one worker spec per run
- prepare worktree + state dirs
- launch and monitor worker containers
- collect result payloads
- update DB after the worker finishes

### B. Worker worktree

Each worker gets its own isolated melee checkout root.

Properties:

- created from the main melee repo
- writable only by that worker
- destroyed after completion unless kept for debugging
- contains the exact source/build artifacts that worker owns

This replaces the current model where all workers touch the same bind mount.

### C. Worker container

Each run gets one dedicated container.

Properties:

- unique container name or autogenerated compose project/service instance
- mounted to exactly one worker worktree
- mounted to exactly one worker home/state directory
- runs the MWCC/decomp toolchain plus Codex CLI plus MCP server
- no approval prompts inside the worker

### D. Worker-local Codex home

Each worker gets a private `.codex` root.

Properties:

- seeded from a minimal host-auth bootstrap
- contains no shared writable state with sibling workers
- can be discarded after completion

Recommended split:

- bootstrap credentials/config from host into worker-local `.codex`
- keep history, logs, sqlite state, and sessions private per worker

### E. MCP tool server

Reuse the existing [mcp_server.py](/Users/dwilliams/proj/gc-decomp/src/decomp_agent/mcp_server.py) inside the worker container.

Properties:

- same decomp tools as Claude path
- config points to the worker-local repo path
- compile/build/diff happens natively inside the worker container

### F. Result handoff

Each worker should produce a result bundle such as:

- `result.json`
- `patch.diff`
- `stdout.log`
- `stderr.log`
- optional copied source snapshots for debugging

The host ingests these artifacts after the container exits.

---

## Codex Execution Model

## Why Codex CLI instead of API

The user goal is specifically to use included subscription compute through the coding tool rather than OpenAI API billing. The correct path is therefore the `codex` CLI.

## Expected invocation shape

The Codex worker should use `codex exec`, not the interactive TUI.

Desired properties of the invocation:

- non-interactive
- machine-parseable output
- no approval prompts
- no internal sandboxing beyond the external container boundary
- working directory set to the worker checkout

Target style:

```bash
codex exec \
  --cd /work/melee \
  --json \
  --dangerously-bypass-approvals-and-sandbox \
  "<prompt>"
```

Exact flags may need small adjustment during implementation, but the design assumption is:

- container provides the safety boundary
- Codex should not pause for approvals inside that boundary
- `exec` output should be parsed into `AgentResult`

## MCP integration

Codex should use the same MCP tool server pattern as Claude.

We should prefer MCP over telling Codex to use only raw shell/file tools because:

- the decomp workflow already exists
- rollback behavior already exists in `write_function`
- compile/match output formats are already understood by the orchestrator
- the system prompt can stay focused on decomp rather than tool plumbing

## Prompt strategy

The Claude system prompt in [docker/system-prompt.md](/Users/dwilliams/proj/gc-decomp/docker/system-prompt.md) should be treated as the starting point, not the final Codex prompt.

The Codex prompt should:

- preserve decomp-specific instructions
- preserve the MCP tool-first workflow
- acknowledge Codex built-in shell/edit/search abilities
- explicitly state that the container is the sandbox
- explicitly forbid unnecessary changes outside the intended scope

We should expect some provider-specific tuning after first runs.

---

## Isolation Model

## One worker == one container == one worktree == one home

This is the central design rule.

Every worker gets:

- one isolated source tree
- one isolated build/output state
- one isolated `.codex`
- one isolated container process

Anything less will eventually create cross-worker interference.

## Source isolation

Preferred mechanism: disposable git worktrees rooted from the melee repo.

Benefits:

- cheap creation
- real git semantics
- easy diff generation
- easy cleanup
- makes the current "never have multiple agents modify the same source file simultaneously" rule enforceable by architecture instead of only by policy

## Home/state isolation

Do not share host `~/.codex` RW with all workers.

Instead:

- create `/worker-state/<worker-id>/.codex`
- copy or materialize only the required auth/config seed
- let Codex manage its own per-worker sessions/history/logs

## DB isolation

The worker should not be the source of truth for orchestration DB state.

Preferred model:

- host marks task as running
- worker returns result payload
- host records attempt/run data into SQLite

This avoids many-container SQLite access and keeps run accounting centralized.

## Commit isolation

Workers should not commit to the main melee repo.

Preferred model:

- worker writes patch against its private worktree
- host validates patch against the main checkout
- host applies/commits only after success

---

## Docker Layout Changes

## Current limitations

The current compose file is built around one long-lived `worker` service. That is useful for manual delegation, but the Codex design needs worker instancing.

## Proposed direction

Split the Docker story into two layers:

### 1. Base image

A reusable image containing:

- MWCC/melee build environment
- Python + decomp-agent
- Codex CLI
- MCP configs and prompts

This can likely extend the current Dockerfile.

### 2. Worker launcher

A host-side launcher that starts one container per run with:

- unique worker name
- worker-local bind mounts
- worker-local env/config
- result output mount

This is a better fit than treating the worker as a single static compose service.

### Suggested mounts per worker

- `/work/melee` or same-path mounted worker worktree
- `/home/decomp/.codex` as worker-private state
- `/worker-output` for result artifacts

### Network model

Keep workers sandboxed by default.

Practical stance:

- build/diff/decomp work should not need general internet
- Codex auth may require bootstrap from host-side login state
- if Codex runtime needs network for inference, traffic should be explicit and minimal

This needs a spike during implementation because Codex CLI service connectivity is product-specific, unlike the current no-network Claude setup.

---

## Orchestrator Changes

## New provider mode

Add a new headless provider for Codex rather than folding this into the OpenAI/API path.

Possible direction:

- keep `run_agent()` for API mode
- keep `run_headless()` for Claude
- add `run_codex_headless()`

Longer term, these could be unified under a provider interface, but that refactor is not required for the first pass.

## Worker spec

Introduce a worker-spec abstraction describing:

- worker id
- target function or source file
- worker repo path
- worker home path
- container name
- result paths
- timeout
- provider

The host batch scheduler should submit worker specs rather than directly calling a single shared runner.

## Scheduling policy

Initial scheduling policy should remain conservative:

- at most one active worker per source file
- prefer file-level uniqueness
- no same-file parallel runs

This matches both the repo guidance and the practical reality of decomp work.

## Result ingestion

After worker completion, host should:

- read `result.json`
- read optional patch/log artifacts
- update DB state
- optionally apply patch to main checkout in a later explicit step

---

## Auth Strategy

## Current host state

The host already has Codex CLI installed and logged in via ChatGPT.

That is promising, but not enough to justify sharing the full host `.codex` directory across workers.

## Recommended approach

Use a two-tier model:

### Tier 1: host authority

The host remains the place where interactive login/refresh occurs.

### Tier 2: worker bootstrap

Each worker receives the minimum material needed to authenticate.

Candidates:

- copied auth file
- minimal config file
- possibly a small bootstrap directory template

This part needs validation during implementation because Codex CLI may expect more than one file for a healthy non-interactive session.

## Important constraint

Do not assume worker credential refresh should happen independently in every container. Keep refresh centralized on the host, then reseed workers.

---

## Rollout Plan

## Phase 0: design and validation

- write this design doc
- confirm Codex CLI behavior needed for `exec`
- confirm how worker auth bootstrap must work
- confirm whether Codex runtime requires network connectivity from workers

## Phase 1: single isolated Codex worker

Goal: prove one function/file can run end-to-end inside one isolated container.

Work:

- add Codex-capable worker image
- add `run_codex_headless()`
- run one worker with one isolated repo copy
- reuse MCP tooling
- parse and persist result on host

Success criteria:

- no OpenAI API usage
- Codex executes via CLI
- worker edits only its isolated checkout
- host receives structured result

## Phase 2: worktree-based isolation

Goal: remove dependence on shared writable repo trees.

Work:

- add host-side worktree creation/cleanup
- point worker config at per-worker worktree
- switch result handoff to patch/artifact output

Success criteria:

- no worker writes directly to main checkout
- reruns are reproducible
- failed workers leave main repo untouched

## Phase 3: parallel worker pool

Goal: run many workers concurrently without stepping on each other.

Work:

- add worker launcher and scheduler integration
- enforce one-worker-per-source-file
- add worker-private `.codex` dirs
- add cleanup/retention policy

Success criteria:

- multiple workers run concurrently
- no file-lock serialization due to shared trees
- no cross-worker home/state corruption

## Phase 4: patch application pipeline

Goal: safely bring accepted worker output back to the main repo.

Work:

- host-side patch validation
- optional recompile/verify in main checkout
- explicit apply/commit flow

Success criteria:

- worker success does not automatically mutate the main repo
- accepted results are applied deterministically

---

## Implementation Work Breakdown

The exact filenames may shift, but this is the expected implementation surface.

### Config

Likely changes in [config.py](/Users/dwilliams/proj/gc-decomp/src/decomp_agent/config.py):

- add Codex headless config block, or
- generalize `claude_code` into a provider-specific headless config

Needed fields:

- enabled/provider selector
- base image
- worker timeout
- max turns or equivalent
- worker root directories
- auth bootstrap mode
- artifact retention policy

### Orchestrator

New modules likely needed:

- `src/decomp_agent/orchestrator/codex_headless.py`
- `src/decomp_agent/orchestrator/worker_launcher.py`
- `src/decomp_agent/orchestrator/worktree.py`
- possibly `src/decomp_agent/orchestrator/result_ingest.py`

### Docker

Likely changes:

- extend [docker/Dockerfile](/Users/dwilliams/proj/gc-decomp/docker/Dockerfile) for Codex CLI
- add worker entrypoint/bootstrap script
- add Codex-specific MCP/prompt config if needed
- reduce dependence on the static single-worker compose pattern

### CLI

Likely changes in [cli.py](/Users/dwilliams/proj/gc-decomp/src/decomp_agent/cli.py):

- add a `--codex-headless` or provider-select flag
- support isolated worker mode in batch execution
- support keep-artifacts/debug flags

### DB and result recording

Likely changes in [db.py](/Users/dwilliams/proj/gc-decomp/src/decomp_agent/models/db.py):

- store provider/run metadata
- record worker artifacts and patch paths if useful
- keep host as sole DB writer

### Tests

Needed coverage:

- Codex exec output parsing
- worker launch command generation
- worktree lifecycle
- result ingestion
- batch scheduling with file uniqueness

---

## Risks

### Risk 1: Codex auth bootstrap is trickier than expected

Mitigation:

- spike this before broad implementation
- start with one worker and a copied bootstrap
- avoid assuming only one file is needed

### Risk 2: Codex runtime may require network access from worker containers

Mitigation:

- validate early
- if required, constrain network policy narrowly rather than opening workers broadly

### Risk 3: Worktree + build paths may interact badly with absolute-path assumptions

The repo already contains absolute-path sensitivity in the build environment.

Mitigation:

- validate worktree path strategy early
- keep worker path layout predictable
- if needed, create worker paths under a stable prefix

### Risk 4: Applying patches back to main may fail due to drift

Mitigation:

- keep worker lifetimes short
- revalidate before apply
- prefer explicit apply step over automatic commit

### Risk 5: Over-generalizing too early

Mitigation:

- get one Codex worker working first
- defer provider unification refactors
- defer fleet polish until the single-worker path is solid

---

## Recommended Decisions

These are the recommended calls for implementation unless new evidence changes them:

1. Reuse the existing MCP tool server.
2. Add a separate Codex headless path instead of modifying the OpenAI/API loop.
3. Make the host the only orchestration DB writer.
4. Use one worker container per run.
5. Use one isolated worktree per worker.
6. Use one private `.codex` home per worker.
7. Do not auto-commit from workers.
8. Return patches/results to host for explicit application.
9. Schedule at most one active worker per source file.
10. Prove the model with one worker before building the full parallel pool.

---

## Immediate Next Step

Before runtime implementation, the next step should be a narrow technical spike plan covering:

- exact `codex exec` invocation and JSON parsing
- minimal worker auth bootstrap for ChatGPT-backed login
- whether worker network access is required for Codex inference
- worktree path compatibility with the melee build environment

Once those are confirmed, implementation can proceed with low ambiguity.
