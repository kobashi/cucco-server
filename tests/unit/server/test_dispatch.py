import asyncio
import json

import pytest

from cucco.protocol.envelope import build_envelope
from cucco.server.dispatch import ConnectionHandler
from cucco.server.registry import TableRegistry


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
