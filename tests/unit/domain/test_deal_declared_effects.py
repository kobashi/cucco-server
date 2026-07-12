"""The effect_declaration="declared" rule variant (docs/rules/final_rules.md
「設定可能なルール」・docs/protocol/design.md): 人間/馬/猫/家 only fire when
their holder actively declares; silence lets the exchange through. Driven via
the step-wise begin_cambio / resolve_* API the runner uses."""

import pytest

from cucco.domain.cards import Rank
from cucco.domain.config import GameConfig
from cucco.domain.errors import IllegalAction
from cucco.domain.events import DeckExchangeAccepted, ExchangeAccepted, ExchangeRefused, PlayerDisqualified
from tests.unit.domain.helpers import build_deal

DECLARED = GameConfig(effect_declaration="declared")


def test_silent_cat_lets_the_exchange_through():
    deal = build_deal({"A": Rank.N5, "B": Rank.CAT, "C": Rank.N7}, dealer_id="C", config=DECLARED)
    events, target = deal.begin_cambio("A")
    assert target == "B"

    events = deal.resolve_exchange_accept("A", "B")
    assert any(isinstance(e, ExchangeAccepted) for e in events)
    assert deal.hands["A"] is Rank.CAT  # the cat moved instead of meowing
    assert deal.hands["B"] is Rank.N5
    assert deal.disqualified == set()


def test_declared_cat_disqualifies_the_requesters_card_origin():
    deal = build_deal({"A": Rank.N5, "B": Rank.CAT, "C": Rank.N7}, dealer_id="C", config=DECLARED)
    _, target = deal.begin_cambio("A")
    events, next_target = deal.resolve_effect_declared("A", target)
    assert next_target is None
    assert any(isinstance(e, ExchangeRefused) and e.reason == "cat_meow" for e in events)
    assert any(isinstance(e, PlayerDisqualified) and e.player_id == "A" for e in events)
    assert "A" in deal.disqualified


def test_silent_human_lets_the_exchange_through():
    deal = build_deal({"A": Rank.N5, "B": Rank.HUMAN, "C": Rank.N7}, dealer_id="C", config=DECLARED)
    deal.begin_cambio("A")
    deal.resolve_exchange_accept("A", "B")
    assert deal.hands["A"] is Rank.HUMAN
    assert deal.disqualified == set()


def test_declared_human_disqualifies_the_requester():
    deal = build_deal({"A": Rank.N5, "B": Rank.HUMAN, "C": Rank.N7}, dealer_id="C", config=DECLARED)
    deal.begin_cambio("A")
    events, next_target = deal.resolve_effect_declared("A", "B")
    assert next_target is None
    assert "A" in deal.disqualified


def test_declared_horse_chains_to_the_next_player_who_may_stay_silent():
    deal = build_deal({"A": Rank.N5, "B": Rank.HORSE, "C": Rank.CAT, "D": Rank.N9}, dealer_id="D", config=DECLARED)
    _, target = deal.begin_cambio("A")
    assert target == "B"

    events, target = deal.resolve_effect_declared("A", "B")  # 馬 declared: skip onward
    assert target == "C"
    assert any(isinstance(e, ExchangeRefused) and e.reason == "house_horse_skip" for e in events)

    events = deal.resolve_exchange_accept("A", "C")  # C stays silent despite holding 猫
    assert any(isinstance(e, ExchangeAccepted) for e in events)
    assert deal.hands["A"] is Rank.CAT
    assert deal.hands["C"] is Rank.N5
    assert deal.disqualified == set()


def test_declared_chain_reaching_the_deck_auto_resolves_there():
    # B is the only seat between A and the dealer... 2 players: A requests,
    # dealer B declares 家 -> nobody after B -> the request goes to the deck,
    # where effects stay automatic.
    deal = build_deal({"A": Rank.N5, "B": Rank.HOUSE}, dealer_id="B", deck_tail=[Rank.N8], config=DECLARED)
    _, target = deal.begin_cambio("A")
    assert target == "B"
    events, next_target = deal.resolve_effect_declared("A", "B")
    assert next_target is None  # resolved against the deck internally
    assert any(isinstance(e, DeckExchangeAccepted) and e.new_card is Rank.N8 for e in events)
    assert deal.hands["A"] is Rank.N8


def test_joker_receipt_stays_automatic_in_declared_mode():
    deal = build_deal({"A": Rank.JOKER, "B": Rank.N4, "C": Rank.N7}, dealer_id="C", config=DECLARED)
    deal.begin_cambio("A")
    events = deal.resolve_exchange_accept("A", "B")  # B has no declarable card
    assert any(isinstance(e, PlayerDisqualified) and e.player_id == "B" and e.cause == "received_joker" for e in events)


def test_resolution_calls_require_a_matching_pending_exchange():
    deal = build_deal({"A": Rank.N5, "B": Rank.CAT, "C": Rank.N7}, dealer_id="C", config=DECLARED)
    with pytest.raises(IllegalAction):
        deal.resolve_exchange_accept("A", "B")  # no begin_cambio yet
    deal.begin_cambio("A")
    with pytest.raises(IllegalAction):
        deal.resolve_effect_declared("A", "C")  # C is not the pending target


def test_cucco_cannot_interrupt_a_pending_exchange_and_open_is_blocked():
    deal = build_deal({"A": Rank.N5, "B": Rank.HORSE, "C": Rank.CUCCO}, dealer_id="C", config=DECLARED)
    deal.begin_cambio("A")
    with pytest.raises(IllegalAction):
        deal.submit_cucco_declare("C")
    with pytest.raises(IllegalAction):
        deal.open()


def test_declaring_with_a_non_declarable_card_is_rejected():
    deal = build_deal({"A": Rank.N5, "B": Rank.N9, "C": Rank.N7}, dealer_id="C", config=DECLARED)
    deal.begin_cambio("A")
    with pytest.raises(IllegalAction):
        deal.resolve_effect_declared("A", "B")
