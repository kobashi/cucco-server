from cucco.domain.cards import Rank
from cucco.domain.events import (
    DeckExchangeAccepted,
    ExchangeAccepted,
    ExchangeRefused,
    PlayerDisqualified,
)
from tests.unit.domain.helpers import build_deal


def test_chain_skips_a_seat_already_disqualified_by_an_earlier_turn():
    # A's turn: A requests B (house) -> refused -> chain relays to C, who
    # accepts and receives A's Joker, disqualifying C. B never took an
    # active turn (it was only a refusing target), so B is still a legal
    # actor afterward. B's own turn must then skip the now-disqualified C
    # and target D directly.
    deal = build_deal(
        {"A": Rank.JOKER, "B": Rank.HOUSE, "C": Rank.N7, "D": Rank.N8, "E": Rank.N9},
        dealer_id="E",
    )
    assert deal.order == ["A", "B", "C", "D", "E"]

    events = deal.submit_cambio("A")
    refusal = next(e for e in events if isinstance(e, ExchangeRefused))
    assert refusal.target == "B"
    assert refusal.reason == "house_horse_skip"
    accepted = next(e for e in events if isinstance(e, ExchangeAccepted))
    assert accepted.target == "C"
    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.player_id == "C"
    assert deal.disqualified == {"C"}

    # B never acted; B is still the next legal actor.
    assert deal.legal_actor() == "B"

    events2 = deal.submit_cambio("B")
    accepted2 = next(e for e in events2 if isinstance(e, ExchangeAccepted))
    # The chain from B must skip disqualified C and land on D.
    assert accepted2.requester == "B"
    assert accepted2.target == "D"
    assert deal.hands["D"] is Rank.HOUSE  # B's house card was given to D
    assert deal.hands["B"] is Rank.N8


def test_chain_reaching_end_of_turn_order_resolves_as_deck_exchange_by_the_original_requester():
    # The worked example from docs/rules/final_rules.md: the player right
    # before the dealer requests exchange with the dealer; the dealer holds
    # horse/house and refuses; the request resolves against the deck,
    # attributed to the ORIGINAL REQUESTER, not the literal dealer.
    deal = build_deal({"A": Rank.N5, "B": Rank.N6, "C": Rank.HORSE}, dealer_id="C", deck_tail=[Rank.N10])
    assert deal.order == ["A", "B", "C"]

    deal.submit_no_change("A")
    events = deal.submit_cambio("B")

    refusal = next(e for e in events if isinstance(e, ExchangeRefused))
    assert refusal.requester == "B"
    assert refusal.target == "C"
    deck_ex = next(e for e in events if isinstance(e, DeckExchangeAccepted))
    assert deck_ex.actor == "B"
    assert deck_ex.new_card is Rank.N10
    assert deck_ex.given_up_card is Rank.N6
    assert deal.hands["B"] is Rank.N10
    assert deal.hands["C"] is Rank.HORSE  # the literal dealer's hand is untouched

    # C only ever REFUSED as a chain relay target -- C never independently
    # acted, so C still gets their own normal turn afterward, even though
    # B's chain already resolved against the deck on B's own behalf.
    assert deal.legal_actor() == "C"
    assert not deal.is_awaiting_open

    # C's own turn is a second, independent exchange with the deck.
    events2 = deal.submit_cambio("C")
    deck_ex2 = next(e for e in events2 if isinstance(e, DeckExchangeAccepted))
    assert deck_ex2.actor == "C"
    assert deck_ex2.given_up_card is Rank.HORSE
    assert deal.hands["C"] is not Rank.HORSE
    assert deal.legal_actor() is None
    assert deal.is_awaiting_open


def test_chain_never_loops_back_to_the_original_requester():
    # Every other seat holds house/horse; the chain must run all the way to
    # the deck rather than wrapping back around to the requester.
    deal = build_deal(
        {"A": Rank.N5, "B": Rank.HOUSE, "C": Rank.HORSE, "D": Rank.HOUSE},
        dealer_id="D",
        deck_tail=[Rank.N11],
    )
    assert deal.order == ["A", "B", "C", "D"]

    events = deal.submit_cambio("A")

    refusals = [e for e in events if isinstance(e, ExchangeRefused)]
    assert [r.target for r in refusals] == ["B", "C", "D"]
    deck_ex = next(e for e in events if isinstance(e, DeckExchangeAccepted))
    assert deck_ex.actor == "A"
    assert deck_ex.new_card is Rank.N11
    # A's hand changed via the deck, never via looping back to itself.
    assert deal.hands["A"] is Rank.N11
    assert deal.hands["B"] is Rank.HOUSE
    assert deal.hands["C"] is Rank.HORSE
    assert deal.hands["D"] is Rank.HOUSE

    # B, C, and D only ever refused as chain relay targets -- none of them
    # independently acted, so they each still have their own turn to take.
    assert deal.legal_actor() == "B"
