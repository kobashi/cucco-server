from cucco.domain.cards import Rank
from cucco.domain.events import DeckDrawRefused, ExchangeRefused, PlayerDisqualified
from tests.unit.domain.helpers import build_deal


def test_cat_refusal_disqualifies_original_holder_of_requesters_current_card():
    # A <-> B moves A's original N5 to B before B requests exchange with the
    # cat (C) -- the cat must disqualify A (the original holder of B's
    # current card), not B (the requester) or C (the cat holder).
    deal = build_deal({"A": Rank.N5, "B": Rank.N6, "C": Rank.CAT, "D": Rank.N9}, dealer_id="D")
    assert deal.order == ["A", "B", "C", "D"]

    deal.submit_cambio("A")  # A <-> B: A now holds N6, B now holds N5 (prov A)
    assert deal.hands["B"] is Rank.N5
    assert deal.provenance["B"] == "A"

    events = deal.submit_cambio("B")  # B requests exchange with C (the cat)

    refusal = next(e for e in events if isinstance(e, ExchangeRefused))
    assert refusal.reason == "cat_meow"
    assert refusal.revealed_rank is Rank.CAT
    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.player_id == "A"
    assert dq.cause == "cat_refusal"
    assert deal.disqualified == {"A"}
    # No swap happened: cat keeps holding cat, B keeps holding A's old N5.
    assert deal.hands["C"] is Rank.CAT
    assert deal.hands["B"] is Rank.N5


def test_cat_refusal_fizzles_when_original_holder_already_disqualified():
    # A <-> B: A requests B (holds Joker) and is disqualified for receiving
    # it; B ends up holding A's original N5 (provenance A). B then requests
    # the cat (C): the original holder of B's current card (A) is already
    # disqualified, so the effect fizzles.
    deal = build_deal({"A": Rank.N5, "B": Rank.JOKER, "C": Rank.CAT, "D": Rank.N9}, dealer_id="D")

    deal.submit_cambio("A")  # A <-> B: A holds JOKER (disqualified), B holds N5 (prov A)
    assert deal.disqualified == {"A"}
    assert deal.hands["B"] is Rank.N5
    assert deal.provenance["B"] == "A"

    events = deal.submit_cambio("B")  # B requests exchange with C (the cat)

    refusal = next(e for e in events if isinstance(e, ExchangeRefused))
    assert refusal.reason == "cat_meow"
    dqs = [e for e in events if isinstance(e, PlayerDisqualified)]
    assert dqs == []  # fizzle: A is already gone, no new disqualification
    assert deal.disqualified == {"A"}  # unchanged
    assert deal.hands["C"] is Rank.CAT  # no swap happened


def test_cat_deck_draw_disqualifies_original_holder_of_actors_current_card():
    # A <-> B moves A's original N5 to B; B <-> C moves that same card on
    # to C, who then draws CAT from the deck as the dealer. The original
    # holder of C's current card is A -- A must be disqualified.
    deal = build_deal({"A": Rank.N5, "B": Rank.N6, "C": Rank.N7}, dealer_id="C", deck_tail=[Rank.CAT])

    deal.submit_cambio("A")  # A <-> B: A holds N6, B holds N5 (prov A)
    assert deal.provenance["B"] == "A"
    deal.submit_cambio("B")  # B <-> C: B holds N7, C holds N5 (prov A)
    assert deal.hands["C"] is Rank.N5
    assert deal.provenance["C"] == "A"

    events = deal.submit_cambio("C")  # dealer draws CAT from the deck

    drawn = next(e for e in events if isinstance(e, DeckDrawRefused))
    assert drawn.drawn_rank is Rank.CAT
    assert drawn.reason == "cat_deck_draw"
    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.player_id == "A"
    assert dq.cause == "cat_deck_draw"
    assert deal.disqualified == {"A"}


def test_cat_deck_draw_fizzles_when_original_holder_already_disqualified():
    # A <-> B: A requests B (holds Joker) and is disqualified for receiving
    # it; B ends up holding A's original N5 (provenance A). B <-> C moves
    # that same card on to C, who then draws CAT as the dealer: the
    # original holder (A) is already disqualified, so the effect fizzles.
    deal = build_deal({"A": Rank.N5, "B": Rank.JOKER, "C": Rank.N7}, dealer_id="C", deck_tail=[Rank.CAT])

    deal.submit_cambio("A")  # A <-> B: A holds JOKER (disqualified), B holds N5 (prov A)
    assert deal.disqualified == {"A"}
    assert deal.hands["B"] is Rank.N5
    assert deal.provenance["B"] == "A"

    deal.submit_cambio("B")  # B <-> C: B holds N7, C holds N5 (prov A, already disqualified)
    assert deal.hands["C"] is Rank.N5
    assert deal.provenance["C"] == "A"

    events = deal.submit_cambio("C")  # dealer draws CAT from the deck

    drawn = next(e for e in events if isinstance(e, DeckDrawRefused))
    assert drawn.reason == "cat_deck_draw"
    dqs = [e for e in events if isinstance(e, PlayerDisqualified)]
    assert dqs == []  # fizzle: no new disqualification
    assert deal.disqualified == {"A"}  # unchanged
