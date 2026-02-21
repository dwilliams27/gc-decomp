"""WebSocket handler and log broadcaster.

Uses a stdlib logging.Handler to intercept ALL log records — this works
regardless of when structlog was configured or whether loggers are cached.
The handler extracts structlog event_dict from the LogRecord and broadcasts
it as JSON to connected WebSocket clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


class WebSocketBroadcaster(logging.Handler):
    """stdlib logging Handler that broadcasts log events to WebSocket clients.

    structlog's ProcessorFormatter attaches the processed event_dict to
    the LogRecord as `_logger`, `_name`, and the formatted msg. We can
    also access structlog keys that were embedded in the record.

    This approach works regardless of structlog's cache_logger_on_first_use
    because it intercepts at the stdlib logging level, after all structlog
    processors have run.
    """

    def __init__(self) -> None:
        super().__init__()
        self._connections: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        log.info("WebSocket client connected (%d total)", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        log.info("WebSocket client disconnected (%d total)", len(self._connections))

    def emit(self, record: logging.LogRecord) -> None:
        """Called by stdlib logging for every log record."""
        if not self._connections or self._loop is None:
            return

        # Skip noisy internal events
        if record.name in ("uvicorn.access", "uvicorn.error", "httpx", "openai", "httpcore"):
            return
        # Skip our own messages to avoid loops
        if record.name == __name__:
            return

        msg: dict[str, Any] = {"type": "agent_event", "ts": time.time()}

        # structlog's wrap_for_formatter stores event_dict as a tuple:
        # record.msg = (event_dict,), record.args = {"extra": ...}
        # Extract the event_dict from this wrapper format.
        event_dict: dict[str, Any] | None = None
        if isinstance(record.msg, tuple) and len(record.msg) == 1 and isinstance(record.msg[0], dict):
            event_dict = record.msg[0]
        elif isinstance(record.msg, dict):
            event_dict = record.msg

        if event_dict is not None:
            for key, value in event_dict.items():
                if key in ("_logger", "_name", "_record", "logger"):
                    continue
                try:
                    json.dumps(value)
                    msg[key] = value
                except (TypeError, ValueError):
                    msg[key] = str(value)
        else:
            # Plain stdlib log
            msg["event"] = record.getMessage()
            msg["level"] = record.levelname.lower()
            msg["logger_name"] = record.name

        for ws in list(self._connections):
            try:
                asyncio.run_coroutine_threadsafe(ws.send_json(msg), self._loop)
            except Exception:
                self._connections.discard(ws)


# Module-level singleton
_broadcaster = WebSocketBroadcaster()


def get_broadcaster() -> WebSocketBroadcaster:
    return _broadcaster


def install_broadcaster(broadcaster: WebSocketBroadcaster) -> None:
    """Add the broadcaster as a handler on the root logger.

    This is the most reliable integration point — it works regardless of
    structlog configuration, caching, or initialization order.
    """
    root = logging.getLogger()
    # Avoid double-adding
    if broadcaster not in root.handlers:
        root.addHandler(broadcaster)


async def ws_endpoint(ws: WebSocket) -> None:
    """WebSocket endpoint handler for /ws/events."""
    broadcaster = get_broadcaster()
    await broadcaster.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_json({"type": "pong", "ts": time.time()})
    except WebSocketDisconnect:
        broadcaster.disconnect(ws)
    except Exception:
        broadcaster.disconnect(ws)
