import asyncio
import json
import random

import pytest

from cucco.domain.config import GameConfig
from cucco.domain.game import Game
from cucco.protocol.envelope import build_envelope
from cucco.server.dispatch import ConnectionHandler, _start_game
from cucco.server.registry import TableRegistry
from cucco.server.runner import TableRunner
from cucco.server.session import PlayerSession
from cucco.server.table import Table


class FakeConnection:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


@pytest.mark.asyncio
async def test_identify_returns_session_token():
    handler = ConnectionHandler(FakeConnection(), TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    assert handler.session is not None
    assert handler.session.name == "Alice"
    reply = handler.connection.sent[0]
    assert reply["type"] == "identified"
    assert reply["payload"]["player_id"] == handler.session.player_id
    assert reply["payload"]["session_token"] == handler.session.session_token


@pytest.mark.asyncio
async def test_create_table_before_identify_is_rejected():
    handler = ConnectionHandler(FakeConnection(), TableRegistry())
    await handler.handle_message(build_envelope("create_table", {}))
    assert handler.connection.sent[0]["type"] == "action_rejected"


@pytest.mark.asyncio
async def test_create_table_with_evaluation_mode_is_rejected_until_implemented():
    handler = ConnectionHandler(FakeConnection(), TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "ai"}))
    await handler.handle_message(build_envelope("create_table", {"mode": "evaluation", "game_count": 10}))
    assert handler.connection.sent[-1]["type"] == "action_rejected"


@pytest.mark.asyncio
async def test_join_unknown_room_is_rejected():
    handler = ConnectionHandler(FakeConnection(), TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await handler.handle_message(build_envelope("join_table", {"room_id": "NOPE99"}))
    assert handler.connection.sent[-1]["type"] == "action_rejected"


@pytest.mark.asyncio
async def test_reconnect_with_session_token_restores_your_hand():
    registry = TableRegistry()

    creator = ConnectionHandler(FakeConnection(), registry)
    await creator.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "ai"}))
    await creator.handle_message(
        build_envelope("create_table", {"starting_chips": 25})
    )
    room_id = next(m for m in creator.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await creator.handle_message(build_envelope("join_table", {"room_id": room_id}))
    creator_token = creator.session.session_token
    creator_id = creator.session.player_id

    second = ConnectionHandler(FakeConnection(), registry)
    await second.handle_message(build_envelope("identify", {"name": "Bob", "player_type": "ai"}))
    await second.handle_message(build_envelope("join_table", {"room_id": room_id}))

    # Simulate a disconnect: mark not connected, then reconnect with a
    # brand-new ConnectionHandler using the saved session_token.
    table = registry.get(room_id)
    table.get(creator_id).connected = False

    reconnecting = ConnectionHandler(FakeConnection(), registry)
    await reconnecting.handle_message(
        build_envelope("join_table", {"room_id": room_id, "session_token": creator_token})
    )
    # No identify was needed -- the token alone re-binds the existing session.
    assert reconnecting.session is not None
    assert reconnecting.session.player_id == creator_id
    assert reconnecting.session.connected is True
    snapshot = next(m for m in reconnecting.connection.sent if m["type"] == "state_snapshot")
    assert snapshot["payload"]["table_id"] == room_id


@pytest.mark.asyncio
async def test_spectator_cannot_declare_ready():
    registry = TableRegistry()
    handler = ConnectionHandler(FakeConnection(), registry)
    await handler.handle_message(build_envelope("identify", {"name": "Watcher", "player_type": "spectator"}))
    await handler.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in handler.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    await handler.handle_message(build_envelope("ready", {}))
    assert handler.connection.sent[-1]["type"] == "action_rejected"


@pytest.mark.asyncio
async def test_ready_timeout_starts_game_with_only_the_players_who_readied():
    table = Table(room_id="ABC123", config=GameConfig(), creator_id="p1")
    for pid in ("p1", "p2", "p3"):
        table.add_session(PlayerSession(player_id=pid, name=pid, player_type="ai", session_token=pid, connection=FakeConnection()))
    # Only 2 of the 3 seated players readied up before the lobby-wide
    # timeout fires -- the watchdog must start the game with just them
    # rather than waiting forever for the third.
    table.ready_ids = {"p1", "p2"}

    await _start_game(table)

    assert table.game is not None
    assert set(table.game.seats) == {"p1", "p2"}


@pytest.mark.asyncio
async def test_ready_timeout_with_too_few_players_resets_for_a_retry():
    table = Table(room_id="ABC123", config=GameConfig(), creator_id="p1")
    table.add_session(PlayerSession(player_id="p1", name="p1", player_type="ai", session_token="p1", connection=FakeConnection()))
    table.ready_ids = set()  # nobody readied up in time

    await _start_game(table)

    assert table.game is None
    assert table.ready_ids == set()


@pytest.mark.asyncio
async def test_force_end_fires_when_too_few_connected_players_remain_to_start_a_pot():
    config = GameConfig()
    table = Table(room_id="ABC123", config=config, creator_id="p1")
    conns = {pid: FakeConnection() for pid in ("p1", "p2")}
    for pid, conn in conns.items():
        table.add_session(PlayerSession(player_id=pid, name=pid, player_type="ai", session_token=pid, connection=conn))
    game = Game(["p1", "p2"], config, random.Random(0))
    table.game = game
    game.start_first_pot()
    table.get("p2").connected = False  # p2 dropped before this pot could run

    runner = TableRunner(table)
    await runner._run_pot(game)

    assert game.is_finished
    assert any(m["type"] == "game_ended" for m in conns["p1"].sent)
