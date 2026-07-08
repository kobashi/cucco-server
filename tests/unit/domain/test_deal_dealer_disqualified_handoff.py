from cucco.domain.cards import Rank
from cucco.domain.events import DeckExchangeAccepted, PlayerDisqualified
from tests.unit.domain.helpers import build_deal


def test_later_not_yet_acted_seat_inherits_the_deck_exchange_when_dealer_is_disqualified_early():
    # A's chain (Joker, relayed through three house/horse holders) reaches
    # the dealer E, who accepts and is disqualified for receiving it. B, C,
    # D were only relay targets in that single resolution -- none of them
    # has taken their own turn yet. After B and C decline, D actively
    # requests an exchange; with E gone, D's own turn must resolve against
    # the deck, exactly like a dealer's normal final turn would.
    deal = build_deal(
        {"A": Rank.JOKER, "B": Rank.HOUSE, "C": Rank.HORSE, "D": Rank.HOUSE, "E": Rank.N9},
        dealer_id="E",
        deck_tail=[Rank.N10],
    )
    assert deal.order == ["A", "B", "C", "D", "E"]

    events = deal.submit_cambio("A")
    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.player_id == "E"
    assert deal.disqualified == {"E"}

    # B, C, D never individually acted -- they're still legal actors.
    assert deal.legal_actor() == "B"
    deal.submit_no_change("B")
    assert deal.legal_actor() == "C"
    deal.submit_no_change("C")
    assert deal.legal_actor() == "D"

    events2 = deal.submit_cambio("D")

    deck_ex = next(e for e in events2 if isinstance(e, DeckExchangeAccepted))
    assert deck_ex.actor == "D"
    assert deck_ex.new_card is Rank.N10
    assert deck_ex.given_up_card is Rank.HOUSE
    assert deal.hands["D"] is Rank.N10
    assert deal.legal_actor() is None
    assert deal.is_awaiting_open


def test_no_deck_exchange_when_dealer_is_disqualified_during_the_inheriting_seats_own_turn():
    # B's own turn is what disqualifies the dealer C directly (B gives away
    # a Joker by requesting exchange with C). B has therefore already used
    # their own turn in the very act that removed C -- no one remains to
    # inherit a deck-exchange turn, so the deal proceeds straight to open()
    # with no deck exchange at all.
    deal = build_deal({"A": Rank.N5, "B": Rank.JOKER, "C": Rank.N9}, dealer_id="C")
    assert deal.order == ["A", "B", "C"]

    deal.submit_no_change("A")
    events = deal.submit_cambio("B")

    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.player_id == "C"
    assert deal.disqualified == {"C"}
    assert deal.hands["B"] is Rank.N9  # B received C's old plain card, not the deck

    assert deal.legal_actor() is None
    assert deal.is_awaiting_open

    opened = deal.open()[0]
    assert set(opened.hands) == {"A", "B"}
    assert "C" not in opened.hands
