# Tuning Decisions

This file records operational parameter changes for the overnight/campaign system.

For each change, capture:

- date
- parameter(s) changed
- old value
- new value
- why we changed it
- what evidence motivated the change
- what outcome we expect to improve

## 2026-03-14

### Claude campaign/worker budget tuning

- Parameters changed:
  - `claude_code.timeout_seconds`
  - `claude_code.max_turns`
  - `claude_code.warm_start_turns`
  - `claude_code.near_match_turns`
  - `claude_code.warm_start_timeout_seconds`
  - `claude_code.near_match_timeout_seconds`
  - `claude_code.warm_start_threshold_pct`
  - `claude_code.near_match_threshold_pct`
  - `claude_code.file_mode_max_turns`
  - `claude_code.file_mode_timeout_seconds`
  - `claude_code.orchestrator_max_turns`
  - `claude_code.orchestrator_timeout_seconds`
  - `campaign.max_active_workers`

- Old values:
  - `timeout_seconds = 1800`
  - `max_turns = 30` in `config/default.toml`
  - warm-start/file-mode/orchestrator escalation behavior partly hardcoded in Python
  - `campaign.max_active_workers = 4`

- New values:
  - `timeout_seconds = 3600`
  - `max_turns = 50`
  - `warm_start_turns = 80`
  - `near_match_turns = 150`
  - `warm_start_timeout_seconds = 3600`
  - `near_match_timeout_seconds = 5400`
  - `warm_start_threshold_pct = 80.0`
  - `near_match_threshold_pct = 95.0`
  - `file_mode_max_turns = 150`
  - `file_mode_timeout_seconds = 7200`
  - `orchestrator_max_turns = 30`
  - `orchestrator_timeout_seconds = 1800`
  - `campaign.max_active_workers = 2`

- Why:
  - The previous worker/orchestrator budgets relied on embedded heuristics and were too conservative for overnight decomp work.
  - We wanted worker persistence and orchestrator planning limits to be tunable without code edits.
  - Lowering default parallelism reduces burn rate and contention during early overnight trials.

- Evidence:
  - Prior overnight and live campaign runs showed workers often needed more room than the old defaults allowed.
  - Review of the code showed important heuristics were hardcoded in `headless.py` and `campaign_orchestrator.py`.

- Expected improvement:
  - better persistence on near-match functions
  - less premature worker termination
  - more stable overnight behavior
  - easier iteration on budgets using config rather than code changes
