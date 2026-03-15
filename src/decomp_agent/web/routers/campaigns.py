"""Campaign list, detail, events, messages, and SSE endpoints."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select
from sqlalchemy import func as sa_func

from decomp_agent.models.db import (
    Campaign,
    CampaignEvent,
    CampaignMessage,
    CampaignTask,
    Function,
)
from decomp_agent.web.deps import get_session

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def _campaign_dict(c: Campaign, tasks: list[CampaignTask] | None = None) -> dict:
    d = {
        "id": c.id,
        "source_file": c.source_file,
        "status": c.status,
        "orchestrator_provider": c.orchestrator_provider,
        "worker_provider_policy": c.worker_provider_policy,
        "max_active_workers": c.max_active_workers,
        "timeout_hours": c.timeout_hours,
        "notes": c.notes or "",
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "completed_at": c.completed_at.isoformat() if c.completed_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
    if tasks is not None:
        d["tasks"] = [_task_dict(t) for t in tasks]
    return d


def _task_dict(t: CampaignTask) -> dict:
    return {
        "id": t.id,
        "campaign_id": t.campaign_id,
        "function_id": t.function_id,
        "function_name": t.function_name,
        "source_file": t.source_file,
        "provider": t.provider,
        "scope": t.scope,
        "status": t.status,
        "priority": t.priority,
        "best_match_pct": t.best_match_pct,
        "termination_reason": t.termination_reason,
        "error": t.error,
        "worker_id": t.worker_id,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
    }


def _event_dict(e: CampaignEvent) -> dict:
    return {
        "id": e.id,
        "campaign_id": e.campaign_id,
        "task_id": e.task_id,
        "function_name": e.function_name,
        "event_type": e.event_type,
        "data": json.loads(e.data) if e.data else {},
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def _message_dict(m: CampaignMessage) -> dict:
    return {
        "id": m.id,
        "campaign_id": m.campaign_id,
        "role": m.role,
        "content": m.content,
        "session_number": m.session_number,
        "turn_number": m.turn_number,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("")
def list_campaigns(
    session: Session = Depends(get_session),
    status: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    """List campaigns with optional status filter and pagination."""
    stmt = select(Campaign)
    if status is not None:
        stmt = stmt.where(Campaign.status == status)
    count_stmt = select(sa_func.count()).select_from(stmt.subquery())
    total = session.exec(count_stmt).one()  # type: ignore[call-overload]
    stmt = stmt.order_by(Campaign.id.desc()).offset((page - 1) * per_page).limit(per_page)  # type: ignore[arg-type]
    campaigns = session.exec(stmt).all()
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "campaigns": [_campaign_dict(c) for c in campaigns],
    }


@router.get("/{campaign_id}")
def get_campaign_detail(
    campaign_id: int,
    session: Session = Depends(get_session),
):
    """Campaign detail with tasks."""
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Campaign not found")
    tasks = list(session.exec(
        select(CampaignTask)
        .where(CampaignTask.campaign_id == campaign_id)
        .order_by(CampaignTask.priority.desc(), CampaignTask.id.asc())  # type: ignore[arg-type]
    ).all())
    return _campaign_dict(campaign, tasks)


@router.get("/{campaign_id}/events")
def get_campaign_events(
    campaign_id: int,
    session: Session = Depends(get_session),
    after_id: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    """CampaignEvent rows with cursor-based pagination."""
    stmt = (
        select(CampaignEvent)
        .where(
            CampaignEvent.campaign_id == campaign_id,
            CampaignEvent.id > after_id,  # type: ignore[operator]
        )
        .order_by(CampaignEvent.id.asc())  # type: ignore[arg-type]
        .limit(limit)
    )
    events = list(session.exec(stmt).all())
    return {
        "events": [_event_dict(e) for e in events],
        "last_id": events[-1].id if events else after_id,
    }


@router.get("/{campaign_id}/events/stream")
async def stream_campaign_events(
    campaign_id: int,
    request: Request,
    after_id: int = Query(0, ge=0),
):
    """SSE endpoint tailing CampaignEvent rows every 1-2s."""
    from decomp_agent.web.deps import get_state
    engine = get_state().engine

    async def event_generator():
        last_id = after_id
        while True:
            if await request.is_disconnected():
                break
            with Session(engine) as session:
                events = list(session.exec(
                    select(CampaignEvent)
                    .where(
                        CampaignEvent.campaign_id == campaign_id,
                        CampaignEvent.id > last_id,  # type: ignore[operator]
                    )
                    .order_by(CampaignEvent.id.asc())  # type: ignore[arg-type]
                    .limit(50)
                ).all())
                if events:
                    for e in events:
                        data = json.dumps(_event_dict(e))
                        yield f"id: {e.id}\nevent: campaign_event\ndata: {data}\n\n"
                        last_id = e.id  # type: ignore[assignment]
                else:
                    yield ": heartbeat\n\n"
            await asyncio.sleep(1.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{campaign_id}/messages")
def get_campaign_messages(
    campaign_id: int,
    session: Session = Depends(get_session),
    after_id: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    """CampaignMessage rows with cursor-based pagination."""
    stmt = (
        select(CampaignMessage)
        .where(
            CampaignMessage.campaign_id == campaign_id,
            CampaignMessage.id > after_id,  # type: ignore[operator]
        )
        .order_by(CampaignMessage.id.asc())  # type: ignore[arg-type]
        .limit(limit)
    )
    messages = list(session.exec(stmt).all())
    return {
        "messages": [_message_dict(m) for m in messages],
        "last_id": messages[-1].id if messages else after_id,
    }


@router.get("/{campaign_id}/messages/stream")
async def stream_campaign_messages(
    campaign_id: int,
    request: Request,
    after_id: int = Query(0, ge=0),
):
    """SSE endpoint tailing CampaignMessage rows for real-time comm log."""
    from decomp_agent.web.deps import get_state
    engine = get_state().engine

    async def message_generator():
        last_id = after_id
        while True:
            if await request.is_disconnected():
                break
            with Session(engine) as session:
                messages = list(session.exec(
                    select(CampaignMessage)
                    .where(
                        CampaignMessage.campaign_id == campaign_id,
                        CampaignMessage.id > last_id,  # type: ignore[operator]
                    )
                    .order_by(CampaignMessage.id.asc())  # type: ignore[arg-type]
                    .limit(50)
                ).all())
                if messages:
                    for m in messages:
                        data = json.dumps(_message_dict(m))
                        yield f"id: {m.id}\nevent: campaign_message\ndata: {data}\n\n"
                        last_id = m.id  # type: ignore[assignment]
                else:
                    yield ": heartbeat\n\n"
            await asyncio.sleep(1.5)

    return StreamingResponse(
        message_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{campaign_id}/timeline")
def get_campaign_timeline(
    campaign_id: int,
    session: Session = Depends(get_session),
):
    """Aggregated match% snapshots over time for replay.

    Returns events that changed match state, ordered chronologically.
    """
    events = list(session.exec(
        select(CampaignEvent)
        .where(
            CampaignEvent.campaign_id == campaign_id,
            CampaignEvent.event_type.in_(  # type: ignore[attr-defined]
                ["match_achieved", "worker_completed", "worker_started",
                 "worker_failed", "status_change"]
            ),
        )
        .order_by(CampaignEvent.id.asc())  # type: ignore[arg-type]
    ).all())

    # Also get task snapshots for match percentages
    tasks = list(session.exec(
        select(CampaignTask)
        .where(CampaignTask.campaign_id == campaign_id)
        .order_by(CampaignTask.id.asc())  # type: ignore[arg-type]
    ).all())

    return {
        "events": [_event_dict(e) for e in events],
        "tasks": [_task_dict(t) for t in tasks],
    }
