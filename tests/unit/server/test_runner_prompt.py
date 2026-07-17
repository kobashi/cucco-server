import asyncio
import json
import random
import time

import pytest

from cucco.domain.cards import Rank
from cucco.domain.config import GameConfig
from cucco.domain.deck import Deck
from cucco.domain.pot import Pot
from cucco.protocol.actions import (
    CambioDeclare,
    ContinueDeclare,
    CuccoDeclare,
    DealerReady,
    EffectDeclare,
    EffectPass,
    NoChangeDeclare,
)
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

    # Simulate a late effect_pass arriving after a previous (already-closed)
    # effect_window -- it must NOT be consumed by this unrelated turn prompt.
    session.inbox.put_nowait(EffectPass())

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
async def test_late_effect_answer_during_an_unrelated_prompt_is_silently_dropped():
    # docs/protocol/design.md: an effect_declare/effect_pass that arrives
    # after its window already closed (pure network-delay timing) must never
    # trigger action_rejected -- unlike any other wrong-type response.
    table = make_table()
    conn = FakeConnection()
    session = PlayerSession(player_id="p1", name="Bot", player_type="ai", session_token="t", connection=conn)
    table.add_session(session)
    runner = TableRunner(table)

    async def send_late_effect_then_right():
        await asyncio.sleep(0.02)
        session.inbox.put_nowait(EffectDeclare())
        await asyncio.sleep(0.02)
        session.inbox.put_nowait(NoChangeDeclare())

    task = asyncio.create_task(send_late_effect_then_right())
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
    """Answers prompts/events from a per-type queue of canned responses and
    records every event type it receives (in order). Mirrors dispatch's
    routing faithfully: a scripted `CuccoDeclare` is delivered as the
    fire-and-forget pending flag + table wakeup (never via the inbox), while
    everything else goes through the session inbox like a prompt answer."""

    def __init__(self, session_ref: list, scripts: dict) -> None:
        self.received: list[str] = []
        self.table: Table | None = None  # assigned after table construction
        self._session_ref = session_ref  # 1-item list, filled in after construction
        self._scripts = scripts  # {event_type: [action, ...]}

    async def send(self, message: str) -> None:
        data = json.loads(message)
        self.received.append(data["type"])
        script = self._scripts.get(data["type"])
        if not script:
            return
        action = script.pop(0)
        if isinstance(action, CuccoDeclare):
            self._session_ref[0].pending_cucco = True
            self.table.cucco_wakeup.set()
        else:
            self._session_ref[0].inbox.put_nowait(action)


class _StubGame:
    def note_deal_played(self) -> None:
        pass


def _build_table(config: GameConfig, scripts_by_pid: dict) -> tuple[Table, dict]:
    table = Table(room_id="ABC123", config=config, creator_id="p1")
    sessions = {}
    for pid, scripts in scripts_by_pid.items():
        ref = [None]
        conn = ScriptedConnection(ref, scripts)
        conn.table = table
        session = PlayerSession(player_id=pid, name=pid, player_type="ai", session_token=pid, connection=conn)
        ref[0] = session
        table.add_session(session)
        sessions[pid] = session
    return table, sessions


@pytest.mark.asyncio
async def test_declared_mode_effect_window_declared_and_silent_paths():
    # effect_declaration="declared": the runner must open an effect_window
    # for a declarable-card holder, honor a declared 馬 (skip onward), and
    # treat the NEXT target's silence as acceptance of the exchange.
    config = GameConfig(effect_declaration="declared", turn_timeout_ai_sec=1.0, cucco_window_timeout_ai_sec=1.0)
    # dealer p1 -> order [p2, p3, p4, p1]; p2 requests, p3 holds 馬 and
    # declares (skip), p4 holds 猫 but stays silent -> plain swap p2<->p4.
    deck = Deck.from_fixed_order([Rank.N5, Rank.HORSE, Rank.CAT, Rank.N9])
    pot = Pot(
        ["p1", "p2", "p3", "p4"], "p1", {p: 24 for p in ("p1", "p2", "p3", "p4")}, config, random.Random(0), deck=deck
    )
    table, sessions = _build_table(
        config,
        {
            "p1": {"turn_prompt": [NoChangeDeclare()], "dealer_ready": [DealerReady()]},
            "p2": {"turn_prompt": [CambioDeclare()]},
            "p3": {"turn_prompt": [NoChangeDeclare()], "effect_window": [EffectDeclare()]},
            "p4": {"turn_prompt": [NoChangeDeclare()], "effect_window": [EffectPass()]},
        },
    )

    runner = TableRunner(table)
    deal = await runner._run_deal(pot, _StubGame())

    # p3 was asked and declared; p4 was asked and passed.
    assert "effect_window" in sessions["p3"].connection.received
    assert "effect_window" in sessions["p4"].connection.received
    # The horse skip chained past p3; p4's silence accepted the swap.
    assert deal.hands["p2"] is Rank.CAT
    assert deal.hands["p4"] is Rank.N5
    assert deal.hands["p3"] is Rank.HORSE  # untouched, kept the horse
    assert deal.disqualified == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("plain_holder_answer", [EffectPass(), EffectDeclare()])
