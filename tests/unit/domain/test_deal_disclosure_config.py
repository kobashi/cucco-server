from cucco.domain.cards import Rank
from cucco.domain.config import GameConfig
from cucco.domain.events import ExchangeRefused, PlayerDisqualified
from tests.unit.domain.helpers import build_deal


def test_immediate_disclosure_reveals_card_and_discards_it_right_away():
    config = GameConfig(disqualified_card_disclosure="immediate")
    deal = build_deal({"A": Rank.N5, "B": Rank.JOKER, "C": Rank.N7}, dealer_id="C", config=config)

    events = deal.submit_cambio("A")

    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.card is Rank.JOKER  # revealed immediately
    assert any(entry.card is Rank.JOKER for entry in deal.deck.discard_pile)
    assert deal.deferred_discards == []


def test_deferred_disclosure_hides_card_until_open():
    config = GameConfig(disqualified_card_disclosure="deferred")
    deal = build_deal({"A": Rank.N5, "B": Rank.JOKER, "C": Rank.N7}, dealer_id="C", config=config)

    events = deal.submit_cambio("A")

    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.card is None  # not revealed yet
    assert deal.deck.discard_pile == []
    assert len(deal.deferred_discards) == 1

    # A (the requester) is the one disqualified for receiving the Joker; B
    # never acted and is still the next legal actor.
    deal.submit_no_change("B")
    deal.submit_no_change("C")
    deal.open()
    assert any(entry.card is Rank.JOKER for entry in deal.deck.discard_pile)


def test_disclosure_setting_does_not_change_the_deal_outcome():
    for disclosure in ("immediate", "deferred"):
        config = GameConfig(disqualified_card_disclosure=disclosure)
        deal = build_deal({"A": Rank.N5, "B": Rank.JOKER, "C": Rank.N2}, dealer_id="C", config=config)
        deal.submit_cambio("A")  # A disqualified (received Joker)
        deal.submit_no_change("B")
        deal.submit_no_change("C")
        opened = deal.open()[0]
        assert opened.losers == ("C",)  # same outcome regardless of disclosure timing


def test_horse_house_reveal_setting_controls_revealed_rank_on_refusal():
    deal_off = build_deal(
        {"A": Rank.N5, "B": Rank.HORSE, "C": Rank.N7},
        dealer_id="C",
        config=GameConfig(horse_house_reveal=False),
    )
    events_off = deal_off.submit_cambio("A")
    refusal_off = next(e for e in events_off if isinstance(e, ExchangeRefused))
    assert refusal_off.revealed_rank is None

    deal_on = build_deal(
        {"A": Rank.N5, "B": Rank.HORSE, "C": Rank.N7},
        dealer_id="C",
        config=GameConfig(horse_house_reveal=True),
    )
    events_on = deal_on.submit_cambio("A")
    refusal_on = next(e for e in events_on if isinstance(e, ExchangeRefused))
    assert refusal_on.revealed_rank is Rank.HORSE
