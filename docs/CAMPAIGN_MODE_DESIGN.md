# Campaign Mode Design

## Goal

Add a long-running autonomous `campaign` mode for matching an entire source file overnight.

This mode should:

- run fully inside sandboxed containers
- allow autonomous execution without human approval prompts
- support one orchestrator agent managing a fleet of worker agents
- support long-running, multi-stage work that may require edits outside the target `.c` file
- support either Claude Code or Codex for both orchestrator and workers
- allow mixed-provider fleets so expensive/abundant compute can be allocated intentionally

## Core Requirements

Campaign mode is not just "batch mode with more workers." It needs:

- a persistent campaign state model
- an orchestrator agent that plans and reacts
- isolated worker agents that can be launched, resumed, and replaced
- staged patch promotion and validation
- provider selection at campaign start

The system should be suitable for an overnight run where no human is expected to intervene.

## Provider Model

Campaign mode must be provider-agnostic.

At campaign start, configuration should choose:

- orchestrator provider: `claude` or `codex`
- worker provider policy:
  - `claude`
  - `codex`
  - `mixed`

In mixed mode, the orchestrator can dispatch workers to either provider according to policy.

Example useful configurations:

- orchestrator = `claude`, workers = `claude`
- orchestrator = `codex`, workers = `codex`
- orchestrator = `claude`, workers = `mixed`
- orchestrator = `codex`, workers = `mixed`

Practical reason:

- Claude Max 2x may provide more overnight worker compute
- Codex Pro may still be useful for some worker tasks or orchestration styles
- different providers may be better for different task shapes

This does not need 1:1 parity in CLI flags. The abstraction should be:

- orchestrator provider interface
- worker provider interface

Each provider can be implemented differently under the hood.

## High-Level Architecture

Campaign mode has three layers:

1. Host scheduler/validator
2. Orchestrator agent
3. Worker agents

### Host scheduler/validator

The host remains deterministic and non-agentic. It is responsible for:

- creating the campaign record
- creating isolated worktrees and containers
- exposing campaign-management tools to the orchestrator
- validating candidate patches
- promoting accepted patches into a staging worktree
- persisting artifacts, status, and metrics

The host should not do strategic reasoning. That belongs to the orchestrator agent.

### Orchestrator agent

The orchestrator is a headless agent session running in its own isolated container/worktree.

Its job is to:

- inspect campaign status
- choose which functions to attack
- choose worker scope and provider
- analyze worker outputs
- re-dispatch promising functions with new instructions
- identify header/type/shared-file blockers
- coordinate staged progress across the file

The orchestrator should be told explicitly:

- this is a long-running overnight autonomous campaign
- there is no true internet/web access beyond provider inference connectivity
- it must rely on local repo context, MCP tools, and worker artifacts
- it should assume hard functions may take many turns, many workers, and many retries

### Worker agents

Workers are disposable or resumable isolated agent sessions.

Each worker gets:

- its own container
- its own repo worktree
- its own home directory
- its own provider auth/config
- its own artifact directory

Workers should be launched against a scoped task, not just a raw function name.

## Worker Scope Types

Campaign mode should support multiple worker scopes.

### 1. Function worker

Allowed to focus primarily on one function.

Typical use:

- local register-allocation cleanup
- expression restructuring
- direct function matching

### 2. File repair worker

Allowed to modify:

- target `.c` file
- directly related headers

Typical use:

- fixing `UNK_RET` / `UNK_PARAMS`
- correcting struct fields/types
- cleaning shared declarations used by several functions in the file

### 3. Shared fix worker

Allowed broader scoped edits outside the file when a real cross-file blocker is identified.

Typical use:

- shared headers
- common struct definitions
- declarations or inlines used by multiple modules

This broader mode should be orchestrator-directed, not the default for every worker.

## Campaign Workflow

### Phase 1: Initialize campaign

Host creates:

- campaign record
- staging worktree
- orchestrator worktree/container
- initial file-wide status snapshot

### Phase 2: Orchestrator planning

The orchestrator receives:

- target source file
- current status of all functions in the file
- available provider options
- campaign rules and limits
- worker-management tools

It then creates an initial plan:

- easiest functions first
- promising near-match retries
- likely header/type cleanup opportunities

### Phase 3: Worker dispatch

The orchestrator launches workers with:

- target function or repair task
- worker scope
- provider selection
- custom instructions
- optional prior worker artifact/session to build on

### Phase 4: Result analysis

The orchestrator watches worker results and reacts.

Examples:

- Worker A gets a function from `88%` to `96%`
- Orchestrator reads the patch and diff summary
- Orchestrator decides the remaining issue is type/layout related
- It launches Worker B on the same function with explicit "preserve structure, focus on declaration order and header types"
- It may also launch Worker C as a file-repair worker to fix the relevant header

