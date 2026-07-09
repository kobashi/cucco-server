"""Thin WebSocket wrapper shared by the Stub client and the Mock AI.

Handles only the transport-level concerns from docs/ai-client-guide.md §1:
connecting, the envelope format ({type, table_id, protocol_version,
payload, ts}), identify/create_table/join_table handshakes, and yielding
parsed server events. All *game* decisions live in the callers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator

import websockets

PROTOCOL_VERSION = "1.0"


@dataclass
class ServerEvent:
    type: str
    payload: dict
    table_id: str | None


class CuccoConnection:
    """One WebSocket connection speaking the cucco-server protocol."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._ws = None
        self.player_id: str | None = None
        self.session_token: str | None = None
        self.room_id: str | None = None

    async def __aenter__(self) -> "CuccoConnection":
        self._ws = await websockets.connect(self.url)
        return self

    async def __aexit__(self, *exc) -> None:
        await self._ws.close()

    async def send(self, type_: str, payload: dict | None = None) -> None:
        await self._ws.send(
            json.dumps(
                {
                    "type": type_,
                    "table_id": self.room_id,
                    "protocol_version": PROTOCOL_VERSION,
                    "payload": payload or {},
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            )
        )

    async def recv(self) -> ServerEvent:
        data = json.loads(await self._ws.recv())
        return ServerEvent(type=data["type"], payload=data.get("payload", {}), table_id=data.get("table_id"))

    async def events(self) -> AsyncIterator[ServerEvent]:
        while True:
            try:
                yield await self.recv()
            except websockets.ConnectionClosed:
                return

    # -- handshakes (docs/ai-client-guide.md §1) --------------------------------

    async def identify(self, name: str, player_type: str = "ai") -> None:
        await self.send("identify", {"name": name, "player_type": player_type})
        event = await self._expect("identified")
        self.player_id = event.payload["player_id"]
        # Keep the token: reconnection via join_table needs it (guide §1).
        self.session_token = event.payload["session_token"]

    async def create_table(self, config: dict | None = None) -> str:
        await self.send("create_table", config or {})
        event = await self._expect("table_created")
        self.room_id = event.payload["room_id"]
        return self.room_id

    async def join_table(self, room_id: str, *, session_token: str | None = None) -> ServerEvent:
        payload: dict = {"room_id": room_id}
        if session_token:
            payload["session_token"] = session_token
        await self.send("join_table", payload)
        snapshot = await self._expect("state_snapshot")
        self.room_id = room_id
        return snapshot

    async def _expect(self, type_: str) -> ServerEvent:
        event = await self.recv()
        if event.type == "action_rejected":
            raise RuntimeError(f"server rejected the request: {event.payload.get('reason')}")
        if event.type != type_:
            raise RuntimeError(f"expected {type_!r} from server, got {event.type!r}")
        return event
