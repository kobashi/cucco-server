from cucco.domain.cards import Rank
from tests.unit.domain.helpers import build_deal


def test_provenance_starts_self_mapped():
    deal = build_deal({"A": Rank.N5, "B": Rank.N6, "C": Rank.N7}, dealer_id="C")
    assert deal.provenance == {"A": "A", "B": "B", "C": "C"}


def test_multi_hop_swap_chain_updates_provenance_correctly():
    deal = build_deal({"A": Rank.N5, "B": Rank.N6, "C": Rank.N7, "D": Rank.N8}, dealer_id="D")
    deal.submit_cambio("A")  # A <-> B: A's N5 moves to B
    assert deal.hands["B"] is Rank.N5 and deal.provenance["B"] == "A"
    assert deal.hands["A"] is Rank.N6 and deal.provenance["A"] == "B"

    deal.submit_cambio("B")  # B <-> C: A's N5 (currently held by B) moves to C
    assert deal.hands["C"] is Rank.N5 and deal.provenance["C"] == "A"
    assert deal.hands["B"] is Rank.N7 and deal.provenance["B"] == "C"

    deal.submit_cambio("C")  # C <-> D: A's N5 (currently held by C) moves to D
    assert deal.hands["D"] is Rank.N5 and deal.provenance["D"] == "A"
    assert deal.hands["C"] is Rank.N8 and deal.provenance["C"] == "D"


def test_deck_swap_clears_provenance_for_new_holder_but_keeps_it_for_the_discarded_card():
    deal = build_deal({"A": Rank.N5, "B": Rank.N6, "C": Rank.N7}, dealer_id="C", deck_tail=[Rank.N10])
    deal.submit_no_change("A")
    deal.submit_no_change("B")
    deal.submit_cambio("C")  # dealer draws N10, gives up N7

    assert deal.hands["C"] is Rank.N10
    assert deal.provenance["C"] is None  # deck-origin card has no original holder

    discarded = deal.deck.discard_pile[-1]
    assert discarded.card is Rank.N7
    assert discarded.original_holder == "C"  # the given-up card keeps its own provenance
    assert discarded.discarded_via == "dealer_swap"
