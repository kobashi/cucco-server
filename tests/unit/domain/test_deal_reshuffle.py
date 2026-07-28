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


# -- 山札の再構成に表向きの失格札を含める設定 (config.reshuffle_includes_revealed) ----
#
# Default rule: a disqualified player's card sits face-up in front of them
# until the deal opens, so a mid-deal reshuffle rebuilds from the discard pile
# alone. Sweep rule: those face-up cards are gathered in first.


def _deal_with_a_face_up_disqualified_card(*, sweep: bool) -> Deal:
    """A disqualifies himself by taking the Joker off B, leaving B's old card
    face-up in front of A. The draw pile is then empty, so the dealer's next
    deck draw has to reshuffle."""
    config = GameConfig(joker_disclosure="immediate", reshuffle_includes_revealed=sweep)
    # A, B, C hands + nothing left over: the deck runs dry after the deal-out.
    deck = Deck.from_fixed_order([Rank.N5, Rank.JOKER, Rank.N7], rng=random.Random(0))
    deal = Deal(["A", "B", "C"], dealer_id="C", deck=deck, config=config)
    deal.submit_cambio("A")  # A receives the Joker and is disqualified
    return deal


def test_by_default_a_face_up_disqualified_card_stays_out_of_a_mid_deal_reshuffle():
    deal = _deal_with_a_face_up_disqualified_card(sweep=False)
    assert [pid for pid, _ in deal.revealed_discards] == ["A"]
    assert deal.deck.discard_pile == []

    assert deal.deck.remaining_count == 0

    # Give the pile exactly one card. The rebuild must produce that card and
    # only that card -- never the Joker sitting face-up in front of A.
    deal.deck.discard_pile = [DiscardEntry(card=Rank.N9, original_holder=None, discarded_via="open")]
    card = deal.deck.draw()

    assert card is Rank.N9
    assert [pid for pid, _ in deal.revealed_discards] == ["A"]  # untouched by the rebuild
    assert deal.take_pending_events()[0].swept_seats == ()


def test_sweep_rule_gathers_face_up_cards_into_the_pile_before_rebuilding():
    deal = _deal_with_a_face_up_disqualified_card(sweep=True)
    assert [pid for pid, _ in deal.revealed_discards] == ["A"]
    assert deal.deck.discard_pile == []
    assert deal.deck.remaining_count == 0

    # The draw pile is empty and the discard pile holds nothing -- only the
    # face-up card in front of A can supply the rebuild, so this draw proves
    # the sweep ran (it would raise "both piles empty" otherwise).
    card = deal.deck.draw()

    assert card is Rank.JOKER  # A's face-up card, back in play
    assert deal.revealed_discards == []  # swept off the table
    assert deal.take_pending_events()[0].swept_seats == ("A",)


def test_swept_seats_is_reported_once_and_not_repeated_on_the_next_reshuffle():
    deal = _deal_with_a_face_up_disqualified_card(sweep=True)
    deal.deck.draw()  # sweeps A's card in and rebuilds
    first = deal.take_pending_events()
    assert first[0].swept_seats == ("A",)

    # A second reshuffle with nothing face-up left reports no swept seats.
    deal.deck._draw.clear()
    deal.deck.discard_pile = [DiscardEntry(card=Rank.N9, original_holder=None, discarded_via="open")]
    deal.deck.draw()
    assert deal.take_pending_events()[0].swept_seats == ()


def test_a_swept_card_is_not_discarded_twice_when_the_deal_opens():
    deal = _deal_with_a_face_up_disqualified_card(sweep=True)
    deal.deck.draw()  # the Joker goes back into the draw pile
    deal.take_pending_events()

    deal.submit_no_change("B")
    deal.submit_no_change("C")
    deal.open()

    # open() flushes revealed_discards, which the sweep already emptied --
    # the Joker must not reappear in the pile as a phantom second copy.
    assert [e.card for e in deal.deck.discard_pile].count(Rank.JOKER) == 0
