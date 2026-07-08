"""End-to-end test driving the full stack (dispatch -> runner -> domain)
with fake in-memory connections standing in for real WebSockets."""

import asyncio
import json

import pytest

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


async def auto_respond(handler: ConnectionHandler, conn: FakeConnection, stop_event: asyncio.Event) -> None:
    """Always no-change / cucco-pass / continue=True -- the simplest
    possible well-behaved client, enough to drive a game to completion."""
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
        elif type_ == "cucco_window":
            await handler.handle_message(build_envelope("cucco_pass", {}, table_id=table_id))
        elif type_ == "continue_prompt":
            await handler.handle_message(build_envelope("continue_declare", {"continue": True}, table_id=table_id))
        elif type_ == "game_ended":
            stop_event.set()


async def _setup_player(registry: TableRegistry, name: str) -> tuple[ConnectionHandler, FakeConnection]:
    conn = FakeConnection(name)
    handler = ConnectionHandler(conn, registry)
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
