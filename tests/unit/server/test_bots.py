"""Server-embedded AI players (`create_table`'s `ai_players`)."""

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

    def last(self, type_: str) -> dict | None:
        for data in reversed(self.sent):
            if data["type"] == type_:
                return data
        return None


async def _create_table_with_bots(handler, ai_players, *, config: dict | None = None) -> str:
    payload = dict(config or {})
    payload["ai_players"] = ai_players
    await handler.handle_message(build_envelope("create_table", payload))
    created = handler.connection.last("table_created")
    assert created is not None, handler.connection.sent
    return created["payload"]["room_id"]


async def _settle() -> None:
    # Let the spawned bot brain tasks run (join replies, ready sends).
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_bots_are_not_seated_until_someone_joins():
    conn = FakeConnection()
    handler = ConnectionHandler(conn, TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Host", "player_type": "human"}))
    room_id = await _create_table_with_bots(handler, [{"policy": "matrix", "count": 2}])
    table = handler.registry.get(room_id)
    await _settle()
    # Spawning at create time would let the bots' all-ready auto-start the
    # game before the creator has even joined.
    assert table.players() == []
    assert table.game is None

    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    await _settle()
    names = sorted(s.name for s in table.players())
    assert names == ["AI-matrix-1", "AI-matrix-2", "Host"]
    assert all(s.player_type == "ai" for s in table.players() if s.name.startswith("AI-"))
    # Bots readied themselves, but the human host hasn't: no auto-start.
    assert len(table.ready_ids) == 2
    assert table.game is None


@pytest.mark.asyncio
async def test_host_ready_starts_the_game_with_bots_and_it_finishes():
    conn = FakeConnection()
    handler = ConnectionHandler(conn, TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Watcher", "player_type": "spectator"}))
    # A spectator host: only the bots are eligible participants, so their
    # own all-ready auto-starts the game -- the AI-vs-AI watching flow.
    room_id = await _create_table_with_bots(
        handler,
        [{"policy": "always_change", "count": 1}, {"policy": "always_no_change", "count": 1}],
        config={"starting_chips": 3},
    )
    table = handler.registry.get(room_id)
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    await _settle()

    async def game_ran_to_completion():
        started = False
        while True:
            if table.game is not None:
                started = True
            if started and table.game is None:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(game_ran_to_completion(), timeout=30)
    # The spectator saw the whole thing.
    types = {d["type"] for d in conn.sent}
    assert "game_ended" in types


@pytest.mark.asyncio
async def test_bots_re_ready_for_a_rematch():
    conn = FakeConnection()
    handler = ConnectionHandler(conn, TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Watcher", "player_type": "spectator"}))
    room_id = await _create_table_with_bots(
        handler,
        [{"policy": "always_no_change", "count": 2}],
        config={"starting_chips": 2},
    )
    table = handler.registry.get(room_id)
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))

    async def wait_for_second_game():
        games_seen = 0
        running = False
        while games_seen < 2:
            if table.game is not None and not running:
                running = True
                games_seen += 1
            elif table.game is None and running:
                running = False
            await asyncio.sleep(0.01)

    # Bot-only normal table: after game_ended the bots re-ready, and their
    # all-ready auto-starts the next game -- the 連戦 flow with no human.
    await asyncio.wait_for(wait_for_second_game(), timeout=60)


@pytest.mark.asyncio
async def test_unknown_policy_is_rejected():
    conn = FakeConnection()
    handler = ConnectionHandler(conn, TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Host", "player_type": "human"}))
    await handler.handle_message(
        build_envelope("create_table", {"ai_players": [{"policy": "does_not_exist", "count": 1}]})
    )
    rejected = conn.last("action_rejected")
    assert rejected is not None and "unknown AI policy" in rejected["payload"]["reason"]


@pytest.mark.asyncio
async def test_too_many_bots_is_rejected():
    conn = FakeConnection()
    handler = ConnectionHandler(conn, TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Host", "player_type": "human"}))
    await handler.handle_message(
        build_envelope("create_table", {"ai_players": [{"policy": "matrix", "count": 15}]})
    )
    rejected = conn.last("action_rejected")
    assert rejected is not None and "at most" in rejected["payload"]["reason"]


@pytest.mark.asyncio
async def test_bot_name_avoids_collision_with_a_seated_player():
    conn = FakeConnection()
    handler = ConnectionHandler(conn, TableRegistry())
    # A human who happens to be called like a bot must not block the spawn.
    await handler.handle_message(build_envelope("identify", {"name": "AI-matrix-1", "player_type": "human"}))
    room_id = await _create_table_with_bots(handler, [{"policy": "matrix", "count": 1}])
    table = handler.registry.get(room_id)
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    await _settle()
    names = sorted(s.name for s in table.players())
    assert names == ["AI-matrix-1", "AI-matrix-2"]
