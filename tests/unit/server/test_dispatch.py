import asyncio
import json
import random

import pytest

from cucco.domain.cards import Rank
from cucco.domain.config import GameConfig
from cucco.domain.deck import Deck
from cucco.domain.game import Game
from cucco.protocol.actions import CuccoPass, DealerReady, NoChangeDeclare
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


class AutoRespondConnection:
    """Answers every prompt immediately (no_change / cucco_pass) by pushing
    straight into its own session's inbox -- enough to drive a deal to
    "open" without a real client loop."""

    def __init__(self):
        self.sent: list[dict] = []
        self.session: PlayerSession | None = None

    async def send(self, message: str) -> None:
        data = json.loads(message)
        self.sent.append(data)
        if data["type"] == "turn_prompt":
            self.session.inbox.put_nowait(NoChangeDeclare())
        elif data["type"] == "cucco_window":
            self.session.inbox.put_nowait(CuccoPass())
        elif data["type"] == "dealer_ready":
            self.session.inbox.put_nowait(DealerReady())


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
async def test_create_table_with_evaluation_mode_succeeds():
    handler = ConnectionHandler(FakeConnection(), TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "ai"}))
    await handler.handle_message(build_envelope("create_table", {"mode": "evaluation", "game_count": 10}))
    assert handler.connection.sent[-1]["type"] == "table_created"


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
async def test_start_game_still_starts_if_the_action_log_cannot_be_created(tmp_path, monkeypatch):
    # Persistence is server-internal (docs/protocol/design.md) -- a failure
    # opening the replay log must never prevent the game itself from
    # starting. Force that failure: action_log_dir needs to be a directory,
    # but here it's a plain file, so ActionLogWriter's mkdir() raises.
    blocked_path = tmp_path / "action_logs"
    blocked_path.write_text("not a directory")

    import cucco.server.dispatch as dispatch_module

    ran_without_action_log = asyncio.Event()

    async def fake_run_table_safely(table, action_log=None):
        assert action_log is None
        ran_without_action_log.set()

    monkeypatch.setattr(dispatch_module, "_run_table_safely", fake_run_table_safely)

    table = Table(room_id="ABC123", config=GameConfig(), creator_id="p1", action_log_dir=blocked_path)
    for pid in ("p1", "p2"):
        table.add_session(PlayerSession(player_id=pid, name=pid, player_type="ai", session_token=pid, connection=FakeConnection()))
    table.ready_ids = {"p1", "p2"}

    await _start_game(table)

    assert table.game is not None
    assert set(table.game.seats) == {"p1", "p2"}
    await asyncio.wait_for(ran_without_action_log.wait(), timeout=1.0)


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
    stale_task = asyncio.create_task(asyncio.sleep(0))
    table.ready_deadline_task = stale_task  # simulates the just-fired watchdog

    await _start_game(table)

    assert table.game is None
    assert table.ready_ids == set()
    # The fired watchdog's task reference must be cleared too, or a later
    # `ready` would see a non-None task and never spawn a fresh watchdog --
    # wedging the lobby forever after one failed retry.
    assert table.ready_deadline_task is None
    await stale_task


@pytest.mark.asyncio
async def test_ready_after_a_failed_timeout_retry_rearms_a_fresh_watchdog():
    registry = TableRegistry()
    handler = ConnectionHandler(FakeConnection(), registry)
    await handler.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "ai"}))
    await handler.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in handler.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    table = handler.table
    table.min_players = 2  # a lone reader is never enough to start

    await handler.handle_message(build_envelope("ready", {}))
    first_watchdog = table.ready_deadline_task
    assert first_watchdog is not None

    # Simulate that watchdog firing with too few players ready.
    await _start_game(table)
    assert table.ready_deadline_task is None
    first_watchdog.cancel()

    # A later `ready` (e.g. a reconnect/retry) must spawn a new watchdog
    # instead of silently doing nothing forever.
    await handler.handle_message(build_envelope("ready", {}))
    assert table.ready_deadline_task is not None
    assert table.ready_deadline_task is not first_watchdog
    table.ready_deadline_task.cancel()


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


@pytest.mark.asyncio
async def test_wipeout_instant_win_after_carryover_sends_a_pot_result_aggregate():
    # docs/protocol/design.md:159 -- when a wipeout carryover leaves exactly
    # one solvent seat, Game resolves that instantly (no deal played) and
    # this must still produce its own pot_result aggregate, not just the
    # granular pot_won buried inside game_events.
    config = GameConfig(starting_chips=25, end_condition="chips_zero")
    game = Game(["A", "B"], config, random.Random(0))
    game.start_first_pot()  # A: 24, B: 24
    game.chips["B"] = 0  # B is already insolvent from an earlier pot

    table = Table(room_id="ABC123", config=config, creator_id="A")
    conns = {}
    for pid in ("A", "B"):
        conn = AutoRespondConnection()
        session = PlayerSession(player_id=pid, name=pid, player_type="ai", session_token=pid, connection=conn)
        conn.session = session
        table.add_session(session)
        conns[pid] = conn

    pot = game.current_pot
    pot.dealer_id = "A"  # deal order becomes [B, A] (dealer last)
    pot.deal_number = 3  # next deal is 4 -- adult time, tie eliminates both
    pot.deck = Deck.from_fixed_order([Rank.N5, Rank.N5], rng=random.Random(0))

    runner = TableRunner(table)
    await runner._run_pot(game)

    pot_results = [m for m in conns["A"].sent if m["type"] == "pot_result"]
    assert len(pot_results) == 2
    assert pot_results[0]["payload"]["result"] == "wiped_out"
    assert pot_results[1]["payload"] == {
        "result": "won",
        "winner": "A",
        "amount": 2,
        "chips_now": {"A": 26, "B": 0},
    }
    assert game.is_finished  # B is still at 0 chips (chips_zero end condition)
