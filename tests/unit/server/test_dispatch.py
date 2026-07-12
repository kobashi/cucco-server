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
from cucco.server.runner import TableRunner, build_state_snapshot
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
async def test_create_table_with_nonpositive_game_count_is_rejected_not_crashed():
    handler = ConnectionHandler(FakeConnection(), TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "ai"}))
    await handler.handle_message(build_envelope("create_table", {"mode": "evaluation", "game_count": 0}))
    assert handler.connection.sent[-1]["type"] == "action_rejected"


@pytest.mark.asyncio
async def test_human_ready_on_an_evaluation_table_is_rejected():
    # docs/protocol/design.md 「AI専用高速評価モード」: only AI players play.
    # A human's `ready` must be rejected outright, not silently accepted --
    # accepting it would let it count toward (and potentially distort) the
    # readiness threshold without the human ever becoming a participant.
    registry = TableRegistry()
    handler = ConnectionHandler(FakeConnection(), registry)
    await handler.handle_message(build_envelope("identify", {"name": "Dave", "player_type": "human"}))
    await handler.handle_message(build_envelope("create_table", {"mode": "evaluation", "game_count": 2}))
    room_id = next(m for m in handler.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    await handler.handle_message(build_envelope("ready", {}))
    assert handler.connection.sent[-1]["type"] == "action_rejected"


@pytest.mark.asyncio
async def test_join_unknown_room_is_rejected():
    handler = ConnectionHandler(FakeConnection(), TableRegistry())
    await handler.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await handler.handle_message(build_envelope("join_table", {"room_id": "NOPE99"}))
    assert handler.connection.sent[-1]["type"] == "action_rejected"


@pytest.mark.asyncio
async def test_duplicate_player_name_at_a_table_is_rejected():
    # Best-effort deterrent against one person taking several seats and
    # against label impersonation (docs/security-notes.md #2).
    registry = TableRegistry()
    creator = ConnectionHandler(FakeConnection(), registry)
    await creator.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await creator.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in creator.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await creator.handle_message(build_envelope("join_table", {"room_id": room_id}))

    clash = ConnectionHandler(FakeConnection(), registry)
    await clash.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await clash.handle_message(build_envelope("join_table", {"room_id": room_id}))
    assert clash.connection.sent[-1]["type"] == "action_rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("clashing_name", ["ALICE", "alice", "Ａlice"])
async def test_case_and_width_variant_names_are_treated_as_duplicates(clashing_name):
    # NFKC + casefold folding (docs/security-notes.md) so full-width and
    # letter-case variants can't sneak past the duplicate-name deterrent.
    registry = TableRegistry()
    creator = ConnectionHandler(FakeConnection(), registry)
    await creator.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await creator.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in creator.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await creator.handle_message(build_envelope("join_table", {"room_id": room_id}))

    clash = ConnectionHandler(FakeConnection(), registry)
    await clash.handle_message(build_envelope("identify", {"name": clashing_name, "player_type": "human"}))
    await clash.handle_message(build_envelope("join_table", {"room_id": room_id}))
    assert clash.connection.sent[-1]["type"] == "action_rejected"


@pytest.mark.asyncio
async def test_duplicate_name_is_allowed_for_spectators():
    # Spectators hold no seat or hand, so a name clash there is harmless and
    # must not be blocked.
    registry = TableRegistry()
    creator = ConnectionHandler(FakeConnection(), registry)
    await creator.handle_message(build_envelope("identify", {"name": "Watcher", "player_type": "spectator"}))
    await creator.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in creator.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await creator.handle_message(build_envelope("join_table", {"room_id": room_id}))

    other = ConnectionHandler(FakeConnection(), registry)
    await other.handle_message(build_envelope("identify", {"name": "Watcher", "player_type": "spectator"}))
    await other.handle_message(build_envelope("join_table", {"room_id": room_id}))
    assert other.connection.sent[-1]["type"] == "state_snapshot"


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
async def test_creator_can_start_pot_early_once_enough_players_are_ready():
    registry = TableRegistry()
    creator = ConnectionHandler(FakeConnection(), registry)
    await creator.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await creator.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in creator.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await creator.handle_message(build_envelope("join_table", {"room_id": room_id}))

    p2 = ConnectionHandler(FakeConnection(), registry)
    await p2.handle_message(build_envelope("identify", {"name": "Bob", "player_type": "human"}))
    await p2.handle_message(build_envelope("join_table", {"room_id": room_id}))

    p3 = ConnectionHandler(FakeConnection(), registry)
    await p3.handle_message(build_envelope("identify", {"name": "Carol", "player_type": "human"}))
    await p3.handle_message(build_envelope("join_table", {"room_id": room_id}))

    await creator.handle_message(build_envelope("ready", {}))
    await p2.handle_message(build_envelope("ready", {}))
    # p3 never readies

    await creator.handle_message(build_envelope("start_pot", {}))
    table = registry.get(room_id)
    assert table.game is not None
    assert set(table.game.seats) == {creator.session.player_id, p2.session.player_id}


@pytest.mark.asyncio
async def test_start_pot_auto_readies_the_creator():
    # The organizer's flow is "wait for guests to ready, then press start" --
    # pressing start IS their participation declaration, no separate `ready`
    # needed (and the waiting-room UI no longer offers them one).
    registry = TableRegistry()
    creator = ConnectionHandler(FakeConnection(), registry)
    await creator.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await creator.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in creator.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await creator.handle_message(build_envelope("join_table", {"room_id": room_id}))

    p2 = ConnectionHandler(FakeConnection(), registry)
    await p2.handle_message(build_envelope("identify", {"name": "Bob", "player_type": "human"}))
    await p2.handle_message(build_envelope("join_table", {"room_id": room_id}))

    await p2.handle_message(build_envelope("ready", {}))
    # creator never sends `ready`
    await creator.handle_message(build_envelope("start_pot", {}))

    table = registry.get(room_id)
    assert table.game is not None
    assert set(table.game.seats) == {creator.session.player_id, p2.session.player_id}


@pytest.mark.asyncio
async def test_non_creator_cannot_start_pot():
    registry = TableRegistry()
    creator = ConnectionHandler(FakeConnection(), registry)
    await creator.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await creator.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in creator.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await creator.handle_message(build_envelope("join_table", {"room_id": room_id}))

    p2 = ConnectionHandler(FakeConnection(), registry)
    await p2.handle_message(build_envelope("identify", {"name": "Bob", "player_type": "human"}))
    await p2.handle_message(build_envelope("join_table", {"room_id": room_id}))

    await creator.handle_message(build_envelope("ready", {}))
    await p2.handle_message(build_envelope("ready", {}))

    await p2.handle_message(build_envelope("start_pot", {}))
    assert p2.connection.sent[-1]["type"] == "action_rejected"


@pytest.mark.asyncio
async def test_start_pot_before_enough_players_ready_is_rejected_without_clearing_ready_ids():
    registry = TableRegistry()
    creator = ConnectionHandler(FakeConnection(), registry)
    await creator.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await creator.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in creator.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await creator.handle_message(build_envelope("join_table", {"room_id": room_id}))

    p2 = ConnectionHandler(FakeConnection(), registry)
    await p2.handle_message(build_envelope("identify", {"name": "Bob", "player_type": "human"}))
    await p2.handle_message(build_envelope("join_table", {"room_id": room_id}))

    await creator.handle_message(build_envelope("ready", {}))

    await creator.handle_message(build_envelope("start_pot", {}))
    assert creator.connection.sent[-1]["type"] == "action_rejected"

    table = registry.get(room_id)
    assert table.game is None
    assert creator.session.player_id in table.ready_ids

    # a subsequent successful start_pot still works once enough are ready
    await p2.handle_message(build_envelope("ready", {}))
    await creator.handle_message(build_envelope("start_pot", {}))
    assert table.game is not None


@pytest.mark.asyncio
async def test_stale_disconnect_after_reconnect_does_not_mark_the_session_disconnected():
    # Page-reload race: the new connection's session_token rebind can finish
    # BEFORE the old connection's close is detected (tunnel close lag). The
    # old handler's on_disconnect must not flip `connected` back to False on
    # a session that has already moved to a newer connection -- that silently
    # mutes all sends to the player and makes the runner treat them as gone.
    registry = TableRegistry()
    old_handler = ConnectionHandler(FakeConnection(), registry)
    await old_handler.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await old_handler.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in old_handler.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await old_handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    token = old_handler.session.session_token

    # Reload: a brand-new connection rebinds the session via the token.
    new_handler = ConnectionHandler(FakeConnection(), registry)
    await new_handler.handle_message(build_envelope("join_table", {"room_id": room_id, "session_token": token}))
    session = new_handler.session
    assert session is old_handler.session  # same PlayerSession, rebound
    assert session.connected is True

    # The OLD connection's close arrives late -- must be a no-op now.
    await old_handler.on_disconnect()
    assert session.connected is True

    # A close of the CURRENT connection must still mark it disconnected.
    await new_handler.on_disconnect()
    assert session.connected is False


@pytest.mark.asyncio
async def test_reconnect_resends_the_outstanding_prompt_with_remaining_time():
    # A player who reloads mid-turn lost the prompt envelope with their old
    # connection; the rebind must re-send it (with the remaining seconds) or
    # they sit promptless until the server times them out.
    registry = TableRegistry()
    handler = ConnectionHandler(FakeConnection(), registry)
    await handler.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await handler.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in handler.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))
    session = handler.session
    table = registry.get(room_id)

    from cucco.protocol.actions import CambioDeclare as _Cambio, NoChangeDeclare as _NoChange
    from cucco.server.runner import TableRunner as _Runner

    runner = _Runner(table)
    prompt_task = asyncio.create_task(runner._prompt(session, "turn", (_Cambio, _NoChange)))
    await asyncio.sleep(0.01)  # let the prompt send and register itself
    assert session.outstanding_prompt is not None

    reconnecting = ConnectionHandler(FakeConnection(), registry)
    await reconnecting.handle_message(
        build_envelope("join_table", {"room_id": room_id, "session_token": session.session_token})
    )
    resent = [m for m in reconnecting.connection.sent if m["type"] == "turn_prompt"]
    assert len(resent) == 1
    assert 0 < resent[0]["payload"]["timeout_sec"] <= 30.0

    session.inbox.put_nowait(_NoChange())
    action = await prompt_task
    assert isinstance(action, _NoChange)
    assert session.outstanding_prompt is None  # cleared once the prompt resolves