### Phase 5: Patch promotion

When a worker produces a promising result, the host:

1. applies the patch to the campaign staging worktree
2. recompiles/checks the relevant translation unit
3. validates target improvement
4. ensures already-matched functions do not regress
5. records file-wide status changes

The orchestrator then sees the updated status and plans the next wave.

## Validation Rules

Validation should happen against the campaign staging worktree, not the primary checkout.

Baseline rules:

- never regress a 100% function
- reject patches that fail to apply cleanly
- reject compile failures
- reject patches that make the target function worse

For unmatched functions, campaign mode may optionally allow controlled temporary regressions if:

- net file progress improves
- the orchestrator is informed
- the regression is limited and recoverable

This should be a campaign policy toggle, not the default.

## Provider-Agnostic Interfaces

The host should expose one orchestrator interface and one worker interface, independent of provider.

### Orchestrator interface

The campaign runner should be able to launch:

- `run_orchestrator(provider=claude|codex, ...)`

### Worker interface

The campaign runner should be able to launch:

- `run_worker(provider=claude|codex, scope=..., instructions=..., ...)`

Implementation can differ:

- Claude may use one-shot sessions with larger turn budgets
- Codex may rely on resumable sessions instead of a direct max-turn flag

The campaign layer should not care. It should reason in terms of:

- launch worker
- resume worker
- inspect result
- promote patch

## Persistence and Resume

Campaign mode should support persistent worker sessions.

For Codex, this likely means:

- stop using ephemeral mode for campaign workers
- store session ids
- use `codex exec resume` for continued work on promising functions

For Claude, this likely means:

- continuing or relaunching sessions with prior context and artifact history

The orchestrator should be able to choose between:

- resume same worker/session
- fork a new worker on the same function with different instructions
- escalate scope

## New Campaign Tools

The orchestrator needs host-backed management tools.

Suggested tools:

- `campaign_get_status(source_file)`
- `campaign_list_unmatched(source_file)`
- `campaign_launch_worker(task, provider, scope, instructions)`
- `campaign_resume_worker(worker_id, instructions)`
- `campaign_list_workers(source_file)`
- `campaign_get_worker_result(worker_id)`
- `campaign_read_worker_artifact(worker_id, artifact_kind)`
- `campaign_promote_patch(worker_id)`
- `campaign_check_file(source_file)`
- `campaign_stop_worker(worker_id)`

These should be higher-level control tools, not raw docker commands.

## Orchestrator Prompt Requirements

The orchestrator prompt should explicitly say:

- this is an overnight autonomous campaign
- there is no true internet/web access
- workers are expensive resources and should be managed intentionally
- use parallelism to explore different angles
- do not stop because one worker produced a plausible draft
- monitor progress and keep dispatching follow-up work
- header and shared-type cleanup may be required
- choose provider/provider mix strategically

## Worker Prompt Requirements

Worker prompts should explicitly say:

- this task may require many iterations
- do not stop after the first non-matching draft
- use `write_function`, `get_diff`, `get_context`, and repo edits aggressively
- if given a prior patch/result, attack the remaining mismatch from a different angle
- obey the assigned scope

## Configuration Model

Campaign mode should be configurable at run start.

Suggested top-level campaign options:

- `source_file`
- `orchestrator_provider = claude|codex`
- `worker_provider_policy = claude|codex|mixed`
- `max_active_workers`
- `campaign_timeout_hours`
- `allow_shared_fix_workers`
- `allow_temporary_unmatched_regressions`
- `worker_resume_policy`
- `provider_allocation_policy`

For mixed fleets, provider allocation policy could later include:

- `prefer_claude_for_workers`
- `prefer_codex_for_workers`
- `near_match_provider = claude|codex`
- `header_fix_provider = claude|codex`

## Safety Boundaries

Even in autonomous overnight mode:

- all agent command freedom is bounded by the container sandbox
- workers do not share writable repo state
- campaign changes are promoted through host validation
- the primary repo checkout is not directly mutated by worker containers

## Recommended MVP

Start with:

- one file campaign
- one orchestrator agent
- N worker agents
- provider choice at startup
- mixed worker fleet support
- function workers and file-repair workers
- staged promotion into a campaign worktree

Defer initially:

- shared-fix workers across the broader repo
- temporary regression policies
- sophisticated provider routing heuristics

## Why This Design

This gives:

- overnight autonomy
- real parallel exploration
- provider flexibility
- isolation strong enough for many workers
- a strategic orchestrator that can learn from worker failures and re-dispatch better follow-ups

That is the right shape for matching entire files rather than just running independent single-function attempts.
