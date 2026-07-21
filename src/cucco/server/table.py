"""One 卓 (Table/Room): one Game instance plus connection bookkeeping.

`Table` itself holds no orchestration logic (that's `cucco.server.runner`)
-- just the set of connected sessions, the table's configuration, and the
live `Game` once it's running.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

from cucco.domain.config import GameConfig
from cucco.domain.game import Game
from cucco.persistence.results_store import ResultsStore
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
    # Set by dispatch whenever any player's out-of-band cucco_declare arrives
    # (see PlayerSession.pending_cucco); the runner races its prompt waits
    # against this so a klop interrupts even mid-someone-else's think time.
    cucco_wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    # Shared server-wide persistence handles (docs/protocol/design.md
    # 「永続化・成績記録」), copied onto each Table at creation time so
    # dispatch.py's `_start_game` doesn't need them threaded through every
    # call site. None in tests that don't care about persistence.
    results_store: ResultsStore | None = None
    action_log_dir: Path | None = None
    # Evaluation-mode tables (docs/protocol/design.md 「AI専用高速評価
    # モード」) don't set `game` synchronously in dispatch._start_game --
    # EvaluationRunner assigns a fresh Game per game_count iteration from
    # inside its own task. This flag is the re-entry guard that `game`
    # itself serves for normal mode, so a redundant `ready` arriving after
    # _start_game has already scheduled the evaluation task (but before its
    # first Game exists) can't launch a second EvaluationRunner.
    evaluation_started: bool = False
    # Players who confirmed the current result screen (`result_ack`); the
    # runner's result pause ends early once every seated, connected player
    # is in here. Cleared at the start of each pause.
    result_acks: set[str] = field(default_factory=set)
    # Server-embedded AI opponents requested via create_table's `ai_players`
    # ((policy, count), ...). Spawned once, when the FIRST real session joins
    # -- not at creation, because the bots' immediate `ready` would auto-start
    # the game before the creator has even taken their seat (the all-ready
    # auto-start only counts joined players).
    pending_ai_players: tuple = ()
    bots_spawned: bool = False
    bot_tasks: list = field(default_factory=list)
    # Presentation-only preference from create_table (not a domain rule, so
    # it lives here rather than in GameConfig): "grouped" | "pile". Clients
    # read it from state_snapshot so the whole table renders one style.
    discard_display: str = "grouped"
    # Wall-clock bookkeeping for the admin surface: when the table was
    # created, and when it last saw any activity (a client action or a
    # broadcast). "How long has this table been idle" is the operator's
    # main signal for spotting stuck tables.
    created_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)
    # The running TableRunner/EvaluationRunner task, kept so the admin
    # surface can cancel a stuck game. None while no game is running.
    runner_task: asyncio.Task | None = None
    # When the table was first observed with NO connected real participant
    # (human/spectator/external-AI -- embedded bots don't count). Set and
    # measured by the GC sweep, NOT by last_activity_at: a bot-only table
    # that rematches forever keeps broadcasting, so its idle time never grows
    # -- "how long has nobody real been here" is the signal that actually
    # catches it. None while someone real is connected.
    real_absent_since: float | None = None

    def touch(self) -> None:
        self.last_activity_at = time.time()

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

    def effective_creator_id(self) -> str:
        """Who currently holds the organizer role (start_pot rights).

        The original creator, unless they are gone/disconnected -- then the
        earliest-joined connected player (dict insertion order = join order)
        inherits it, so a room is never stuck unable to start because its
        creator left. The role snaps back to the creator when they return;
        evaluated lazily on demand so a brief reload doesn't permanently
        bounce the role around.
        """
        creator = self.sessions.get(self.creator_id)
        if creator is not None and creator.connected:
            return self.creator_id
        for session in self.players():
            if session.connected:
                return session.player_id
        return self.creator_id