@pytest.mark.asyncio
async def test_pot_start_waits_for_a_reconnecting_player_instead_of_force_ending(monkeypatch):
    # A reload spanning a pot boundary used to force_end the game instantly
    # (connected_count < 2 with zero grace); the reconnect then landed on an
    # already-finished game.
    import cucco.server.runner as runner_module

    monkeypatch.setattr(runner_module, "RECONNECT_GRACE_SEC", 1.0)
    table = Table(room_id="ABC123", config=GameConfig(), creator_id="p1")
    for pid in ("p1", "p2"):
        table.add_session(PlayerSession(player_id=pid, name=pid, player_type="ai", session_token=pid, connection=FakeConnection()))
    table.get("p2").connected = False  # mid-reload at pot start

    import random as _random

    from cucco.domain.pot import Pot

    pot = Pot(["p1", "p2"], "p1", {"p1": 24, "p2": 24}, table.config, _random.Random(0))
    runner = TableRunner(table)

    async def reconnect_soon():
        await asyncio.sleep(0.2)
        table.get("p2").connected = True

    task = asyncio.create_task(reconnect_soon())
    await runner._await_reconnections(pot)
    await task
    assert runner._connected_count(pot.active_participants()) == 2


@pytest.mark.asyncio
async def test_snapshot_reports_a_finished_game_to_late_reconnects():
    table = Table(room_id="ABC123", config=GameConfig(), creator_id="p1")
    for pid in ("p1", "p2"):
        table.add_session(PlayerSession(player_id=pid, name=pid, player_type="ai", session_token=pid, connection=FakeConnection()))
    table.game = Game(["p1", "p2"], table.config, random.Random(0))
    list(table.game.start_first_pot())
    table.game.force_end()

    snapshot = build_state_snapshot(table, "p1")
    assert snapshot["game_finished"] is True
    assert snapshot["final_ranking"] is not None
    assert {pid for pid, _ in snapshot["final_ranking"]} == {"p1", "p2"}


