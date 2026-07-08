from cucco.domain.cards import Rank
from cucco.domain.config import GameConfig
from cucco.domain.events import DeckDrawRefused, ExchangeRefused, PlayerDisqualified
from tests.unit.domain.helpers import build_deal


def test_requesting_exchange_with_human_disqualifies_the_requester():
    deal = build_deal({"A": Rank.N5, "B": Rank.HUMAN, "C": Rank.N7}, dealer_id="C")

    events = deal.submit_cambio("A")

    refusal = next(e for e in events if isinstance(e, ExchangeRefused))
    assert refusal.reason == "human_refusal"
    assert refusal.revealed_rank is Rank.HUMAN  # always revealed, no config gate
    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.player_id == "A"
    assert dq.cause == "human_refusal"
    # No swap occurred: Human keeps holding Human.
    assert deal.hands["B"] is Rank.HUMAN
    assert "A" not in deal.hands
    assert deal.disqualified == {"A"}


def test_human_revealed_regardless_of_horse_house_reveal_setting():
    config = GameConfig(horse_house_reveal=False)
    deal = build_deal({"A": Rank.N5, "B": Rank.HUMAN, "C": Rank.N7}, dealer_id="C", config=config)
    events = deal.submit_cambio("A")
    refusal = next(e for e in events if isinstance(e, ExchangeRefused))
    assert refusal.revealed_rank is Rank.HUMAN


def test_human_drawn_from_deck_disqualifies_the_deck_exchange_actor():
    # A, B act (no-change), leaving C (the dealer) to draw from the deck.
    deal = build_deal(
        {"A": Rank.N5, "B": Rank.N3, "C": Rank.N7},
        dealer_id="C",
        deck_tail=[Rank.HUMAN],
    )
    deal.submit_no_change("A")
    deal.submit_no_change("B")

    events = deal.submit_cambio("C")

    drawn = next(e for e in events if isinstance(e, DeckDrawRefused))
    assert drawn.drawn_rank is Rank.HUMAN
    assert drawn.reason == "human_deck_draw"
    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.player_id == "C"
    assert dq.cause == "human_deck_draw"
    assert deal.disqualified == {"C"}
    # The drawn Human card itself was discarded immediately with no original holder.
    assert any(entry.card is Rank.HUMAN and entry.original_holder is None for entry in deal.deck.discard_pile)
    # C's own original hand (N7) is deferred (default config), not yet in the shared discard pile.
    assert any(entry.card is Rank.N7 and entry.original_holder == "C" for entry in deal.deferred_discards)
