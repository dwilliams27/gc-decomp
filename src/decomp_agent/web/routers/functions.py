"""Function list and treemap hierarchy endpoints."""

from __future__ import annotations

import json
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func as sa_func
from sqlmodel import Session, select

from decomp_agent.models.db import Attempt, Function
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

    return {
        "function_name": func.name,
        "attempts": [
            {
                "id": a.id,
                "started_at": a.started_at.isoformat(),
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                "matched": a.matched,
                "best_match_pct": a.best_match_pct,
                "iterations": a.iterations,
                "total_tokens": a.total_tokens,
                "input_tokens": a.input_tokens,
                "output_tokens": a.output_tokens,
                "cached_tokens": a.cached_tokens,
                "elapsed_seconds": a.elapsed_seconds,
                "termination_reason": a.termination_reason,
                "final_code": a.final_code,
                "error": a.error,
                "model": a.model,
                "reasoning_effort": a.reasoning_effort,
                "match_history": json.loads(a.match_history) if a.match_history else [],
                "tool_counts": json.loads(a.tool_counts) if a.tool_counts else {},
                "cost": a.cost,
            }
            for a in attempts
        ],
    }
