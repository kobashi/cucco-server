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
    # The prompt the runner is currently awaiting from this player, if any:
    # {"type": wire event type, "payload": dict, "deadline": loop-time}.
    # A reconnect re-sends it (with the remaining time) -- the original went
    # to the now-dead connection, so without this a player who reloads
    # mid-turn just stares at a promptless screen until the server times
    # them out.
    outstanding_prompt: dict | None = None

    async def send(self, message: str) -> None:
        if self.connection is not None and self.connected:
            await self.connection.send(message)

    def is_ai(self) -> bool:
        return self.player_type == "ai"

    def is_spectator(self) -> bool:
        return self.player_type == "spectator"