@pytest.mark.asyncio
async def test_room_resets_after_a_game_so_another_can_start(monkeypatch):
    # A normal-mode room outlives its game: after game_ended the table goes
    # back to the waiting state (same room_id) so the same and/or new players
    # can ready up and the organizer can start a fresh game.
    import cucco.server.dispatch as dispatch_module

    class InstantRunner:
        def __init__(self, table, action_log=None, results_store=None):
            self.table = table

        async def run(self):
            self.table.game.force_end()

    monkeypatch.setattr(dispatch_module, "TableRunner", InstantRunner)

    registry = TableRegistry()
    creator = ConnectionHandler(FakeConnection(), registry)
    await creator.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await creator.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in creator.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await creator.handle_message(build_envelope("join_table", {"room_id": room_id}))

    p2 = ConnectionHandler(FakeConnection(), registry)
    await p2.handle_message(build_envelope("identify", {"name": "Bob", "player_type": "human"}))
    await p2.handle_message(build_envelope("join_table", {"room_id": room_id}))
    await p2.handle_message(build_envelope("ready", {}))

    await creator.handle_message(build_envelope("start_pot", {}))
    await asyncio.sleep(0.01)  # let the fire-and-forget _run_table_safely finish

    table = registry.get(room_id)
    assert table.game is None  # room is back in the waiting state
    assert table.ready_ids == set()
    assert table.finished is False

    # A newcomer joins the SAME room and a second game starts.
    p3 = ConnectionHandler(FakeConnection(), registry)
    await p3.handle_message(build_envelope("identify", {"name": "Carol", "player_type": "human"}))
    await p3.handle_message(build_envelope("join_table", {"room_id": room_id}))
    await p3.handle_message(build_envelope("ready", {}))
    await creator.handle_message(build_envelope("start_pot", {}))
    assert table.game is not None
    assert p3.session.player_id in table.game.seats


