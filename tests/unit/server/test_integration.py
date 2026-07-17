"""End-to-end test driving the full stack (dispatch -> runner -> domain)
with fake in-memory connections standing in for real WebSockets."""

import asyncio
import json
from pathlib import Path

import pytest

from cucco.persistence.results_store import ResultsStore
from cucco.protocol.envelope import build_envelope
from cucco.server.dispatch import ConnectionHandler
from cucco.server.registry import TableRegistry


class FakeConnection:
    def __init__(self, name: str):
        self.name = name
        self.sent: list[dict] = []
        self.queue: "asyncio.Queue[dict]" = asyncio.Queue()

    async def send(self, message: str) -> None:
        data = json.loads(message)
        self.sent.append(data)
        await self.queue.put(data)


async def auto_respond(
    handler: ConnectionHandler, conn: FakeConnection, stop_event: asyncio.Event, stop_type: str = "game_ended"
) -> None:
    """Always no-change / どうぞ / continue=True -- the simplest possible
    well-behaved client, enough to drive a game (or, with
    stop_type="evaluation_summary", a whole game_count run) to completion."""
    while not stop_event.is_set():
        try:
            data = await asyncio.wait_for(conn.queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        type_ = data["type"]
        table_id = data.get("table_id")
        if type_ == "dealer_ready":
            await handler.handle_message(build_envelope("dealer_ready", {}, table_id=table_id))
        elif type_ == "turn_prompt":
            await handler.handle_message(build_envelope("no_change_declare", {}, table_id=table_id))
        elif type_ == "continue_prompt":
            await handler.handle_message(build_envelope("continue_declare", {"continue": True}, table_id=table_id))
        if type_ == stop_type:
            stop_event.set()


async def _setup_player(
    registry: TableRegistry, name: str, results_store=None, action_log_dir=None
) -> tuple[ConnectionHandler, FakeConnection]:
    conn = FakeConnection(name)
    handler = ConnectionHandler(conn, registry, results_store=results_store, action_log_dir=action_log_dir)
    await handler.handle_message(build_envelope("identify", {"name": name, "player_type": "ai"}))
    return handler, conn


@pytest.mark.asyncio
async def test_full_game_runs_to_completion_over_fake_connections():
    registry = TableRegistry()

    creator, creator_conn = await _setup_player(registry, "Alice")
    await creator.handle_message(
        build_envelope(
            "create_table",
            {"mode": "normal", "end_condition": "chips_zero", "starting_chips": 5},
        )
    )
    table_created = next(m for m in creator_conn.sent if m["type"] == "table_created")
    room_id = table_created["payload"]["room_id"]

    bob, bob_conn = await _setup_player(registry, "Bob")
    carol, carol_conn = await _setup_player(registry, "Carol")

    handlers_and_conns = [(creator, creator_conn), (bob, bob_conn), (carol, carol_conn)]
    for handler, conn in handlers_and_conns:
        await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))

    stop_event = asyncio.Event()
    responder_tasks = [
        asyncio.create_task(auto_respond(handler, conn, stop_event)) for handler, conn in handlers_and_conns
    ]

    for handler, _ in handlers_and_conns:
        await handler.handle_message(build_envelope("ready", {}))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    finally:
        for task in responder_tasks:
            task.cancel()
        await asyncio.gather(*responder_tasks, return_exceptions=True)

    game_ended = next(m for conn in (creator_conn, bob_conn, carol_conn) for m in conn.sent if m["type"] == "game_ended")
    ranking = game_ended["payload"]["ranking"]
    assert {pid for pid, _ in ranking} == {creator.session.player_id, bob.session.player_id, carol.session.player_id}
    total_chips = sum(chips for _, chips in ranking)
    assert total_chips <= 15  # 3 players x 5 starting chips, minus any unclaimed pot

    # Every player should have received at least one deal_started with
    # their own hand.
    for _, conn in handlers_and_conns:
        deal_starts = [m for m in conn.sent if m["type"] == "deal_started"]
        assert deal_starts
        assert any(m["payload"]["your_hand"] is not None for m in deal_starts)

    # deal_result/pot_result aggregates (docs/protocol/design.md) were sent
    # and carry absolute chip counts for every seat.
    deal_results = [m for m in creator_conn.sent if m["type"] == "deal_result"]
    assert deal_results
    for m in deal_results:
        assert set(m["payload"]["chips_now"]) == {creator.session.player_id, bob.session.player_id, carol.session.player_id}
        assert "losers" in m["payload"]
        assert "discarded_cards" in m["payload"]

    pot_results = [m for m in creator_conn.sent if m["type"] == "pot_result"]
    assert pot_results
    for m in pot_results:
        assert m["payload"]["result"] in ("won", "wiped_out")
        assert set(m["payload"]["chips_now"]) == {creator.session.player_id, bob.session.player_id, carol.session.player_id}


