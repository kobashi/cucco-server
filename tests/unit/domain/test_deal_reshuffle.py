import random

from cucco.domain.cards import Rank
from cucco.domain.config import GameConfig
from cucco.domain.deal import Deal
from cucco.domain.deck import Deck, DiscardEntry
from cucco.domain.events import DeckReshuffled


def test_reshuffle_during_initial_dealing_is_reported_via_take_pending_events():
    # Only 1 card left in the draw pile, but 3 participants need dealing --
    # the 2nd deal-out draw must trigger a mid-construction reshuffle.
    deck = Deck.from_fixed_order([Rank.N5], rng=random.Random(0))
    deck.discard_pile = [
        DiscardEntry(card=Rank.N6, original_holder=None, discarded_via="open"),
        DiscardEntry(card=Rank.N7, original_holder=None, discarded_via="open"),
    ]

    deal = Deal(["A", "B", "C"], dealer_id="C", deck=deck, config=GameConfig())

    pending = deal.take_pending_events()
    assert len(pending) == 1
    assert isinstance(pending[0], DeckReshuffled)
    # Taking events clears them -- a second call returns nothing new.
    assert deal.take_pending_events() == []
