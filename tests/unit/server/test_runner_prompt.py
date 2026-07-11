import asyncio
import json
import random

import pytest

from cucco.domain.cards import Rank
from cucco.domain.config import GameConfig
from cucco.domain.deck import Deck
from cucco.domain.pot import Pot
from cucco.protocol.actions import CambioDeclare, ContinueDeclare, CuccoDeclare, CuccoPass, DealerReady, NoChangeDeclare
from cucco.server.runner import TableRunner
from cucco.server.session import PlayerSession
from cucco.server.table import Table


class FakeConnection:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


def make_table() -> Table:
    return Table(room_id="ABC123", config=GameConfig(turn_timeout_ai_sec=0.2, cucco_window_timeout_ai_sec=0.1), creator_id="p1")


@pytest.mark.asyncio
async def test_stale_action_from_a_previous_prompt_is_drained_not_misapplied():
    table = make_table()
    session = PlayerSession(player_id="p1", name="Bot", player_type="ai", session_token="t", connection=FakeConnection())
    table.add_session(session)
    runner = TableRunner(table)

    # Simulate a late cucco_pass arriving after a previous (already-closed)
    # cucco_window -- it must NOT be consumed by this unrelated turn prompt.
    session.inbox.put_nowait(CuccoPass())

    async def respond_after_delay():
        await asyncio.sleep(0.05)
        session.inbox.put_nowait(NoChangeDeclare())

    task = asyncio.create_task(respond_after_delay())
    action = await runner._prompt(session, "turn", (CambioDeclare, NoChangeDeclare))
    await task

    assert isinstance(action, NoChangeDeclare)


@pytest.mark.asyncio
async def test_wrong_type_response_is_rejected_and_wait_continues():
    table = make_table()
    conn = FakeConnection()
    session = PlayerSession(player_id="p1", name="Bot", player_type="ai", session_token="t", connection=conn)
    table.add_session(session)
    runner = TableRunner(table)

    async def send_wrong_then_right():
        await asyncio.sleep(0.02)
        session.inbox.put_nowait(ContinueDeclare(continue_playing=True))  # wrong type for a turn prompt
        await asyncio.sleep(0.02)
        session.inbox.put_nowait(CambioDeclare())

    task = asyncio.create_task(send_wrong_then_right())
    action = await runner._prompt(session, "turn", (CambioDeclare, NoChangeDeclare))
    await task

    assert isinstance(action, CambioDeclare)
    rejected = [m for m in conn.sent if m["type"] == "action_rejected"]
    assert len(rejected) == 1


@pytest.mark.asyncio
async def test_late_cucco_declare_during_an_unrelated_prompt_is_silently_dropped():
    # docs/protocol/design.md: a cucco_declare/cucco_pass that arrives after
    # its window already closed (pure network-delay timing) must never
    # trigger action_rejected -- unlike any other wrong-type response.
    table = make_table()
    conn = FakeConnection()
    session = PlayerSession(player_id="p1", name="Bot", player_type="ai", session_token="t", connection=conn)
    table.add_session(session)
    runner = TableRunner(table)

    async def send_late_cucco_then_right():
        await asyncio.sleep(0.02)
        session.inbox.put_nowait(CuccoDeclare())
        await asyncio.sleep(0.02)
        session.inbox.put_nowait(CuccoPass())
        await asyncio.sleep(0.02)
        session.inbox.put_nowait(NoChangeDeclare())

    task = asyncio.create_task(send_late_cucco_then_right())
    action = await runner._prompt(session, "turn", (CambioDeclare, NoChangeDeclare))
    await task

    assert isinstance(action, NoChangeDeclare)
    assert not any(m["type"] == "action_rejected" for m in conn.sent)


@pytest.mark.asyncio
async def test_prompt_returns_none_on_timeout_with_no_response():
    table = make_table()
    session = PlayerSession(player_id="p1", name="Bot", player_type="ai", session_token="t", connection=FakeConnection())
    table.add_session(session)
    runner = TableRunner(table)

    action = await runner._prompt(session, "turn", (CambioDeclare, NoChangeDeclare))
    assert action is None


