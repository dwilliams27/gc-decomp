"""Statistics and overview endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import case, func as sa_func
from sqlmodel import Session, select

from decomp_agent.models.db import Attempt, Function
from decomp_agent.web.deps import get_session

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/overview")
def overview(session: Session = Depends(get_session)):
    """Global statistics: counts, tokens, cost, match distribution."""
    total = session.exec(select(sa_func.count(Function.id))).one()

    # Counts by status
    status_rows = session.exec(
        select(Function.status, sa_func.count(Function.id)).group_by(Function.status)
    ).all()
    status_counts = {status: count for status, count in status_rows}

    # Token and cost totals from attempts
    agg = session.exec(
        select(
            sa_func.coalesce(sa_func.sum(Attempt.total_tokens), 0),
            sa_func.coalesce(sa_func.sum(Attempt.cost), 0.0),
            sa_func.count(Attempt.id),
        )
    ).one()
    total_tokens, total_cost, total_attempts = agg

    # Match distribution histogram (10% buckets)
    histogram = []
    for bucket_start in range(0, 100, 10):
        bucket_end = bucket_start + 10
        if bucket_start == 90:
            # 90-99% (exclude 100% matched)
            count = session.exec(
                select(sa_func.count(Function.id)).where(
                    Function.current_match_pct >= bucket_start,
                    Function.current_match_pct < 100.0,
                )
            ).one()
        else:
            count = session.exec(
                select(sa_func.count(Function.id)).where(
                    Function.current_match_pct >= bucket_start,
                    Function.current_match_pct < bucket_end,
                )
            ).one()
        histogram.append({"range": f"{bucket_start}-{bucket_end}%", "count": count})

    # 100% bucket
    matched_count = session.exec(
        select(sa_func.count(Function.id)).where(Function.current_match_pct >= 100.0)
    ).one()
    histogram.append({"range": "100%", "count": matched_count})

    # Total byte size
    total_bytes = session.exec(
        select(sa_func.coalesce(sa_func.sum(Function.size), 0))
    ).one()
    matched_bytes = session.exec(
        select(sa_func.coalesce(sa_func.sum(Function.size), 0)).where(
            Function.current_match_pct >= 100.0
        )
    ).one()

    return {
        "total_functions": total,
        "status_counts": status_counts,
        "total_tokens": total_tokens,
        "total_cost": round(float(total_cost), 4),
        "total_attempts": total_attempts,
        "total_bytes": total_bytes,
        "matched_bytes": matched_bytes,
        "match_histogram": histogram,
    }


@router.get("/by-library")
def by_library(session: Session = Depends(get_session)):
    """Per-library statistics: count, matched, avg match, cost."""
    rows = session.exec(
        select(
            Function.library,
            sa_func.count(Function.id),
            sa_func.sum(case((Function.current_match_pct >= 100.0, 1), else_=0)),
            sa_func.avg(Function.current_match_pct),
            sa_func.sum(Function.size),
        ).group_by(Function.library)
    ).all()

    # Get cost per library via join
    cost_rows = session.exec(
        select(
            Function.library,
            sa_func.coalesce(sa_func.sum(Attempt.cost), 0.0),
            sa_func.coalesce(sa_func.sum(Attempt.total_tokens), 0),
        )
        .join(Attempt, Attempt.function_id == Function.id)  # type: ignore[arg-type]
        .group_by(Function.library)
    ).all()
    cost_by_lib = {lib: (cost, tokens) for lib, cost, tokens in cost_rows}

    libraries = []
    for lib_name, count, matched, avg_match, total_size in rows:
        cost, tokens = cost_by_lib.get(lib_name, (0.0, 0))
        libraries.append(
            {
                "library": lib_name,
                "count": count,
                "matched": matched or 0,
                "avg_match_pct": round(float(avg_match or 0), 2),
                "total_size": total_size or 0,
                "cost": round(float(cost), 4),
                "tokens": tokens,
            }
        )

    libraries.sort(key=lambda x: x["count"], reverse=True)
    return {"libraries": libraries}
