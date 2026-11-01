"""A WebSocket client for an ASGI app, with no socket: the app runs in the caller's event loop. Used by the service
tests and by scripts/gen_api_examples (httpx's ASGITransport does not speak WebSocket)."""
from __future__ import annotations

import asyncio
import json


class ASGIWebSocket:
    def __init__(self, app, headers: dict[str, str] | None = None, path: str = "/run/stream", timeout: float = 5.0):
        h = [(b"host", b"127.0.0.1")] + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        self.scope = {"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "path": path,
                      "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": h,
                      "subprotocols": [], "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 50000)}
        self.app, self.timeout = app, timeout
        self.to_app: asyncio.Queue = asyncio.Queue()
        self.from_app: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self):
        self.task = asyncio.ensure_future(self.app(self.scope, self.to_app.get, self.from_app.put))
        await self.to_app.put({"type": "websocket.connect"})
        self.first = await asyncio.wait_for(self.from_app.get(), self.timeout)
        self.accepted = self.first["type"] == "websocket.accept"
        return self

    async def __aexit__(self, *exc):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def send(self, obj) -> None:
        await self.to_app.put({"type": "websocket.receive", "text": obj if isinstance(obj, str) else json.dumps(obj)})

    async def recv(self) -> dict:
        """The next frame as a dict; the server's close is {"type": "websocket.close", "code": ...}."""
        m = await asyncio.wait_for(self.from_app.get(), self.timeout)
        return m if m["type"] == "websocket.close" else json.loads(m["text"])

    async def leave(self) -> None:
        await self.to_app.put({"type": "websocket.disconnect", "code": 1001})

    async def frames(self) -> list[dict]:
        """Everything up to and including the server's close."""
        out = []
        while True:
            out.append(await self.recv())
            if out[-1].get("type") == "websocket.close":
                return out