class ScriptedConnection:
    """Answers cucco_window/turn_prompt/dealer_ready from a per-type queue of
    canned responses, and records every event type it receives (in order) so
    a test can assert exactly when a cucco_window arrived relative to the
    holder's own turn_prompt. `cucco_window` may instead be a callable
    (received_so_far) -> action, since a holder can legitimately be offered
    several cucco_windows before their own turn (one per OTHER player's
    resolved turn, not just their own) -- a fixed pop-list can't express
    "keep passing until after my own turn_prompt, then declare"."""

    def __init__(self, session_ref: list, scripts: dict) -> None:
        self.received: list[str] = []
        self._session_ref = session_ref  # 1-item list, filled in after construction
        self._scripts = scripts  # {event_type: [action, ...] | (list[str]) -> action}

    async def send(self, message: str) -> None:
        data = json.loads(message)
        self.received.append(data["type"])
        script = self._scripts.get(data["type"])
        if callable(script):
            self._session_ref[0].inbox.put_nowait(script(self.received))
        elif script:
            self._session_ref[0].inbox.put_nowait(script.pop(0))


class _StubGame:
    def note_deal_played(self) -> None:
        pass


@pytest.mark.asyncio
async def test_cucco_window_fires_before_and_after_the_holders_own_turn():
    # docs/rules/final_rules.md: cucco may be declared regardless of whose
    # turn it is, both before the holder's own turn and immediately after
    # their own cambio/no_change decision resolves. This drives a full deal
    # through TableRunner (not just the domain layer) to confirm the server
    # actually opens a cucco_window for the holder at both points, not just
    # that the domain API would technically permit it if asked.
    config = GameConfig(turn_timeout_ai_sec=1.0, cucco_window_timeout_ai_sec=1.0)
    # 3 seats, dealer p1 -> deal.order = [p2, p3, p1]. p2 is dealt first (acts
    # first, before p3's turn); p3 is dealt クク and acts second.
    deck = Deck.from_fixed_order([Rank.N5, Rank.CUCCO, Rank.N7])
    pot = Pot(["p1", "p2", "p3"], "p1", {"p1": 24, "p2": 24, "p3": 24}, config, random.Random(0), deck=deck)

    table = Table(room_id="ABC123", config=config, creator_id="p1")
    sessions = {}
    for pid, first_action, dealer_ready_script in (
        ("p1", NoChangeDeclare(), [DealerReady()]),
        ("p2", NoChangeDeclare(), []),
        ("p3", NoChangeDeclare(), []),
    ):
        ref = [None]
        scripts = {"turn_prompt": [first_action], "dealer_ready": dealer_ready_script, "cucco_window": []}
        conn = ScriptedConnection(ref, scripts)
        session = PlayerSession(player_id=pid, name=pid, player_type="ai", session_token=pid, connection=conn)
        ref[0] = session
        table.add_session(session)
        sessions[pid] = session

    # p3 holds クク: pass on every window that fires before their own turn
    # (there can be more than one -- each OTHER player's resolved turn also
    # re-opens a window for current holders), then declare cucco on the
    # first window that fires after their own no_change resolves.
    def p3_cucco_response(received_so_far: list[str]) -> object:
        return CuccoDeclare() if "turn_prompt" in received_so_far else CuccoPass()

    sessions["p3"].connection._scripts["cucco_window"] = p3_cucco_response

    runner = TableRunner(table)
    deal = await runner._run_deal(pot, _StubGame())

    p3_events = sessions["p3"].connection.received
    assert p3_events.count("cucco_window") >= 2, p3_events
    turn_prompt_idx = p3_events.index("turn_prompt")
    first_window_idx = p3_events.index("cucco_window")
    last_window_idx = len(p3_events) - 1 - p3_events[::-1].index("cucco_window")
    # At least one window (before p3's own turn) precedes their turn_prompt...
    assert first_window_idx < turn_prompt_idx
    # ...and the window where they actually declared comes after it.
    assert turn_prompt_idx < last_window_idx

    # The declared cucco actually took effect (ended the deal early).
    assert deal.cucco_declared_by == "p3"
