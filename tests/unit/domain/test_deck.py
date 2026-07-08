import random

import pytest

from cucco.domain.cards import Rank
from cucco.domain.deck import Deck


def make_deck(seed: int = 0) -> Deck:
    return Deck(random.Random(seed))


def test_deck_starts_with_44_cards_and_empty_discard():
    deck = make_deck()
    assert deck.remaining_count == 44
    assert deck.discard_pile == []


def test_draw_reduces_remaining_count():
    deck = make_deck()
    deck.draw()
    assert deck.remaining_count == 43


def test_discard_appends_entry_with_metadata():
    deck = make_deck()
    deck.discard(Rank.N5, original_holder="alice", via="open")
    assert len(deck.discard_pile) == 1
    entry = deck.discard_pile[0]
    assert entry.card is Rank.N5
    assert entry.original_holder == "alice"
    assert entry.discarded_via == "open"
    assert entry.discarded_at


def test_deck_draw_card_has_no_original_holder():
    deck = make_deck()
    deck.discard(Rank.HORSE, original_holder=None, via="deck_draw")
    assert deck.discard_pile[0].original_holder is None


def test_reshuffle_from_discard_when_draw_pile_exhausted():
    deck = make_deck()
    reshuffled = []
    deck.on_reshuffle = lambda: reshuffled.append(True)

    # Drain the draw pile entirely, discarding each card drawn so the
    # discard pile has exactly 44 cards once the draw pile is empty.
    drawn = []
    for _ in range(44):
        card = deck.draw()
        drawn.append(card)
        deck.discard(card, original_holder=None, via="open")
    assert deck.remaining_count == 0
    assert len(deck.discard_pile) == 44

    # Next draw must trigger a reshuffle-from-discard rather than raising.
    next_card = deck.draw()
    assert next_card in drawn
    assert reshuffled == [True]
    assert deck.discard_pile == []
    assert deck.remaining_count == 43  # 44 moved to draw pile, 1 popped by draw()


def test_draw_raises_if_both_piles_empty():
    deck = make_deck()
    for _ in range(44):
        deck.draw()  # discard nothing, so discard pile stays empty
    with pytest.raises(RuntimeError):
        deck.draw()
