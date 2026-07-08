from cucco.domain.cards import Rank
from cucco.domain.events import ExchangeAccepted, PlayerDisqualified
from tests.unit.domain.helpers import build_deal


def test_requester_who_receives_joker_is_disqualified():
    deal = build_deal({"A": Rank.N5, "B": Rank.JOKER, "C": Rank.N7}, dealer_id="C")

    events = deal.submit_cambio("A")

    accepted = next(e for e in events if isinstance(e, ExchangeAccepted))
    assert accepted.requester_new_card is Rank.JOKER
    dqs = [e for e in events if isinstance(e, PlayerDisqualified)]
    assert len(dqs) == 1
    assert dqs[0].player_id == "A"
    assert dqs[0].cause == "received_joker"
    assert deal.disqualified == {"A"}
    assert "A" not in deal.hands
    assert deal.hands["B"] is Rank.N5


def test_target_who_receives_joker_by_being_given_it_is_disqualified():
    # A holds Joker and actively requests exchange with B on A's own turn --
    # a player is always free to declare cambio regardless of what they hold.
    deal = build_deal({"A": Rank.JOKER, "B": Rank.N5, "C": Rank.N7}, dealer_id="C")

    events = deal.submit_cambio("A")

    accepted = next(e for e in events if isinstance(e, ExchangeAccepted))
    assert accepted.target_new_card is Rank.JOKER
    dqs = [e for e in events if isinstance(e, PlayerDisqualified)]
    assert len(dqs) == 1
    assert dqs[0].player_id == "B"
    assert deal.disqualified == {"B"}
    assert deal.hands["A"] is Rank.N5


def test_mutual_joker_exchange_disqualifies_both_participants():
    deal = build_deal({"A": Rank.JOKER, "B": Rank.JOKER, "C": Rank.N7}, dealer_id="C")

    events = deal.submit_cambio("A")

    dqs = {e.player_id for e in events if isinstance(e, PlayerDisqualified)}
    assert dqs == {"A", "B"}
    assert deal.disqualified == {"A", "B"}
    assert deal.hands == {"C": Rank.N7}


def test_disqualified_jokers_are_deferred_until_open_by_default():
    deal = build_deal({"A": Rank.N5, "B": Rank.JOKER, "C": Rank.N7}, dealer_id="C")
    deal.submit_cambio("A")
    # config default is "deferred": nothing hits the shared discard pile yet.
    assert deal.deck.discard_pile == []
    assert len(deal.deferred_discards) == 1
    assert deal.deferred_discards[0].card is Rank.JOKER
