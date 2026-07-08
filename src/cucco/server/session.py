"""A connected client: identity plus however it sends/receives messages.

`Connection` is a minimal Protocol (just `send`) so tests can supply a fake
in-memory connection instead of a real WebSocket -- `cucco.server.app` is
the only module that needs to know about `websockets` itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

PlayerType = str  # "human" | "ai" | "spectator"


class Connection(Protocol):
    async def send(self, message: str) -> None: ...


@dataclass
class PlayerSession:
    player_id: str
    name: str
    player_type: PlayerType
    session_token: str
    connection: Connection | None = None
    connected: bool = True
    room_id: str | None = None
    # Incoming parsed actions land here; the runner awaits from this queue
    # when it needs a specific response from this player.
    inbox: "asyncio.Queue" = field(default_factory=asyncio.Queue)

    async def send(self, message: str) -> None:
        if self.connection is not None and self.connected:
            await self.connection.send(message)

    def is_ai(self) -> bool:
        return self.player_type == "ai"

    def is_spectator(self) -> bool:
        return self.player_type == "spectator"