@pytest.mark.asyncio
async def test_start_button_falls_to_the_next_connected_player_when_the_creator_leaves():
    registry = TableRegistry()
    creator = ConnectionHandler(FakeConnection(), registry)
    await creator.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await creator.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in creator.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await creator.handle_message(build_envelope("join_table", {"room_id": room_id}))

    p2 = ConnectionHandler(FakeConnection(), registry)
    await p2.handle_message(build_envelope("identify", {"name": "Bob", "player_type": "human"}))
    await p2.handle_message(build_envelope("join_table", {"room_id": room_id}))
    p3 = ConnectionHandler(FakeConnection(), registry)
    await p3.handle_message(build_envelope("identify", {"name": "Carol", "player_type": "human"}))
    await p3.handle_message(build_envelope("join_table", {"room_id": room_id}))

    table = registry.get(room_id)
    creator_session = creator.session

    # While the creator is present, Bob has no start rights.
    await p2.handle_message(build_envelope("ready", {}))
    await p3.handle_message(build_envelope("ready", {}))
    await p2.handle_message(build_envelope("start_pot", {}))
    assert p2.connection.sent[-1]["type"] == "action_rejected"

    # Creator leaves -> the earliest-joined connected player inherits the role.
    creator_session.connected = False
    assert table.effective_creator_id() == p2.session.player_id
    snapshot = build_state_snapshot(table, p2.session.player_id)
    assert snapshot["creator_id"] == p2.session.player_id

    await p2.handle_message(build_envelope("start_pot", {}))
    assert table.game is not None
    assert set(table.game.seats) == {p2.session.player_id, p3.session.player_id}


@pytest.mark.asyncio
async def test_state_snapshot_includes_creator_id_and_ready_ids():
    registry = TableRegistry()
    creator = ConnectionHandler(FakeConnection(), registry)
    await creator.handle_message(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
    await creator.handle_message(build_envelope("create_table", {}))
    room_id = next(m for m in creator.connection.sent if m["type"] == "table_created")["payload"]["room_id"]
    await creator.handle_message(build_envelope("join_table", {"room_id": room_id}))

    p2 = ConnectionHandler(FakeConnection(), registry)
    await p2.handle_message(build_envelope("identify", {"name": "Bob", "player_type": "human"}))
    await p2.handle_message(build_envelope("join_table", {"room_id": room_id}))

    await creator.handle_message(build_envelope("ready", {}))

    table = registry.get(room_id)
    snapshot = build_state_snapshot(table, p2.session.player_id)
    assert snapshot["creator_id"] == creator.session.player_id
    assert snapshot["ready_ids"] == [creator.session.player_id]


@pytest.mark.asyncio
async def test_force_end_fires_when_too_few_connected_players_remain_to_start_a_pot(monkeypatch):
    import cucco.server.runner as runner_module

    monkeypatch.setattr(runner_module, "RECONNECT_GRACE_SEC", 0.05)  # don't wait the real 60s grace in a test
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
