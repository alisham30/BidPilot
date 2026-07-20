"""WebSocket hub — live run progress for the dashboard.

Graph nodes run in worker threads; `publish` is thread-safe and schedules the
broadcast onto the FastAPI event loop captured at startup.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import WebSocket

log = logging.getLogger("bidpilot.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: dict[str, set[WebSocket]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, run_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._clients[run_id].add(ws)

    def disconnect(self, run_id: str, ws: WebSocket) -> None:
        self._clients[run_id].discard(ws)

    async def _broadcast(self, run_id: str, payload: dict) -> None:
        message = json.dumps(payload, default=str)
        dead = []
        for ws in list(self._clients.get(run_id, [])):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(run_id, ws)

    def publish(self, run_id: str, payload: dict) -> None:
        """Thread-safe publish from graph worker threads."""
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(run_id, payload), self._loop)


manager = ConnectionManager()
