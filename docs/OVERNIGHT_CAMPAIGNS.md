# Overnight Campaign Operations

## Status

This document describes the overnight file-campaign functionality that is implemented today in `gc-decomp`.

Implemented:

- provider-selectable campaigns (`claude`, `codex`, `mixed`)
- isolated Claude workers
- isolated Codex workers
- per-worker git worktrees and isolated containers
- worker patch/artifact capture
- host-side isolated patch promotion for single-function runs
- campaign task queueing and worker dispatch
- provider cooldown handling for rate limits
- fixed-window Claude cooldown alignment

Partially implemented / still being hardened:

- orchestrator-driven overnight supervision
- broader file/header/shared-fix promotion flows
- long-run campaign recovery and resume behavior

For the first serious overnight runs, prefer the worker queue path (`campaign run`) over the full supervisor/orchestrator loop unless you are actively testing orchestrator behavior.

## Main Components

Relevant modules:

- `src/decomp_agent/orchestrator/campaign.py`
- `src/decomp_agent/orchestrator/campaign_orchestrator.py`
- `src/decomp_agent/orchestrator/headless.py`
- `src/decomp_agent/orchestrator/codex_headless.py`
- `src/decomp_agent/orchestrator/worker_launcher.py`
- `src/decomp_agent/orchestrator/worker_results.py`
- `src/decomp_agent/orchestrator/worktree.py`

Relevant CLI commands:

- `decomp-agent campaign start ...`
- `decomp-agent campaign show <id>`
- `decomp-agent campaign run <id>`
- `decomp-agent campaign supervise <id>`
- `decomp-agent campaign orchestrate <id>`

## Provider Model

Campaigns are configured independently for:

- orchestrator provider
- worker provider policy

Valid options:

- orchestrator: `claude`, `codex`
- workers: `claude`, `codex`, `mixed`

Notes:

- Claude and Codex do not need identical CLI/session behavior.
- Claude workers and Codex workers are both isolated now.
- `mixed` is supported at the campaign model level, but scheduling policy is still basic.

## Isolation Model

Each isolated worker gets:

- one detached git worktree
- one dedicated container
- one private agent home
- one artifact directory
- one worker-local config file

This allows same-file parallel exploration without workers clobbering one another. Promotion back to the main melee checkout is still serialized.

Worker roots:

- Claude: `/private/tmp/decomp-claude-workers` and `/tmp/decomp-claude-workers`
- Codex: `/private/tmp/decomp-codex-workers` and `/tmp/decomp-codex-workers`

Campaign roots:

- `/tmp/decomp-campaigns/campaign-<id>/`

## Rate Limit Handling

Campaigns now defer and retry rate-limited work instead of treating it as final progress.

Implemented behavior:

- task-level `next_eligible_at`
- provider-level cooldowns on campaigns
- deferred `pending` requeue for rate-limited tasks
- cooldown-aware claiming/dispatch
- supervisor sleep instead of wasting no-progress cycles during cooldown

Claude-specific behavior:

- Claude cooldowns are aligned to a fixed 5-hour reset window
- current anchor in code: `2026-03-13 02:04 CDT`

This means overnight runs should wait out Claude usage windows instead of hammering the provider.

## Recommended Overnight Path

For the current implementation, the safest overnight flow is:

1. Start a fresh campaign.
2. Use a clean melee checkout synced to `upstream/master`.
3. Prefer one file per campaign.
4. Start with `claude` or `codex` workers only, not `mixed`.
5. Use `max_active_workers = 2` for the first run on a new file.
6. Prefer `decomp-agent campaign run <id>` for the first unattended run.

Example:

```bash
decomp-agent campaign start melee/ft/chara/ftPurin/ftPr_SpecialN.c \
  --orchestrator-provider claude \
  --worker-provider-policy claude \
  --max-active-workers 2 \
  --timeout-hours 8

decomp-agent campaign run <campaign_id>
```

If you want background execution:

```bash
nohup decomp-agent campaign run <campaign_id> \
  > /tmp/decomp-campaigns/campaign-<campaign_id>/run.log 2>&1 &
```

## Current Caveats

- The orchestrator/supervisor control plane exists, but long unattended orchestrator-driven runs still need more hardening.
- The simpler worker queue is the more reliable overnight path right now.
- Isolated worker cleanup is not automatic in every failure case yet; stale worker dirs/worktrees should be cleaned before a fresh overnight run.
- A worker can produce a `100%` candidate patch/artifact without that result being fully promoted into durable matched state if promotion/validation is interrupted.

## Morning-After Inspection

Useful things to inspect:

- campaign row in `decomp.db`
- `campaigntask` rows for the campaign
- manager notes:
  - `/tmp/decomp-campaigns/campaign-<id>/artifacts/manager-notes.md`
- worker artifact dirs under `/tmp/decomp-campaigns/campaign-<id>/` and `/tmp/decomp-*-workers/...`
- supervisor summary artifact, when produced:
  - `/tmp/decomp-campaigns/campaign-<id>/artifacts/supervisor-summary.json`

When reviewing a run, do not stop at “matched vs failed”. Also answer:

- what went well?
- which functions improved, even if they did not match?
- what blocked progress?
- did the orchestrator leave useful manager notes?
- what parameter tunings look promising for the next run?

Useful commands:

```bash
decomp-agent campaign show <campaign_id>
sqlite3 decomp.db "select id, function_name, status, termination_reason, best_match_pct from campaigntask where campaign_id = <campaign_id> order by id;"
git -C /Users/dwilliams/proj/melee-fork/melee status --short
```

## Cleanup Before a Fresh Overnight Run

Before starting a fresh overnight run:

- make sure the melee checkout is clean
- remove stale isolated worker worktrees
- remove stale worker roots under `/tmp` and `/private/tmp`
- stop stray isolated worker containers

This avoids carrying residue from earlier manual testing into the overnight campaign.
