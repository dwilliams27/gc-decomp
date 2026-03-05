#!/bin/bash
# Overnight batch runner with smart rate-limit handling and auto-restart.
# Runs batches of BATCH_SIZE functions, pausing on rate limits.
# Writes status to overnight-status.json for monitoring.

set -euo pipefail
cd "$(dirname "$0")"

BATCH_SIZE=${BATCH_SIZE:-10}
MAX_SIZE=${MAX_SIZE:-1000}
STRATEGY=${STRATEGY:-smallest_first}
WORKERS=${WORKERS:-3}
LOG_FILE="overnight-$(date +%Y%m%d-%H%M%S).log"
STATUS_FILE="overnight-status.json"
RATE_LIMIT_BACKOFF=600   # Start with 10 min on persistent rate limits
MAX_BACKOFF=1800          # Cap at 30 min
CONSECUTIVE_EMPTY=0
CONSECUTIVE_RATE_LIMIT=0
TOTAL_MATCHED=0
TOTAL_ATTEMPTED=0
TOTAL_ERRORS=0
BATCH_NUM=0
START_TIME=$(date +%s)

write_status() {
    local status="$1"
    local detail="${2:-}"
    local now=$(date +%s)
    local elapsed=$(( now - START_TIME ))
    cat > "$STATUS_FILE" <<STATUSEOF
{
  "status": "$status",
  "detail": "$detail",
  "batch_num": $BATCH_NUM,
  "total_matched": $TOTAL_MATCHED,
  "total_attempted": $TOTAL_ATTEMPTED,
  "total_errors": $TOTAL_ERRORS,
  "elapsed_seconds": $elapsed,
  "last_update": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "log_file": "$LOG_FILE"
}
STATUSEOF
}

log_msg() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log_msg "=== Overnight batch run starting ==="
log_msg "Batch size: $BATCH_SIZE, Max function size: $MAX_SIZE, Strategy: $STRATEGY, Workers: $WORKERS"
log_msg "Log file: $LOG_FILE"

write_status "starting" "Initializing overnight run"

while true; do
    BATCH_NUM=$(( BATCH_NUM + 1 ))
    log_msg "--- Batch #$BATCH_NUM ---"
    write_status "running" "Batch #$BATCH_NUM in progress"

    # Run a batch. Capture exit code.
    set +e
    OUTPUT=$(decomp-agent batch \
        --headless \
        --yes \
        --limit "$BATCH_SIZE" \
        --max-size "$MAX_SIZE" \
        --strategy "$STRATEGY" \
        --workers "$WORKERS" \
        --warm-start \
        2>&1 | tee -a "$LOG_FILE")
    EXIT_CODE=$?
    set -e

    # Parse results from output — use specific event names to avoid double-counting
    BATCH_MATCHED=$(echo "$OUTPUT" | grep -c "function_matched\|matched=True" || true)
    BATCH_RATE_LIMITED=$(echo "$OUTPUT" | grep -ci "rate.limit\|fast_crash\|overloaded" || true)
    BATCH_NO_CANDIDATES=$(echo "$OUTPUT" | grep -c "No candidates" || true)

    TOTAL_MATCHED=$(( TOTAL_MATCHED + BATCH_MATCHED ))

    # Count attempted — only batch_function_start (fires once per function)
    BATCH_ATTEMPTED=$(echo "$OUTPUT" | grep -c "batch_function_start" || true)
    TOTAL_ATTEMPTED=$(( TOTAL_ATTEMPTED + BATCH_ATTEMPTED ))

    log_msg "Batch #$BATCH_NUM done: exit=$EXIT_CODE matched=$BATCH_MATCHED attempted=$BATCH_ATTEMPTED"
    log_msg "Running totals: matched=$TOTAL_MATCHED attempted=$TOTAL_ATTEMPTED"

    # Handle no candidates (all done or filters too narrow)
    if [[ "$BATCH_NO_CANDIDATES" -gt 0 ]]; then
        CONSECUTIVE_EMPTY=$(( CONSECUTIVE_EMPTY + 1 ))
        if [[ "$CONSECUTIVE_EMPTY" -ge 3 ]]; then
            log_msg "No candidates found 3 times in a row. All done or filters too narrow. Exiting."
            write_status "completed" "No more candidates available"
            break
        fi
        log_msg "No candidates (attempt $CONSECUTIVE_EMPTY/3). Waiting 60s..."
        write_status "waiting" "No candidates, retry $CONSECUTIVE_EMPTY/3"
        sleep 60
        continue
    fi
    CONSECUTIVE_EMPTY=0

    # Handle rate limits
    if [[ "$BATCH_RATE_LIMITED" -gt 2 ]]; then
        CONSECUTIVE_RATE_LIMIT=$(( CONSECUTIVE_RATE_LIMIT + 1 ))
        BACKOFF=$(( RATE_LIMIT_BACKOFF * CONSECUTIVE_RATE_LIMIT ))
        if [[ "$BACKOFF" -gt "$MAX_BACKOFF" ]]; then
            BACKOFF=$MAX_BACKOFF
        fi
        log_msg "Heavy rate limiting detected ($BATCH_RATE_LIMITED hits). Backing off ${BACKOFF}s..."
        write_status "rate_limited" "Backing off ${BACKOFF}s (consecutive: $CONSECUTIVE_RATE_LIMIT)"
        sleep "$BACKOFF"
        continue
    fi
    CONSECUTIVE_RATE_LIMIT=0

    # Handle crash/error
    if [[ "$EXIT_CODE" -ne 0 ]]; then
        TOTAL_ERRORS=$(( TOTAL_ERRORS + 1 ))
        if [[ "$TOTAL_ERRORS" -ge 5 ]]; then
            log_msg "Too many errors ($TOTAL_ERRORS). Giving up."
            write_status "error" "Too many consecutive errors ($TOTAL_ERRORS)"
            break
        fi
        log_msg "Batch exited with code $EXIT_CODE. Waiting 120s before retry..."
        write_status "error_retry" "Exit code $EXIT_CODE, retry in 120s"
        sleep 120
        continue
    fi
    TOTAL_ERRORS=0  # Reset on success

    # Brief pause between successful batches to avoid hammering the API
    log_msg "Batch complete. Brief cooldown (30s)..."
    write_status "cooldown" "30s between batches"
    sleep 30
done

log_msg "=== Overnight run finished ==="
log_msg "Final: matched=$TOTAL_MATCHED attempted=$TOTAL_ATTEMPTED errors=$TOTAL_ERRORS"
write_status "finished" "matched=$TOTAL_MATCHED attempted=$TOTAL_ATTEMPTED"