@pytest.mark.asyncio
async def test_full_game_persists_a_results_row_and_a_replayable_action_log(tmp_path, monkeypatch):
    registry = TableRegistry()
    results_store = ResultsStore(tmp_path / "results.db")
    action_log_dir = tmp_path / "action_logs"

    # dispatch._start_game draws its RNG seed from OS entropy (by design, so
    # it can't be predicted/replayed by an attacker) -- pin it here so this
    # persistence test runs a fully deterministic game.
    class _FixedEntropy:
        def randrange(self, _n):
            return 0

    monkeypatch.setattr("cucco.server.dispatch.random.SystemRandom", _FixedEntropy)

    creator, creator_conn = await _setup_player(registry, "Alice", results_store, action_log_dir)
    await creator.handle_message(
        build_envelope("create_table", {"mode": "normal", "end_condition": "chips_zero", "starting_chips": 5})
    )
    room_id = next(m for m in creator_conn.sent if m["type"] == "table_created")["payload"]["room_id"]

    bob, bob_conn = await _setup_player(registry, "Bob", results_store, action_log_dir)
    carol, carol_conn = await _setup_player(registry, "Carol", results_store, action_log_dir)

    handlers_and_conns = [(creator, creator_conn), (bob, bob_conn), (carol, carol_conn)]
    for handler, conn in handlers_and_conns:
        await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))

    stop_event = asyncio.Event()
    responder_tasks = [
        asyncio.create_task(auto_respond(handler, conn, stop_event)) for handler, conn in handlers_and_conns
    ]
    for handler, _ in handlers_and_conns:
        await handler.handle_message(build_envelope("ready", {}))
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    finally:
        for task in responder_tasks:
            task.cancel()
        await asyncio.gather(*responder_tasks, return_exceptions=True)

    # _record_results() runs synchronously right after game_ended is
    # broadcast, in the same TableRunner task -- this sleep is a defensive
    # margin against FakeConnection.send() ever gaining a real suspension
    # point, not a required synchronization today.
    await asyncio.sleep(0.05)

    games = results_store._conn.execute("SELECT table_id, mode, action_log_path FROM games").fetchall()
    assert len(games) == 1
    assert games[0][:2] == (room_id, "normal")
    participants = results_store._conn.execute(
        "SELECT player_id, name, player_type, final_rank FROM participants ORDER BY final_rank"
    ).fetchall()
    assert {row[0] for row in participants} == {creator.session.player_id, bob.session.player_id, carol.session.player_id}
    assert [row[3] for row in participants] == [1, 2, 3]  # ranks 1..3, no gaps

    # The action log's filename includes a per-game uuid (not just room_id)
    # so a room_id reissued after a restart, or multiple games under one
    # table in evaluation mode, can never collide/truncate each other.
    log_path = Path(games[0][2])
    assert log_path.parent == action_log_dir
    assert log_path.name.startswith(f"{room_id}-")
    assert log_path.exists()
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["kind"] == "seed"
    assert isinstance(lines[0]["seed"], int)
    assert any(line["kind"] == "event" and line["event_type"] == "GameEnded" for line in lines)

    results_store.close()


@pytest.mark.asyncio
async def test_evaluation_table_runs_game_count_games_and_excludes_a_human_observer():
    registry = TableRegistry()

    creator, creator_conn = await _setup_player(registry, "Alice")
    await creator.handle_message(
        build_envelope(
            "create_table",
            {
                "mode": "evaluation",
                "game_count": 2,
                "end_condition": "chips_zero",
                "starting_chips": 5,
                "turn_timeout_ai_sec": 0.2,
                "cucco_window_timeout_ai_sec": 0.05,
            },
        )
    )
    room_id = next(m for m in creator_conn.sent if m["type"] == "table_created")["payload"]["room_id"]

    bob, bob_conn = await _setup_player(registry, "Bob")

    # A human joins to watch -- per docs/protocol/design.md 「AI専用高速
    # 評価モード」, humans never become game participants in evaluation
    # mode, no matter what they do.
    human_conn = FakeConnection("Dave")
    human = ConnectionHandler(human_conn, registry)
    await human.handle_message(build_envelope("identify", {"name": "Dave", "player_type": "human"}))

    for handler in (creator, bob, human):
        await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))

    stop_event = asyncio.Event()
    responder_tasks = [
        asyncio.create_task(auto_respond(handler, conn, stop_event, stop_type="evaluation_summary"))
        for handler, conn in [(creator, creator_conn), (bob, bob_conn)]
    ]
    # Only the two AI players ready up -- if the human's presence still
    # counted toward the readiness threshold, this would hang until the
    # ready-timeout watchdog (60s) instead of starting immediately.
    for handler in (creator, bob):
        await handler.handle_message(build_envelope("ready", {}))

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=10.0)
    finally:
        for task in responder_tasks:
            task.cancel()
        await asyncio.gather(*responder_tasks, return_exceptions=True)

    summary = next(m for m in creator_conn.sent if m["type"] == "evaluation_summary")["payload"]
    assert summary["game_count"] == 2
    assert set(summary["players"]) == {creator.session.player_id, bob.session.player_id}
    assert len(summary["seat_rotations"]) == 2

    # The human never played, but did watch: they got the normal broadcast
    # stream (your_hand always null) plus the final summary like everyone.
    assert any(m["type"] == "deal_started" for m in human_conn.sent)
    assert any(m["type"] == "evaluation_summary" for m in human_conn.sent)
    for m in human_conn.sent:
        if m["type"] == "deal_started":
            assert m["payload"]["your_hand"] is None

    # Two full games were played (chips reset each time), not one long one.
    game_ended = [m for m in creator_conn.sent if m["type"] == "game_ended"]
    assert len(game_ended) == 2
