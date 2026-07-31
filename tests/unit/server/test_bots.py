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


@pytest.fixture
def unpaced(monkeypatch):
    """Run the bots at full speed.

    A connected spectator paces every AI turn so the game stays watchable
    (see test_runner_prompt.py), but these tests are about the start/finish
    mechanics rather than the pacing, and a spectator host is the shape they
    happen to use -- paying 0.8s per turn would push them past their timeout
    for nothing.
    """
    monkeypatch.setattr("cucco.server.runner.AI_TURN_PACING_SEC", 0.0)


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
async def test_host_ready_starts_the_game_with_bots_and_it_finishes(unpaced):
    conn = FakeConnection()
    handler = ConnectionHandler(conn, TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Watcher", "player_type": "spectator"}))
    # A spectator host watching an AI-vs-AI game. The FIRST start waits for
    # the host's start_pot (so there is a window to look at the roster and
    # invite people); only rematches auto-start.
    room_id = await _create_table_with_bots(
        handler,
        [{"policy": "always_change", "count": 1}, {"policy": "always_no_change", "count": 1}],
        config={"starting_chips": 3},
    )
    table = handler.registry.get(room_id)
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    await _settle()
    await handler.handle_message(build_envelope("start_pot", {}))

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
async def test_a_rematch_waits_for_the_host_even_though_the_bots_re_ready(unpaced):
    """連戦は自動で始まらない。The bots declare themselves ready for another
    game (a room stays usable), but nothing starts until the human running the
    room asks for it -- otherwise the next game deals itself over the result
    screen the watchers are still reading."""
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
    await _settle()
    await handler.handle_message(build_envelope("start_pot", {}))  # first start is manual

    async def first_game_over():
        started = False
        while True:
            if table.game is not None:
                started = True
            if started and table.game is None:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(first_game_over(), timeout=60)
    # The bots are ready for another one...
    await _settle()
    assert len(table.ready_ids) == 2
    # ...but a good while later, still nothing has started by itself.
    for _ in range(50):
        await asyncio.sleep(0.01)
        assert table.game is None
    # The watcher asks for the rematch, and only then does it start.
    await handler.handle_message(build_envelope("start_pot", {}))
    assert table.game is not None


@pytest.mark.asyncio
async def test_a_deserted_table_closes_itself_when_its_game_ends(unpaced):
    """人間が誰もいなくなった卓: the game in progress is played to its end (and
    recorded), and the room is closed at that boundary instead of dealing
    another game to an empty room -- or being left for the GC, which would cut
    a game mid-play ten minutes later."""
    conn = FakeConnection()
    registry = TableRegistry()
    handler = ConnectionHandler(conn, registry)
    await handler.handle_message(build_envelope("identify", {"name": "Watcher", "player_type": "spectator"}))
    room_id = await _create_table_with_bots(
        handler,
        [{"policy": "always_no_change", "count": 2}],
        config={"starting_chips": 2},
    )
    table = registry.get(room_id)
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    await _settle()
    await handler.handle_message(build_envelope("start_pot", {}))
    assert table.game is not None

    # The watcher closes their tab while the bots are playing.
    await handler.on_disconnect()

    async def table_closed():
        while registry.get(room_id) is not None:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(table_closed(), timeout=60)
    # Closed at a game boundary, not by force: the runner returned on its own
    # (which is what clears `game`), rather than being cancelled mid-play the
    # way the GC sweep and the admin abort do it.
    assert table.game is None
    assert table.finished
    # The bots were stopped with it -- nothing keeps playing in the background.
    assert all(task.done() or task.cancelled() for task in table.bot_tasks)


@pytest.mark.asyncio
async def test_discard_display_preference_reaches_the_snapshot():
    conn = FakeConnection()
    handler = ConnectionHandler(conn, TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Host", "player_type": "human"}))
    await handler.handle_message(build_envelope("create_table", {"discard_display": "pile"}))
    room_id = conn.last("table_created")["payload"]["room_id"]
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    snapshot = conn.last("state_snapshot")
    assert snapshot["payload"]["discard_display"] == "pile"


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
