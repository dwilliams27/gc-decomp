#!/bin/bash
# Quick status check for overnight run. Reads status file and queries DB.
cd "$(dirname "$0")"

echo "=== Overnight Status ==="
if [[ -f overnight-status.json ]]; then
    cat overnight-status.json | python3 -m json.tool 2>/dev/null || cat overnight-status.json
else
    echo "No status file found (overnight.sh may not be running)"
fi

echo ""
echo "=== DB Summary ==="
python3 -c "
from decomp_agent.models.db import get_engine, Function, Attempt
from sqlmodel import Session, select, func as sa_func

engine = get_engine('decomp.db')
with Session(engine) as s:
    total = s.exec(select(sa_func.count()).select_from(Function)).one()
    pending = s.exec(select(sa_func.count()).select_from(Function).where(Function.status == 'pending')).one()
    matched = s.exec(select(sa_func.count()).select_from(Function).where(Function.status == 'matched')).one()
    in_prog = s.exec(select(sa_func.count()).select_from(Function).where(Function.status == 'in_progress')).one()

    # Recent attempts
    recent = s.exec(
        select(Attempt)
        .order_by(Attempt.id.desc())
        .limit(5)
    ).all()

    print(f'Functions: {matched} matched / {total} total ({pending} pending, {in_prog} in_progress)')
    print(f'Match rate: {matched/total*100:.1f}%')
    print()
    print('Last 5 attempts:')
    for a in recent:
        func = s.exec(select(Function).where(Function.id == a.function_id)).first()
        name = func.name if func else '???'
        status = 'MATCH' if a.matched else a.termination_reason
        print(f'  {name}: {status} ({a.best_match_pct:.1f}%, {a.iterations} iters, {a.elapsed_seconds:.0f}s)')
" 2>/dev/null || echo "DB query failed"

echo ""
echo "=== Log tail ==="
LOG=$(ls -t overnight-*.log 2>/dev/null | head -1)
if [[ -n "$LOG" ]]; then
    tail -5 "$LOG"
else
    echo "No log files found"
fi
