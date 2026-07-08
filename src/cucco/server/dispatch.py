"""Per-connection receive loop: parse an incoming message, validate it, and
either handle it directly (identify/create_table/join_table/ready) or hand
it to the running TableRunner via the session's inbox queue.

Note: `ready` currently gates only the table's FIRST pot. Once a game is
running, later pots use the domain layer's built-in auto-revival (every
seat rejoins automatically -- docs/rules/final_rules.md "次のポットへの
参加"); per-pot re-ready-gating would require extending the domain Game
API and is a known simplification of this implementation.
"""

from __future__ import annotations

import asyncio
import random
import uuid

from cucco.domain.errors import IllegalAction
from cucco.domain.game import Game
from cucco.protocol.actions import (
    CambioDeclare,
    ContinueDeclare,
    CreateTable,
    CuccoDeclare,
    CuccoPass,
    DealerReady,
    Identify,
    JoinTable,
    NoChangeDeclare,
    Ready,
    create_table_to_config,
    parse_action,
)
from cucco.protocol.envelope import build_envelope, check_protocol_version, parse_envelope
from cucco.protocol.errors import ProtocolError
from cucco.server.registry import MAX_PLAYERS_PER_TABLE, MAX_SPECTATORS_PER_TABLE, TableRegistry
from cucco.server.runner import TableRunner, build_state_snapshot
from cucco.server.session import Connection, PlayerSession
from cucco.server.table import Table

QUEUE_ROUTED = (DealerReady, CambioDeclare, NoChangeDeclare, CuccoDeclare, CuccoPass, ContinueDeclare)


class ConnectionHandler:
    def __init__(self, connection: Connection, registry: TableRegistry) -> None:
        self.connection = connection
        self.registry = registry
        self.session: PlayerSession | None = None
        self.table: Table | None = None

    async def handle_message(self, raw: str) -> None:
        try:
            envelope = parse_envelope(raw)
            check_protocol_version(envelope)
            action = parse_action(envelope)
        except ProtocolError as exc:
            await self._send_raw("action_rejected", {"reason": str(exc)})
            return

        try:
            if isinstance(action, Identify):
                await self._handle_identify(action)
            elif isinstance(action, CreateTable):
                await self._handle_create_table(action)
            elif isinstance(action, JoinTable):
                await self._handle_join_table(action)
            elif isinstance(action, Ready):
                await self._handle_ready()
            elif isinstance(action, QUEUE_ROUTED):
                await self._route_to_inbox(action)
            else:
                raise ProtocolError(f"unhandled action type: {type(action).__name__}")
        except (ProtocolError, IllegalAction) as exc:
            await self._send_raw("action_rejected", {"reason": str(exc)})

    async def _send_raw(self, type_: str, payload: dict) -> None:
        table_id = self.table.room_id if self.table else None
        await self.connection.send(build_envelope(type_, payload, table_id=table_id))

    async def _handle_identify(self, action: Identify) -> None:
        player_id = uuid.uuid4().hex
        session_token = uuid.uuid4().hex
        self.session = PlayerSession(
            player_id=player_id,
            name=action.name,
            player_type=action.player_type,
            session_token=session_token,
            connection=self.connection,
        )
        await self._send_raw("identified", {"player_id": player_id, "session_token": session_token})

    async def _handle_create_table(self, action: CreateTable) -> None:
        if self.session is None:
            raise ProtocolError("must identify before create_table")
        config = create_table_to_config(action)
        table = Table(room_id="", config=config, creator_id=self.session.player_id)
        room_id = self.registry.register(table)
        table.room_id = room_id
        await self._send_raw("table_created", {"room_id": room_id})

    async def _handle_join_table(self, action: JoinTable) -> None:
        table = self.registry.get(action.room_id)
        if table is None:
            raise ProtocolError(f"no such table: {action.room_id!r}")
        self.table = table

        # Reconnection: a session_token alone re-binds the existing session
        # to this connection, no prior `identify` on this connection needed.
        if action.session_token:
            existing = next((s for s in table.sessions.values() if s.session_token == action.session_token), None)
            if existing is None:
                raise ProtocolError("invalid session_token")
            existing.connection = self.connection
            existing.connected = True
            self.session = existing
            await self._send_raw("state_snapshot", build_state_snapshot(table, existing.player_id))
            return

        if self.session is None:
            raise ProtocolError("must identify before join_table (unless reconnecting with session_token)")
        if self.session.player_type != "spectator" and len(table.players()) >= MAX_PLAYERS_PER_TABLE:
            raise ProtocolError("table is full")
        if self.session.player_type == "spectator" and len(table.spectators()) >= MAX_SPECTATORS_PER_TABLE:
            raise ProtocolError("too many spectators")

        self.session.room_id = table.room_id
        table.add_session(self.session)
        await self._send_raw("state_snapshot", build_state_snapshot(table, self.session.player_id))

    async def _handle_ready(self) -> None:
        if self.session is None or self.table is None:
            raise ProtocolError("must join_table before ready")
        if self.session.player_type == "spectator":
            raise ProtocolError("spectators cannot declare ready")
        table = self.table
        if table.game is not None:
            return  # first pot already started; later pots auto-include everyone
        table.ready_ids.add(self.session.player_id)
        if len(table.ready_ids) < max(table.min_players, len(table.players())):
            return
        if len(table.players()) < table.min_players:
            return
        table.game = Game(table.player_ids(), table.config, random.Random())
        asyncio.create_task(TableRunner(table).run())

    async def _route_to_inbox(self, action) -> None:
        if self.session is None or self.table is None:
            raise ProtocolError("must join_table first")
        self.session.inbox.put_nowait(action)

    async def on_disconnect(self) -> None:
        if self.session is not None:
            self.session.connected = False
