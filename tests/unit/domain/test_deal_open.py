from cucco.domain.cards import Rank
from tests.unit.domain.helpers import build_deal


def test_open_raises_if_turns_remain():
    import pytest

    from cucco.domain.errors import IllegalAction

    deal = build_deal({"A": Rank.N5, "B": Rank.N6, "C": Rank.N7}, dealer_id="C")
    with pytest.raises(IllegalAction):
        deal.open()


def test_open_reports_single_weakest_loser():
    deal = build_deal({"A": Rank.N5, "B": Rank.N2, "C": Rank.N9}, dealer_id="C")
    deal.submit_no_change("A")
    deal.submit_no_change("B")
    deal.submit_no_change("C")

    opened = deal.open()[0]
    assert opened.losers == ("B",)


def test_open_reports_tied_losers():
    deal = build_deal({"A": Rank.N3, "B": Rank.N3, "C": Rank.N9}, dealer_id="C")
    deal.submit_no_change("A")
    deal.submit_no_change("B")
    deal.submit_no_change("C")

    opened = deal.open()[0]
    assert set(opened.losers) == {"A", "B"}


def test_open_moves_all_compared_hands_to_discard_not_just_losers():
    deal = build_deal({"A": Rank.N5, "B": Rank.N2, "C": Rank.N9}, dealer_id="C")
    deal.submit_no_change("A")
    deal.submit_no_change("B")
    deal.submit_no_change("C")

    deal.open()

    discarded_ranks = {entry.card for entry in deal.deck.discard_pile if entry.discarded_via == "open"}
    assert discarded_ranks == {Rank.N5, Rank.N2, Rank.N9}


def test_sole_survivor_of_mid_deal_disqualifications_is_not_a_loser():
    # A requests B (holds 人間) and is disqualified. B never independently
    # acted, so B still gets a normal turn; B no-changes. Only B remains
    # un-disqualified -- B must not be treated as "weakest" against nobody.
    deal = build_deal({"A": Rank.N5, "B": Rank.HUMAN}, dealer_id="B")
    deal.submit_cambio("A")
    assert deal.disqualified == {"A"}
    assert deal.legal_actor() == "B"
    deal.submit_no_change("B")

    opened = deal.open()[0]
    assert opened.losers == ()
    assert opened.hands == {"B": Rank.HUMAN}


def test_full_deal_happy_path_all_no_change():
    deal = build_deal({"A": Rank.N5, "B": Rank.N2, "C": Rank.N9, "D": Rank.N1}, dealer_id="D")
    for pid in ("A", "B", "C"):
        deal.submit_no_change(pid)
    deal.submit_no_change("D")  # dealer declines the deck too

    assert deal.legal_actor() is None
    opened = deal.open()[0]
    assert opened.hands == {"A": Rank.N5, "B": Rank.N2, "C": Rank.N9, "D": Rank.N1}
    assert opened.losers == ("D",)