async def test_declared_mode_prompts_plain_card_targets_too(plain_holder_answer):
    # Declared mode opens an effect_window for EVERY exchange target, not just
    # declarable-card holders -- otherwise the timing alone (instant swap vs.
    # think-time) would tell the whole table who holds a special card. A
    # plain-card holder's pass confirms the exchange; a bogus effect_declare
    # from a plain card (buggy AI) is likewise treated as accepting.
    config = GameConfig(effect_declaration="declared", turn_timeout_ai_sec=1.0, cucco_window_timeout_ai_sec=1.0)
    # dealer p1 -> order [p2, p3, p1]; p2 requests, p3 holds a plain 7.
    deck = Deck.from_fixed_order([Rank.N5, Rank.N7, Rank.N9])
    pot = Pot(["p1", "p2", "p3"], "p1", {"p1": 24, "p2": 24, "p3": 24}, config, random.Random(0), deck=deck)
    table, sessions = _build_table(
        config,
        {
            "p1": {"turn_prompt": [NoChangeDeclare()], "dealer_ready": [DealerReady()]},
            "p2": {"turn_prompt": [CambioDeclare()]},
            "p3": {"turn_prompt": [NoChangeDeclare()], "effect_window": [plain_holder_answer]},
        },
    )

    runner = TableRunner(table)
    deal = await runner._run_deal(pot, _StubGame())

    # The plain-card holder WAS prompted, and the exchange went through.
    assert "effect_window" in sessions["p3"].connection.received
    assert deal.hands["p2"] is Rank.N7
    assert deal.hands["p3"] is Rank.N5
    assert deal.disqualified == set()


@pytest.mark.asyncio
async def test_cucco_holder_declares_on_their_own_turn():
    # A holder's クク button works during their own turn too: the scripted
    # declare arrives as the async flag and interrupts their turn prompt.
    # dealer p1 -> deal.order = [p2, p3, p1]; p2 (first actor) is dealt クク.
    config = GameConfig(turn_timeout_ai_sec=1.0, cucco_window_timeout_ai_sec=1.0)
    deck = Deck.from_fixed_order([Rank.CUCCO, Rank.N5, Rank.N7])
    pot = Pot(["p1", "p2", "p3"], "p1", {"p1": 24, "p2": 24, "p3": 24}, config, random.Random(0), deck=deck)
    table, sessions = _build_table(
        config,
        {
            "p1": {"turn_prompt": [NoChangeDeclare()], "dealer_ready": [DealerReady()]},
            "p2": {"turn_prompt": [CuccoDeclare()]},  # p2 holds クク, klops on its turn
            "p3": {"turn_prompt": [NoChangeDeclare()]},
        },
    )

    runner = TableRunner(table)
    deal = await runner._run_deal(pot, _StubGame())

    assert deal.cucco_declared_by == "p2"
    # p2 klopped on its own turn; p3 (later in order) was never given a turn.
    assert "turn_prompt" not in sessions["p3"].connection.received


@pytest.mark.asyncio
async def test_cucco_holding_dealer_declares_at_dealer_ready():
    # A dealer holding クク may declare it in place of どうぞ -- the dealer's own
    # turn is last, so this is their one chance to klop before anyone plays.
    # dealer p1 is order[-1], so it is dealt the last card -> クク.
    config = GameConfig(turn_timeout_ai_sec=1.0, cucco_window_timeout_ai_sec=1.0)
    deck = Deck.from_fixed_order([Rank.N5, Rank.N7, Rank.CUCCO])
    pot = Pot(["p1", "p2", "p3"], "p1", {"p1": 24, "p2": 24, "p3": 24}, config, random.Random(0), deck=deck)
    table, sessions = _build_table(
        config,
        {
            "p1": {"dealer_ready": [CuccoDeclare()], "turn_prompt": [NoChangeDeclare()]},
            "p2": {"turn_prompt": [NoChangeDeclare()]},
            "p3": {"turn_prompt": [NoChangeDeclare()]},
        },
    )

    runner = TableRunner(table)
    deal = await runner._run_deal(pot, _StubGame())

    assert deal.cucco_declared_by == "p1"
    # Declared at どうぞ: nobody, not even the first actor p2, was given a turn.
    assert "turn_prompt" not in sessions["p2"].connection.received
    assert "turn_prompt" not in sessions["p3"].connection.received


