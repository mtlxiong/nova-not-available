"""Fan-out of pipeline messages to WebSocket clients, bridged from worker threads."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket

log = logging.getLogger("echo.ws")


@dataclass(eq=False)
class _Client:
    ws: WebSocket
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=128))
    wants_frames: bool = True
    dropped: int = 0


class Hub:
    def __init__(self) -> None:
        self._clients: set[_Client] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def any_wants_frames(self) -> bool:
        return any(c.wants_frames for c in self._clients)

    # Thread-safe entry points for the pipeline.
    def publish_json(self, text: str) -> None:
        if self._loop is not None and self._clients:
            self._loop.call_soon_threadsafe(self._fanout, ("text", text))

    def publish_frame(self, data: bytes) -> None:
        if self._loop is not None and data and self._clients:
            self._loop.call_soon_threadsafe(self._fanout, ("bytes", data))

    def _fanout(self, message: tuple[str, object]) -> None:
        for client in list(self._clients):
            if message[0] == "bytes" and not client.wants_frames:
                continue
            q = client.queue
            if q.full():
                # Slow client: drop its oldest message rather than block the pipeline.
                try:
                    q.get_nowait()
                    client.dropped += 1
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(message)

    async def serve(self, ws: WebSocket, initial: list[str]) -> None:
        await ws.accept()
        client = _Client(ws)
        self._clients.add(client)
        sender = asyncio.create_task(self._send_loop(client))
        try:
            for text in initial:
                client.queue.put_nowait(("text", text))
            while True:
                msg = await ws.receive_json()
                if isinstance(msg, dict) and msg.get("type") == "client.options":
                    client.wants_frames = bool(msg.get("frames", True))
        except Exception:
            pass
        finally:
            self._clients.discard(client)
            sender.cancel()

    async def _send_loop(self, client: _Client) -> None:
        try:
            while True:
                kind, payload = await client.queue.get()
                if kind == "text":
                    await client.ws.send_text(payload)  # type: ignore[arg-type]
                else:
                    await client.ws.send_bytes(payload)  # type: ignore[arg-type]
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("websocket send failed; client will be dropped")
