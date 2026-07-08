from cucco.domain.cards import Rank
from cucco.domain.events import Declaration, ExchangeAccepted
from tests.unit.domain.helpers import build_deal


def test_numeral_target_must_accept_exchange():
    deal = build_deal({"A": Rank.N5, "B": Rank.N3, "C": Rank.N7}, dealer_id="C")
    assert deal.order == ["A", "B", "C"]

    events = deal.submit_cambio("A")

    assert events[0] == Declaration(player_id="A", action="cambio")
    accepted = [e for e in events if isinstance(e, ExchangeAccepted)]
    assert len(accepted) == 1
    assert accepted[0].requester == "A"
    assert accepted[0].target == "B"
    assert accepted[0].requester_new_card is Rank.N3
    assert accepted[0].target_new_card is Rank.N5
    assert deal.hands["A"] is Rank.N3
    assert deal.hands["B"] is Rank.N5
    assert deal.disqualified == set()


def test_no_change_swaps_nothing():
    deal = build_deal({"A": Rank.N5, "B": Rank.N3, "C": Rank.N7}, dealer_id="C")
    events = deal.submit_no_change("A")
    assert events == [Declaration(player_id="A", action="no_change")]
    assert deal.hands == {"A": Rank.N5, "B": Rank.N3, "C": Rank.N7}


def test_cucco_target_must_accept_exchange_like_a_plain_card():
    deal = build_deal({"A": Rank.N5, "B": Rank.CUCCO, "C": Rank.N7}, dealer_id="C")
    events = deal.submit_cambio("A")
    accepted = [e for e in events if isinstance(e, ExchangeAccepted)]
    assert accepted[0].requester_new_card is Rank.CUCCO
    assert accepted[0].target_new_card is Rank.N5
    assert deal.hands["A"] is Rank.CUCCO
    assert deal.disqualified == set()


def test_bucket_mask_lion_are_plain_ranks_with_no_effect():
    for weak_rank in (Rank.BUCKET, Rank.MASK, Rank.LION):
        deal = build_deal({"A": Rank.N5, "B": weak_rank, "C": Rank.N7}, dealer_id="C")
        deal.submit_cambio("A")
        assert deal.hands["A"] is weak_rank
        assert deal.disqualified == set()
