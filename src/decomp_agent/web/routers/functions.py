"""Function list and treemap hierarchy endpoints."""

from __future__ import annotations

import json
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func as sa_func
from sqlmodel import Session, select

from decomp_agent.models.db import Attempt, Function, Run
from decomp_agent.web.deps import get_session

router = APIRouter(prefix="/api/functions", tags=["functions"])


@router.get("")
def list_functions(
    session: Session = Depends(get_session),
    library: str | None = None,
    status: str | None = None,
    min_match: float | None = None,
    max_match: float | None = None,
    sort_by: str = "size",
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
):
    """Paginated, filterable function list."""
    stmt = select(Function)

    if library is not None:
        stmt = stmt.where(Function.library == library)
    if status is not None:
        stmt = stmt.where(Function.status == status)
    if min_match is not None:
        stmt = stmt.where(Function.current_match_pct >= min_match)
    if max_match is not None:
        stmt = stmt.where(Function.current_match_pct <= max_match)

    # Count total before pagination
    count_stmt = select(sa_func.count()).select_from(stmt.subquery())
    total = session.exec(count_stmt).one()  # type: ignore[call-overload]

    # Sort
    if sort_by == "size":
        stmt = stmt.order_by(Function.size)  # type: ignore[arg-type]
    elif sort_by == "match_pct":
        stmt = stmt.order_by(Function.current_match_pct.desc())  # type: ignore[arg-type, attr-defined]
    elif sort_by == "name":
        stmt = stmt.order_by(Function.name)  # type: ignore[arg-type]
    elif sort_by == "library":
        stmt = stmt.order_by(Function.library, Function.name)  # type: ignore[arg-type]
    else:
        stmt = stmt.order_by(Function.size)  # type: ignore[arg-type]

    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    functions = session.exec(stmt).all()

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "functions": [
            {
                "id": f.id,
                "name": f.name,
                "address": f.address,
                "size": f.size,
                "source_file": f.source_file,
                "library": f.library,
                "initial_match_pct": f.initial_match_pct,
                "current_match_pct": f.current_match_pct,
                "status": f.status,
                "attempts": f.attempts,
                "matched_at": f.matched_at.isoformat() if f.matched_at else None,
                "updated_at": f.updated_at.isoformat(),
            }
            for f in functions
        ],
    }


@router.get("/treemap")
def treemap_data(session: Session = Depends(get_session)):
    """Pre-built hierarchy: root -> library -> source_file -> function.

    Each leaf node contains: name, size, match_pct, status.
    """
    functions = session.exec(select(Function)).all()

    # Build nested: library -> source_file -> [functions]
    tree: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for f in functions:
        tree[f.library][f.source_file].append(
            {
                "name": f.name,
                "id": f.id,
                "size": f.size,
                "match_pct": f.current_match_pct,
                "status": f.status,
            }
        )

    # Convert to D3-compatible hierarchy
    children = []
    for lib_name, source_files in sorted(tree.items()):
        lib_children = []
        for sf_name, funcs in sorted(source_files.items()):
            lib_children.append(
                {
                    "name": sf_name,
                    "children": funcs,
                }
            )
        children.append(
            {
                "name": lib_name,
                "children": lib_children,
            }
        )

    return {
        "name": "root",
        "children": children,
    }


@router.get("/{function_id}")
def get_function(function_id: int, session: Session = Depends(get_session)):
    """Get a single function by ID."""
    func = session.get(Function, function_id)
    if func is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Function not found")
    return {
        "id": func.id,
        "name": func.name,
        "address": func.address,
        "size": func.size,
        "source_file": func.source_file,
        "library": func.library,
        "initial_match_pct": func.initial_match_pct,
        "current_match_pct": func.current_match_pct,
        "status": func.status,
        "attempts": func.attempts,
        "matched_at": func.matched_at.isoformat() if func.matched_at else None,
        "created_at": func.created_at.isoformat(),
        "updated_at": func.updated_at.isoformat(),
    }


@router.get("/{function_id}/attempts")
def get_function_attempts(function_id: int, session: Session = Depends(get_session)):
    """Attempt history for a function."""
    func = session.get(Function, function_id)
    if func is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Function not found")

    stmt = (
        select(Attempt)
        .where(Attempt.function_id == function_id)
        .order_by(Attempt.started_at.desc())  # type: ignore[attr-defined]
    )
    attempts = session.exec(stmt).all()

    # Batch-fetch linked Run records
    run_ids = [a.run_id for a in attempts if a.run_id is not None]
    runs_by_id: dict[int, Run] = {}
    if run_ids:
        run_rows = session.exec(select(Run).where(Run.id.in_(run_ids))).all()  # type: ignore[union-attr]
        runs_by_id = {r.id: r for r in run_rows}  # type: ignore[misc]

    result_attempts = []
    for a in attempts:
        run = runs_by_id.get(a.run_id) if a.run_id else None  # type: ignore[arg-type]
        # For new records, session-level data comes from Run
        total_tokens = run.total_tokens if run else a.total_tokens
        input_tokens = run.input_tokens if run else a.input_tokens
        output_tokens = run.output_tokens if run else a.output_tokens
        cached_tokens = run.cached_tokens if run else a.cached_tokens
        elapsed_seconds = run.elapsed_seconds if run else a.elapsed_seconds
        cost = run.cost if run else a.cost

        result_attempts.append({
            "id": a.id,
            "run_id": a.run_id,
            "started_at": a.started_at.isoformat(),
            "completed_at": a.completed_at.isoformat() if a.completed_at else None,
            "matched": a.matched,
            "best_match_pct": a.best_match_pct,
            "before_match_pct": a.before_match_pct,
            "iterations": a.iterations,
            "total_tokens": total_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "elapsed_seconds": elapsed_seconds,
            "termination_reason": a.termination_reason,
            "final_code": a.final_code,
            "error": a.error,
            "model": a.model,
            "reasoning_effort": a.reasoning_effort,
            "match_history": json.loads(a.match_history) if a.match_history else [],
            "tool_counts": json.loads(a.tool_counts) if a.tool_counts else {},
            "cost": cost,
            "file_mode": run.file_mode if run else False,
        })

    return {
        "function_name": func.name,
        "attempts": result_attempts,
    }
