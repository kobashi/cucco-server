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


# -- AIの手番ペーシング (AI_TURN_PACING_SEC) ---------------------------------------
#
# AIs answer instantly, which leaves a human holding クク no window: the dealer's
# どうぞ and the first AI's カンビオ land in the same instant. _race_prompt holds
# an AI's answer while a human is connected -- without ever blocking the klop.


def _seat(table, pid, player_type, *, connected=True):
    s = PlayerSession(player_id=pid, name=pid, player_type=player_type, session_token=pid, connection=FakeConnection())
    s.connected = connected
    table.add_session(s)
    return s


def test_ai_is_paced_only_when_a_human_is_connected():
    table = make_table()
    bot = _seat(table, "ai1", "ai")
    runner = TableRunner(table)

    # AI-only table: nobody is waiting on the pacing, so don't pay for it.
    assert runner._ai_pacing_sec(bot) == 0.0

    human = _seat(table, "h1", "human")
    assert runner._ai_pacing_sec(bot) > 0.0
    # The human's own prompts are never held back.
    assert runner._ai_pacing_sec(human) == 0.0

    # A human who dropped stops counting -- the remaining AIs play at speed.
    human.connected = False
    assert runner._ai_pacing_sec(bot) == 0.0


def test_a_spectator_alone_still_paces_the_ais():
    # An all-AI table watched by a spectator is the whole point of the
    # spectator-creator route; unpaced, the game races to the result and there
    # is nothing to watch.
    table = make_table()
    bot = _seat(table, "ai1", "ai")
    spectator = _seat(table, "s1", "spectator")

    runner = TableRunner(table)
    assert runner._ai_pacing_sec(bot) > 0.0

    # A spectator who closed the tab stops counting, like a dropped human.
    spectator.connected = False
    assert runner._ai_pacing_sec(bot) == 0.0


def _pausing_table() -> Table:
    return Table(room_id="PAUSE1", config=GameConfig(result_pause_sec=0.6), creator_id="ai1")


@pytest.mark.asyncio
async def test_result_pause_waits_for_a_spectator_who_holds_no_seat():
    # No game is running, so nobody is seated -- exactly the all-AI shape.
    table = _pausing_table()
    _seat(table, "ai1", "ai")
    runner = TableRunner(table)

    # AIs never ack, so with no audience the pause is skipped outright.
    started = asyncio.get_event_loop().time()
    await runner._result_pause()
    assert asyncio.get_event_loop().time() - started < 0.3

    # A watching spectator is someone to wait for, seat or no seat.
    _seat(table, "s1", "spectator")
    started = asyncio.get_event_loop().time()
    await runner._result_pause()
    assert asyncio.get_event_loop().time() - started >= 0.5


@pytest.mark.asyncio
async def test_a_spectators_ack_ends_the_result_pause_early():
    table = _pausing_table()
    _seat(table, "ai1", "ai")
    _seat(table, "s1", "spectator")
    runner = TableRunner(table)

    async def ack_soon():
        await asyncio.sleep(0.05)
        table.result_acks.add("s1")

    started = asyncio.get_event_loop().time()
    await asyncio.gather(runner._result_pause(), ack_soon())
    assert asyncio.get_event_loop().time() - started < 0.5


def test_evaluation_mode_is_never_paced():
    table = Table(room_id="EVAL01", config=GameConfig(mode="evaluation", game_count=1), creator_id="ai1")
    bot = _seat(table, "ai1", "ai")
    _seat(table, "h1", "human")  # a human may be watching; throughput still wins
    runner = TableRunner(table)

    assert runner._ai_pacing_sec(bot) == 0.0


def _paced_table() -> Table:
    """A table whose AI turn timeout is comfortably longer than the pacing
    window, so the hold is what the test measures rather than a timeout."""
    return Table(
        room_id="PACE01",
        config=GameConfig(turn_timeout_ai_sec=5.0, cucco_window_timeout_ai_sec=5.0),
        creator_id="ai1",
    )


@pytest.mark.asyncio
async def test_an_ai_answer_is_held_for_the_pacing_window():
    table = _paced_table()
    bot = _seat(table, "ai1", "ai")
    _seat(table, "h1", "human")
    runner = TableRunner(table)
    pot = Pot(["ai1", "h1"], "ai1", {"ai1": 24, "h1": 24}, table.config, random.Random(0))
    deal = pot.start_next_deal()

    async def bot_answers_instantly():
        await asyncio.sleep(0.01)  # the prompt has to go out first
        bot.inbox.put_nowait(NoChangeDeclare())

    task = asyncio.create_task(bot_answers_instantly())
    started = time.monotonic()
    kind, action = await runner._race_prompt(deal, bot, "turn", (CambioDeclare, NoChangeDeclare))
    elapsed = time.monotonic() - started
    await task

    assert kind == "action" and isinstance(action, NoChangeDeclare)
    # Held for the window rather than resolving in microseconds.
    assert elapsed >= 0.5, f"AIの手番が即決していた ({elapsed:.3f}s)"


@pytest.mark.asyncio
async def test_a_human_answer_is_not_held():
    # Only AI seats are paced; a human's own answer applies at once.
    table = _paced_table()
    human = _seat(table, "h1", "human")
    _seat(table, "ai1", "ai")
    runner = TableRunner(table)
    pot = Pot(["ai1", "h1"], "ai1", {"ai1": 24, "h1": 24}, table.config, random.Random(0))
    deal = pot.start_next_deal()

    async def answer():
        await asyncio.sleep(0.01)
        human.inbox.put_nowait(NoChangeDeclare())

    task = asyncio.create_task(answer())
    started = time.monotonic()
    kind, action = await runner._race_prompt(deal, human, "turn", (CambioDeclare, NoChangeDeclare))
    elapsed = time.monotonic() - started
    await task

    assert kind == "action" and isinstance(action, NoChangeDeclare)
    assert elapsed < 0.3, f"人間の手番まで待たされている ({elapsed:.3f}s)"


@pytest.mark.asyncio
async def test_a_cucco_declared_inside_the_pacing_window_still_wins():
    # The whole point of the hold: a human who klops during it must stop the
    # deal, not have the AI's already-queued answer applied first.
    table = _paced_table()
    bot = _seat(table, "ai1", "ai")
    human = _seat(table, "h1", "human")
    runner = TableRunner(table)
    pot = Pot(["ai1", "h1"], "ai1", {"ai1": 24, "h1": 24}, table.config, random.Random(0))
    deal = pot.start_next_deal()
    deal.hands["h1"] = Rank.CUCCO  # the human is holding クク

    async def bot_answers():
        await asyncio.sleep(0.01)
        bot.inbox.put_nowait(NoChangeDeclare())  # AI answered immediately

    answer_task = asyncio.create_task(bot_answers())

    async def human_klops():
        await asyncio.sleep(0.1)  # well inside the pacing window
        human.pending_cucco = True  # dispatch sets this on an out-of-band cucco_declare
        table.cucco_wakeup.set()

    task = asyncio.create_task(human_klops())
    kind, declarer = await runner._race_prompt(deal, bot, "turn", (CambioDeclare, NoChangeDeclare))
    await task
    await answer_task

    assert kind == "cucco" and declarer == "h1", "ペーシング中のクク宣言が負けている"