@pytest.mark.asyncio
async def test_bystander_cucco_interrupts_anothers_turn_immediately():
    # クク is declarable at ANY time outside an atomic exchange -- including
    # while another player is still thinking about their turn. The pending
    # declaration must interrupt that wait immediately, not sit until the
    # actor answers or times out (turn_timeout is 5s here; the assert on
    # elapsed time proves the interrupt, not the timeout, ended the wait).
    config = GameConfig(turn_timeout_ai_sec=5.0, cucco_window_timeout_ai_sec=1.0)
    # dealer p1 -> order [p2, p3, p1]; p3 holds クク; p2 (the first actor)
    # deliberately never answers its turn prompt.
    deck = Deck.from_fixed_order([Rank.N5, Rank.CUCCO, Rank.N7])
    pot = Pot(["p1", "p2", "p3"], "p1", {"p1": 24, "p2": 24, "p3": 24}, config, random.Random(0), deck=deck)
    table, sessions = _build_table(
        config,
        {
            "p1": {"dealer_ready": [DealerReady()], "turn_prompt": [NoChangeDeclare()]},
            "p2": {},  # silent: their turn prompt would run its full 5s
            "p3": {},
        },
    )

    async def klop_mid_turn():
        # Wait until p2's turn prompt is actually out, then declare from p3
        # exactly the way dispatch would (flag + wakeup).
        while "turn_prompt" not in sessions["p2"].connection.received:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)  # mid-think-time
        sessions["p3"].pending_cucco = True
        table.cucco_wakeup.set()

    runner = TableRunner(table)
    started = time.monotonic()
    klop_task = asyncio.create_task(klop_mid_turn())
    deal = await runner._run_deal(pot, _StubGame())
    await klop_task
    elapsed = time.monotonic() - started

    assert deal.cucco_declared_by == "p3"
    # The klop ended the deal well before p2's 5s turn timeout could.
    assert elapsed < 2.0
    # p2's unanswered turn evaporated: no declaration was recorded for them.
    assert all(d.player_id != "p2" for d in deal.declarations)


@pytest.mark.asyncio
async def test_non_dealer_predozo_cucco_is_deferred_until_after_dozo():
    # A non-dealer has no pre-dōzo declaration timing: a klop clicked between
    # deal_started and どうぞ stays pending and takes effect at the first safe
    # point AFTER どうぞ -- before the first turn is ever prompted.
    config = GameConfig(turn_timeout_ai_sec=1.0, cucco_window_timeout_ai_sec=1.0)
    # dealer p1 -> order [p2, p3, p1]; p2 is dealt クク and "clicks" the
    # moment deal_started reaches it (well before どうぞ).
    deck = Deck.from_fixed_order([Rank.CUCCO, Rank.N5, Rank.N7])
    pot = Pot(["p1", "p2", "p3"], "p1", {"p1": 24, "p2": 24, "p3": 24}, config, random.Random(0), deck=deck)
    table, sessions = _build_table(
        config,
        {
            "p1": {"dealer_ready": [DealerReady()], "turn_prompt": [NoChangeDeclare()]},
            "p2": {"deal_started": [CuccoDeclare()]},
            "p3": {"turn_prompt": [NoChangeDeclare()]},
        },
    )

    runner = TableRunner(table)
    deal = await runner._run_deal(pot, _StubGame())

    assert deal.cucco_declared_by == "p2"
    # The dealer still got to say どうぞ (the pre-dōzo klop did not preempt
    # it), and nobody was ever prompted for a turn.
    assert "dealer_ready" in sessions["p1"].connection.received
    assert "turn_prompt" not in sessions["p2"].connection.received
    assert "turn_prompt" not in sessions["p3"].connection.received


@pytest.mark.asyncio
async def test_stale_cucco_flag_from_a_player_whose_card_moved_is_dropped():
    # A pending declaration is only valid while the sender still holds クク:
    # here p3's flag is raised while p3 holds a plain card (e.g. their クク
    # was exchanged away before the flag was examined). The flag must be
    # dropped -- the deal proceeds to a normal open, no crash, no klop.
    config = GameConfig(turn_timeout_ai_sec=1.0, cucco_window_timeout_ai_sec=1.0)
    deck = Deck.from_fixed_order([Rank.N5, Rank.N7, Rank.N9])  # nobody holds クク
    pot = Pot(["p1", "p2", "p3"], "p1", {"p1": 24, "p2": 24, "p3": 24}, config, random.Random(0), deck=deck)
    table, sessions = _build_table(
        config,
        {
            "p1": {"dealer_ready": [DealerReady()], "turn_prompt": [NoChangeDeclare()]},
            "p2": {"turn_prompt": [NoChangeDeclare()], "deal_started": [CuccoDeclare()]},
            "p3": {"turn_prompt": [NoChangeDeclare()]},
        },
    )

    runner = TableRunner(table)
    deal = await runner._run_deal(pot, _StubGame())

    assert deal.cucco_declared_by is None
    assert sessions["p2"].pending_cucco is False  # invalid flag was dropped
    # Everyone played their normal turn.
    assert sum(1 for d in deal.declarations if d.action == "no_change") == 3
