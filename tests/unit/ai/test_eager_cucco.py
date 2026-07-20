"""Eager クク declaration: enhanced policies fire cucco_declare the moment
they hold クク (it cannot refuse an exchange, so waiting a round leaves it
stealable); baselines keep the historical prompt-only behavior."""

import pytest

from cucco.ai.bot import BotEvent, MockAI
from cucco.ai.policies import make_policy


class ScriptedConn:
    """Feeds a fixed event list to the brain and records what it sends."""

    def __init__(self, events):
        self.player_id = "me"
        self._events = list(events)
        self.sent: list[tuple[str, dict]] = []

    async def send(self, type_, payload=None):
        self.sent.append((type_, payload or {}))

    async def events(self):
        for event in self._events:
            yield event

    def sent_types(self):
        return [t for t, _ in self.sent]


def ev(type_, payload=None):
    return BotEvent(type=type_, payload=payload or {})


async def _run(policy_name, events):
    conn = ScriptedConn(events)
    ai = MockAI(conn, make_policy(policy_name))
    with pytest.raises(RuntimeError):  # scripted stream ends before game_ended
        await ai.play()
    return conn


POT = ev("pot_started", {"participants": ["me", "a", "b"], "chips_now": {}, "pot_chips": 3, "dealer_id": "a"})


@pytest.mark.asyncio
async def test_counting_policy_declares_on_being_dealt_cucco():
    conn = await _run("counting_aggressive", [POT, ev("deal_started", {"your_hand": "クク", "deck_remaining_count": 41})])
    assert "cucco_declare" in conn.sent_types()


@pytest.mark.asyncio
async def test_counting_policy_declares_on_receiving_cucco_mid_deal():
    conn = await _run(
        "counting_conservative",
        [
            POT,
            ev("deal_started", {"your_hand": "5", "deck_remaining_count": 41}),
            # The left neighbor's cambio handed us クク -- no prompt involved.
            ev("exchange_result", {"result": "accepted", "requester": "a", "target": "me", "your_new_card": "クク"}),
        ],
    )
    assert conn.sent_types() == ["ready", "cucco_declare"]


@pytest.mark.asyncio
async def test_counting_policy_declares_once_per_deal_but_resets_next_deal():
    conn = await _run(
        "counting_aggressive",
        [
            POT,
            ev("deal_started", {"your_hand": "クク", "deck_remaining_count": 41}),
            # Another hand-touching event in the same deal: no duplicate.
            ev("exchange_result", {"result": "accepted", "requester": "a", "target": "b"}),
            # Next deal, クク again: a fresh declaration.
            ev("deal_started", {"your_hand": "クク", "deck_remaining_count": 38}),
        ],
    )
    assert conn.sent_types().count("cucco_declare") == 2


@pytest.mark.asyncio
async def test_baseline_matrix_still_waits_for_its_own_prompt():
    conn = await _run("matrix", [POT, ev("deal_started", {"your_hand": "クク", "deck_remaining_count": 41})])
    assert "cucco_declare" not in conn.sent_types()
    # ...but at its own prompt it declares as before.
    conn2 = await _run(
        "matrix",
        [POT, ev("deal_started", {"your_hand": "クク", "deck_remaining_count": 41}), ev("turn_prompt", {"timeout_sec": 10})],
    )
    assert "cucco_declare" in conn2.sent_types()


@pytest.mark.asyncio
async def test_no_eager_declaration_without_cucco():
    conn = await _run("counting_aggressive", [POT, ev("deal_started", {"your_hand": "12", "deck_remaining_count": 41})])
    assert "cucco_declare" not in conn.sent_types()
