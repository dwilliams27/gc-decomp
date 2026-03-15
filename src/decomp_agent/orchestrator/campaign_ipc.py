"""Host-owned campaign IPC for containerized orchestrators.

Containerized manager agents must not write directly to the host SQLite DB.
They issue campaign tool requests by writing JSON files into a shared IPC
directory, and the host campaign orchestrator process services those requests.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from decomp_agent.config import Config
from decomp_agent.orchestrator.campaign import (
    append_campaign_note,
    create_campaign_worker_task,
    format_campaign_status,
    format_campaign_task_result,
    get_campaign_notes,
    run_campaign_next_task_summary,
    retry_campaign_task,
)


def get_campaign_ipc_root(config: Config | None = None) -> Path:
    """Return the shared campaign IPC root."""
    env_value = os.environ.get("CAMPAIGN_IPC_DIR")
    if env_value:
        return Path(env_value)
    if config is not None:
        return config.campaign.root_dir / "ipc"
    return Path("/tmp/decomp-campaigns/ipc")


def _campaign_ipc_requests_dir(root: Path) -> Path:
    return root / "requests"


def _campaign_ipc_responses_dir(root: Path) -> Path:
    return root / "responses"


def ensure_campaign_ipc_dirs(root: Path) -> None:
    _campaign_ipc_requests_dir(root).mkdir(parents=True, exist_ok=True)
    _campaign_ipc_responses_dir(root).mkdir(parents=True, exist_ok=True)


def submit_campaign_ipc_request(
    tool_name: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 0.1,
    root: Path | None = None,
) -> str:
    """Submit a request from a containerized manager and wait for the host response."""
    ipc_root = root or get_campaign_ipc_root()
    ensure_campaign_ipc_dirs(ipc_root)
    request_id = uuid.uuid4().hex
    request_path = _campaign_ipc_requests_dir(ipc_root) / f"{request_id}.json"
    response_path = _campaign_ipc_responses_dir(ipc_root) / f"{request_id}.json"
    tmp_request_path = request_path.with_suffix(".json.tmp")
    tmp_request_path.write_text(
        json.dumps(
            {
                "request_id": request_id,
                "tool": tool_name,
                "payload": payload,
                "created_at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    tmp_request_path.replace(request_path)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if response_path.exists():
            raw = response_path.read_text(encoding="utf-8")
            response_path.unlink(missing_ok=True)
            data = json.loads(raw)
            if data.get("ok"):
                return str(data.get("result", ""))
            raise RuntimeError(str(data.get("error", "unknown campaign IPC error")))
        time.sleep(poll_seconds)

    raise TimeoutError(
        f"Timed out waiting for host campaign response to {tool_name} ({request_id})"
    )


def _emit_ipc_event(engine: Engine, tool_name: str, payload: dict[str, Any]) -> None:
    """Emit a tool_call CampaignEvent for an IPC request."""
    from decomp_agent.models.db import emit_campaign_event
    campaign_id = payload.get("campaign_id")
    if campaign_id is None:
        return
    try:
        from sqlmodel import Session as _Session
        with _Session(engine) as session:
            emit_campaign_event(
                session, int(campaign_id), "tool_call",
                {"tool": tool_name, "payload": payload},
            )
    except Exception:
        pass  # Don't let event emission break IPC dispatch


def _dispatch_campaign_ipc_request(
    engine: Engine,
    config: Config,
    *,
    tool_name: str,
    payload: dict[str, Any],
) -> str:
    _emit_ipc_event(engine, tool_name, payload)
    if tool_name == "campaign_get_status":
        return format_campaign_status(engine, config, int(payload["campaign_id"]))
    if tool_name == "campaign_get_task_result":
        return format_campaign_task_result(
            engine,
            config,
            int(payload["campaign_id"]),
            int(payload["task_id"]),
        )
    if tool_name == "campaign_launch_worker":
        task = create_campaign_worker_task(
            engine,
            campaign_id=int(payload["campaign_id"]),
            function_name=str(payload["function_name"]),
            provider=str(payload.get("provider", "")),
            instructions=str(payload.get("instructions", "")),
            priority=int(payload["priority"]) if payload.get("priority") is not None else None,
            scope=str(payload.get("scope", "function")),
        )
        return (
            f"Queued campaign task #{task.id} for {task.function_name} "
            f"(provider={task.provider or 'default'}, priority={task.priority}, scope={task.scope})"
        )
    if tool_name == "campaign_retry_task":
        task = retry_campaign_task(
            engine,
            campaign_id=int(payload["campaign_id"]),
            task_id=int(payload["task_id"]),
            provider=str(payload.get("provider", "")),
            instructions=str(payload.get("instructions", "")),
            priority=int(payload["priority"]) if payload.get("priority") is not None else None,
        )
        return (
            f"Queued retry task #{task.id} for {task.function_name or task.scope} "
            f"(provider={task.provider or 'default'}, priority={task.priority})"
        )
    if tool_name == "campaign_run_next_task":
        return run_campaign_next_task_summary(
            engine,
            config,
            campaign_id=int(payload["campaign_id"]),
        )
    if tool_name == "campaign_write_note":
        path = append_campaign_note(
            engine,
            int(payload["campaign_id"]),
            str(payload["note"]),
        )
        return f"Wrote manager note for campaign #{payload['campaign_id']} to {path}"
    if tool_name == "campaign_get_notes":
        return get_campaign_notes(engine, int(payload["campaign_id"]))
    raise ValueError(f"Unsupported campaign IPC tool '{tool_name}'")


def process_pending_campaign_ipc_requests(
    engine: Engine,
    config: Config,
    *,
    root: Path | None = None,
) -> int:
    """Process all pending manager requests from the shared IPC directory."""
    ipc_root = root or get_campaign_ipc_root(config)
    ensure_campaign_ipc_dirs(ipc_root)
    processed = 0
    for request_path in sorted(_campaign_ipc_requests_dir(ipc_root).glob("*.json")):
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            response: dict[str, Any]
            try:
                result = _dispatch_campaign_ipc_request(
                    engine,
                    config,
                    tool_name=str(request["tool"]),
                    payload=dict(request.get("payload", {})),
                )
                response = {"ok": True, "result": result}
            except Exception as exc:
                response = {"ok": False, "error": str(exc)}
            response_path = _campaign_ipc_responses_dir(ipc_root) / request_path.name
            tmp_response_path = response_path.with_suffix(".json.tmp")
            tmp_response_path.write_text(json.dumps(response), encoding="utf-8")
            tmp_response_path.replace(response_path)
            processed += 1
        finally:
            request_path.unlink(missing_ok=True)
    return processed


@contextmanager
def campaign_ipc_service(engine: Engine, config: Config):
    """Serve campaign IPC requests while a containerized manager session is running."""
    import threading

    stop_event = threading.Event()

    def _serve() -> None:
        while not stop_event.is_set():
            process_pending_campaign_ipc_requests(engine, config)
            stop_event.wait(0.1)
        process_pending_campaign_ipc_requests(engine, config)

    thread = threading.Thread(target=_serve, daemon=True, name="campaign-ipc-service")
    thread.start()
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=2.0)
