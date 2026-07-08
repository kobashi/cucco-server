"""One 卓 (Table/Room): one Game instance plus connection bookkeeping.

`Table` itself holds no orchestration logic (that's `cucco.server.runner`)
-- just the set of connected sessions, the table's configuration, and the
live `Game` once it's running.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from cucco.domain.config import GameConfig
from cucco.domain.game import Game
from cucco.server.session import PlayerSession


@dataclass
class Table:
    room_id: str
    config: GameConfig
    creator_id: str
    sessions: dict[str, PlayerSession] = field(default_factory=dict)
    game: Game | None = None
    finished: bool = False
    ready_ids: set[str] = field(default_factory=set)
    min_players: int = 2
    ready_deadline_task: asyncio.Task | None = None

    def add_session(self, session: PlayerSession) -> None:
        self.sessions[session.player_id] = session

    def players(self) -> list[PlayerSession]:
        return [s for s in self.sessions.values() if s.player_type != "spectator"]

    def player_ids(self) -> list[str]:
        return [s.player_id for s in self.players()]

    def spectators(self) -> list[PlayerSession]:
        return [s for s in self.sessions.values() if s.player_type == "spectator"]

    def get(self, player_id: str) -> PlayerSession | None:
        return self.sessions.get(player_id)
