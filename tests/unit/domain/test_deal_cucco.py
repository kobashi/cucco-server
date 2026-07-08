import pytest

from cucco.domain.cards import Rank
from cucco.domain.errors import IllegalAction
from cucco.domain.events import CuccoDeclared
from tests.unit.domain.helpers import build_deal


def test_cucco_can_be_declared_out_of_turn():
    deal = build_deal({"A": Rank.N5, "B": Rank.CUCCO, "C": Rank.N7}, dealer_id="C")
    # It is A's turn, but B (not currently active) may still declare cucco.
    assert deal.legal_actor() == "A"

    events = deal.submit_cucco_declare("B")

    assert any(isinstance(e, CuccoDeclared) and e.player_id == "B" for e in events)
    assert deal.cucco_declared_by == "B"


def test_cucco_declaration_freezes_hands_and_excludes_not_yet_acted_players_from_further_action():
    deal = build_deal({"A": Rank.N5, "B": Rank.CUCCO, "C": Rank.N2}, dealer_id="C")
    deal.submit_cucco_declare("B")

    with pytest.raises(IllegalAction):
        deal.submit_cambio("A")  # no further turns after cucco is declared
    with pytest.raises(IllegalAction):
        deal.submit_no_change("C")

    opened = deal.open()[0]
    assert opened.hands == {"A": Rank.N5, "B": Rank.CUCCO, "C": Rank.N2}
    assert opened.losers == ("C",)  # weakest among hands AS THEY STOOD at declaration


def test_cucco_holder_may_decline_via_pass_with_no_public_event():
    deal = build_deal({"A": Rank.N5, "B": Rank.CUCCO, "C": Rank.N7}, dealer_id="C")
    deal.submit_cucco_pass("B")
    assert deal.cucco_declared_by is None
    assert deal.declarations == []  # cucco_pass is never publicly recorded


def test_only_the_current_cucco_holder_may_declare_or_pass():
    deal = build_deal({"A": Rank.N5, "B": Rank.CUCCO, "C": Rank.N7}, dealer_id="C")
    with pytest.raises(IllegalAction):
        deal.submit_cucco_declare("A")  # A does not hold クク
    with pytest.raises(IllegalAction):
        deal.submit_cucco_pass("C")  # C does not hold クク either


def test_disqualified_player_cannot_declare_cucco_even_if_still_holding_it_deferred():
    from cucco.domain.config import GameConfig

    deal = build_deal(
        {"A": Rank.JOKER, "B": Rank.CUCCO, "C": Rank.N7},
        dealer_id="C",
        config=GameConfig(disqualified_card_disclosure="deferred"),
    )
    # Manually disqualify B to simulate a disqualification while B still
    # physically "holds" クク under deferred disclosure.
    deal.disqualified.add("B")

    with pytest.raises(IllegalAction):
        deal.submit_cucco_declare("B")
    with pytest.raises(IllegalAction):
        deal.submit_cucco_pass("B")
    assert "B" not in deal.current_cucco_holders()


def test_cucco_target_can_be_exchanged_away_before_declaration():
    # クク has no refusal ability: it can be won away from its holder just
    # like any plain card, before it's ever declared.
    deal = build_deal({"A": Rank.N5, "B": Rank.CUCCO, "C": Rank.N7}, dealer_id="C")
    deal.submit_cambio("A")  # A <-> B: A now holds クク
    assert deal.hands["A"] is Rank.CUCCO
    assert deal.current_cucco_holders() == {"A"}

    # The new holder (A) now has the declaration right, not B.
    with pytest.raises(IllegalAction):
        deal.submit_cucco_declare("B")
