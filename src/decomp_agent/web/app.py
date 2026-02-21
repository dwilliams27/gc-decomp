"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from decomp_agent.web.deps import AppState, set_state
from decomp_agent.web.routers import batch, config_api, functions, stats
from decomp_agent.web.ws import get_broadcaster, install_broadcaster, ws_endpoint


def create_app(config_path: Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    state = AppState(config_path)
    set_state(state)

    app = FastAPI(
        title="decomp-agent",
        description="Web UI for automated Melee decompilation",
        version="0.1.0",
    )

    # CORS for Vite dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    app.include_router(functions.router)
    app.include_router(stats.router)
    app.include_router(batch.router)
    app.include_router(config_api.router)

    # WebSocket
    app.websocket("/ws/events")(ws_endpoint)

    @app.on_event("startup")
    async def _startup() -> None:
        broadcaster = get_broadcaster()
        broadcaster.set_loop(asyncio.get_running_loop())
        state.broadcaster = broadcaster

        # Install as a stdlib logging handler — works regardless of
        # structlog configuration order or logger caching.
        install_broadcaster(broadcaster)

    # Serve built frontend if it exists
    dist_dir = Path(__file__).parents[3] / "web-ui" / "dist"
    if dist_dir.is_dir():
        from fastapi.responses import FileResponse

        @app.get("/")
        async def _serve_index():
            return FileResponse(dist_dir / "index.html")

        app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")

        @app.get("/{path:path}")
        async def _spa_fallback(path: str):
            if path.startswith("api/") or path.startswith("ws/"):
                from fastapi import HTTPException

                raise HTTPException(status_code=404)
            file_path = dist_dir / path
            if file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(dist_dir / "index.html")

    return app
