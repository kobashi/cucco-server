"""Admin surface: list / status / abort / remove (transport-independent)."""

import asyncio
import json

import pytest

from cucco.protocol.envelope import build_envelope
from cucco.server.admin import handle_admin_request
from cucco.server.dispatch import ConnectionHandler
from cucco.server.registry import TableRegistry

TOKEN = "test-token"


class FakeConnection:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def last(self, type_: str) -> dict | None:
        for data in reversed(self.sent):
            if data["type"] == type_:
                return data
        return None


async def _make_waiting_table(registry, *, ai_players=None):
    conn = FakeConnection()
    handler = ConnectionHandler(conn, registry)
    await handler.handle_message(build_envelope("identify", {"name": "Host", "player_type": "human"}))
    payload = {"ai_players": ai_players} if ai_players else {}
    await handler.handle_message(build_envelope("create_table", payload))
    room_id = conn.last("table_created")["payload"]["room_id"]
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    for _ in range(10):
        await asyncio.sleep(0)
    return conn, room_id


@pytest.mark.asyncio
async def test_bad_token_is_rejected():
    registry = TableRegistry()
    reply = await handle_admin_request(registry, TOKEN, {"token": "wrong", "action": "list_tables"})
    assert reply == {"ok": False, "error": "invalid admin token"}


@pytest.mark.asyncio
async def test_list_and_status():
    registry = TableRegistry()
    _conn, room_id = await _make_waiting_table(registry, ai_players=[{"policy": "matrix", "count": 1}])
    reply = await handle_admin_request(registry, TOKEN, {"token": TOKEN, "action": "list_tables"})
    assert reply["ok"] and len(reply["tables"]) == 1
    summary = reply["tables"][0]
    assert summary["room_id"] == room_id
    assert summary["players"] == 2 and summary["bots"] == 1 and summary["game_active"] is False
    assert summary["idle_sec"] >= 0

    status = await handle_admin_request(registry, TOKEN, {"token": TOKEN, "action": "table_status", "room_id": room_id})
    assert status["ok"]
    names = {s["name"]: s for s in status["sessions"]}
    assert names["AI-matrix-1"]["ai_policy"] == "matrix"
    assert status["snapshot"]["table_id"] == room_id
    assert "your_hand" in status["snapshot"]  # spectator view (None -- no hands leaked)
    assert status["snapshot"]["your_hand"] is None


@pytest.mark.asyncio
async def test_abort_ends_a_running_bot_game_and_unregisters():
    registry = TableRegistry()
    # A spectator host + 2 bots: the bots auto-start and would rematch
    # forever -- exactly the stuck table the admin abort exists for.
    conn = FakeConnection()
    handler = ConnectionHandler(conn, registry)
    await handler.handle_message(build_envelope("identify", {"name": "Watcher", "player_type": "spectator"}))
    await handler.handle_message(
        build_envelope("create_table", {"ai_players": [{"policy": "always_no_change", "count": 2}], "starting_chips": 25})
    )
    room_id = conn.last("table_created")["payload"]["room_id"]
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    table = registry.get(room_id)

    async def wait_running():
        while table.game is None:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait_running(), timeout=10)

    reply = await handle_admin_request(registry, TOKEN, {"token": TOKEN, "action": "abort_table", "room_id": room_id})
    assert reply["ok"] and reply["aborted"] == room_id
    assert len(reply["ranking"]) == 2
    # The spectator (like every session) received a normal game_ended.
    assert conn.last("game_ended") is not None
    # The room is gone and its background tasks are dead.
    assert registry.get(room_id) is None
    assert table.runner_task.done()
    assert all(t.done() for t in table.bot_tasks)


@pytest.mark.asyncio
async def test_remove_refuses_a_running_game_but_sweeps_a_waiting_table():
    registry = TableRegistry()
    conn, room_id = await _make_waiting_table(registry)
    # Waiting table: remove works and the host is notified.
    reply = await handle_admin_request(registry, TOKEN, {"token": TOKEN, "action": "remove_table", "room_id": room_id})
    assert reply["ok"] and registry.get(room_id) is None
    assert any("管理者" in d["payload"].get("reason", "") for d in conn.sent if d["type"] == "action_rejected")

    # Unknown room after removal.
    reply2 = await handle_admin_request(registry, TOKEN, {"token": TOKEN, "action": "remove_table", "room_id": room_id})
    assert not reply2["ok"]


@pytest.mark.asyncio
async def test_unknown_action_and_unknown_room():
    registry = TableRegistry()
    _conn, room_id = await _make_waiting_table(registry)
    bad_action = await handle_admin_request(registry, TOKEN, {"token": TOKEN, "action": "explode", "room_id": room_id})
    assert not bad_action["ok"] and "unknown admin action" in bad_action["error"]
    bad_room = await handle_admin_request(registry, TOKEN, {"token": TOKEN, "action": "table_status", "room_id": "ZZZZZZ"})
    assert not bad_room["ok"]
