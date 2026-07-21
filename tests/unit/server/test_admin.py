"""Admin surface: list / status / abort / remove / GC (transport-independent)."""

import asyncio
import json
import time

import pytest

from cucco.domain.config import GameConfig
from cucco.protocol.envelope import build_envelope
from cucco.server.admin import (
    GC_EMPTY_GRACE_SEC,
    GC_FINISHED_GRACE_SEC,
    _shutdown_table,
    handle_admin_request,
    real_participant_connected,
    sweep_gc,
)
from cucco.server.dispatch import ConnectionHandler
from cucco.server.registry import TableRegistry
from cucco.server.session import PlayerSession
from cucco.server.table import Table

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


# -- automatic garbage collection ------------------------------------------------


def _session(pid, ptype="human", *, connected=True, ai_policy=None):
    return PlayerSession(
        player_id=pid, name=pid, player_type=ptype, session_token=pid + "-tok",
        connection=None, connected=connected, ai_policy=ai_policy,
    )


def _register(registry, sessions, *, room="R1", finished=False, created=0.0, last_activity=0.0):
    table = Table(room_id=room, config=GameConfig(), creator_id="c")
    for s in sessions:
        table.add_session(s)
    table.finished = finished
    table.created_at = created
    table.last_activity_at = last_activity
    registry._tables[room] = table
    return table


def test_real_participant_connected_ignores_only_bots():
    assert real_participant_connected(_register(TableRegistry(), [_session("h")]))
    assert real_participant_connected(_register(TableRegistry(), [_session("sp", "spectator")]))
    # An external AI client (no policy tag) counts as a real occupant.
    assert real_participant_connected(_register(TableRegistry(), [_session("ext", "ai")]))
    # Only embedded bots connected, or the sole human disconnected: nobody real.
    assert not real_participant_connected(_register(TableRegistry(), [_session("b1", "ai", ai_policy="matrix")]))
    assert not real_participant_connected(
        _register(TableRegistry(), [_session("h", connected=False), _session("b1", "ai", ai_policy="matrix")])
    )


def test_sweep_keeps_an_occupied_table():
    reg = TableRegistry()
    table = _register(reg, [_session("h"), _session("b1", "ai", ai_policy="matrix")])
    assert sweep_gc(reg, now=1_000_000) == []
    assert table.real_absent_since is None


def test_sweep_removes_a_bot_only_table_after_the_empty_grace():
    reg = TableRegistry()
    # A watcher who left: their session is disconnected, only a bot remains.
    table = _register(reg, [_session("watcher", "spectator", connected=False), _session("b1", "ai", ai_policy="matrix")])
    now = 1_000_000.0
    assert sweep_gc(reg, now=now) == []  # first sight: start the timer
    assert table.real_absent_since == now
    assert sweep_gc(reg, now=now + GC_EMPTY_GRACE_SEC - 1) == []  # still within grace
    assert sweep_gc(reg, now=now + GC_EMPTY_GRACE_SEC + 1) == [table]  # past grace


def test_sweep_resets_the_timer_when_someone_real_returns():
    reg = TableRegistry()
    watcher = _session("watcher", "spectator", connected=False)
    table = _register(reg, [watcher, _session("b1", "ai", ai_policy="matrix")])
    now = 2_000_000.0
    sweep_gc(reg, now=now)  # timer starts
    assert table.real_absent_since == now
    watcher.connected = True  # they reconnected
    assert sweep_gc(reg, now=now + 5) == []
    assert table.real_absent_since is None
    # Even long afterwards it is safe while they stay.
    assert sweep_gc(reg, now=now + GC_EMPTY_GRACE_SEC * 10) == []


def test_sweep_removes_a_finished_idle_table_even_with_a_connected_human():
    reg = TableRegistry()
    now = 3_000_000.0
    table = _register(reg, [_session("h")], finished=True, last_activity=now - GC_FINISHED_GRACE_SEC - 1)
    assert sweep_gc(reg, now=now) == [table]


@pytest.mark.asyncio
async def test_gc_sweeps_a_bot_only_table_whose_watcher_left():
    registry = TableRegistry()
    conn = FakeConnection()
    handler = ConnectionHandler(conn, registry)
    await handler.handle_message(build_envelope("identify", {"name": "Watcher", "player_type": "spectator"}))
    await handler.handle_message(
        build_envelope("create_table", {"ai_players": [{"policy": "always_no_change", "count": 2}], "starting_chips": 25})
    )
    room_id = conn.last("table_created")["payload"]["room_id"]
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    for _ in range(10):
        await asyncio.sleep(0)
    table = registry.get(room_id)

    # The watcher closes their tab; the embedded bots keep rematching on their
    # own -- the runaway table the GC exists for.
    await handler.on_disconnect()
    assert real_participant_connected(table) is False

    now = time.time()
    assert sweep_gc(registry, now=now) == []  # timer starts
    doomed = sweep_gc(registry, now=now + GC_EMPTY_GRACE_SEC + 1)
    assert table in doomed

    await _shutdown_table(registry, table, notify_reason=None)
    assert registry.get(room_id) is None
    assert all(t.done() for t in table.bot_tasks)
