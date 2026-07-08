import pytest

from cucco.domain.cards import Rank
from cucco.domain.errors import IllegalAction
from cucco.domain.events import ChipsPaid, ContinuePrompted, PlayerLeftPot
from tests.unit.domain.pot_helpers import make_pot, play_deal_all_no_change


def test_child_time_payment_escalates_with_deal_number():
    chips = {"A": 25, "B": 25, "C": 25}
    pot = make_pot(
        ["A", "B", "C"],
        dealer_id="A",
        chips=chips,
        deck_cards=[
            Rank.N2, Rank.N9, Rank.N8,  # deal 1, order [B, C, A]: B weakest
            Rank.N3, Rank.N9, Rank.N8,  # deal 2, order [C, A, B]: C weakest
            Rank.N1, Rank.N9, Rank.N8,  # deal 3, order [A, B, C]: A weakest
        ],
    )

    # Deal 1: B loses, pays 1 chip, stays in.
    deal1, losers1 = play_deal_all_no_change(pot)
    assert losers1 == ("B",)
    events1 = pot.resolve_losers(deal1, losers1)
    assert events1 == [ContinuePrompted(player_id="B", required_chips=1)]
    paid1 = pot.submit_continue_declare("B", True)
    assert paid1 == [ChipsPaid(player_id="B", amount=1, chips_now=24)]
    pot.finalize_deal()
    assert pot.dealer_id == "B"
    assert pot.pot_chips == 1

    # Deal 2: C loses, pays 2 chips.
    deal2, losers2 = play_deal_all_no_change(pot)
    assert losers2 == ("C",)
    pot.resolve_losers(deal2, losers2)
    paid2 = pot.submit_continue_declare("C", True)
    assert paid2 == [ChipsPaid(player_id="C", amount=2, chips_now=23)]
    pot.finalize_deal()
    assert pot.dealer_id == "C"
    assert pot.pot_chips == 3

    # Deal 3: A loses, pays 3 chips.
    deal3, losers3 = play_deal_all_no_change(pot)
    assert losers3 == ("A",)
    pot.resolve_losers(deal3, losers3)
    paid3 = pot.submit_continue_declare("A", True)
    assert paid3 == [ChipsPaid(player_id="A", amount=3, chips_now=22)]
    pot.finalize_deal()
    assert pot.pot_chips == 6
    assert pot.eliminated == set()  # nobody eliminated yet -- still child time


def test_adult_time_loser_is_eliminated_without_payment():
    chips = {"A": 25, "B": 25, "C": 25}
    pot = make_pot(
        ["A", "B", "C"],
        dealer_id="A",
        chips=chips,
        deck_cards=[Rank.N2, Rank.N9, Rank.N8],
    )
    pot.deal_number = 3  # start_next_deal() will bump this to 4 (adult time)

    deal, losers = play_deal_all_no_change(pot)
    assert losers == ("B",)
    events = pot.resolve_losers(deal, losers)
    assert events == [PlayerLeftPot(player_id="B", reason="adult_time")]
    assert pot.eliminated == {"B"}
    assert chips["B"] == 25  # unchanged -- no payment in adult time


def test_insufficient_chips_forces_elimination_even_in_child_time():
    chips = {"A": 25, "B": 2, "C": 25}  # B can't afford deal 3's 3-chip payment
    pot = make_pot(
        ["A", "B", "C"],
        dealer_id="A",
        chips=chips,
        deck_cards=[Rank.N2, Rank.N9, Rank.N8],
    )
    pot.deal_number = 2  # next deal is number 3

    deal, losers = play_deal_all_no_change(pot)
    assert losers == ("B",)
    events = pot.resolve_losers(deal, losers)
    assert events == [PlayerLeftPot(player_id="B", reason="insolvent")]
    assert pot.eliminated == {"B"}
    assert chips["B"] == 2  # unchanged


def test_declining_to_continue_eliminates_the_player():
    chips = {"A": 25, "B": 25, "C": 25}
    pot = make_pot(
        ["A", "B", "C"],
        dealer_id="A",
        chips=chips,
        deck_cards=[Rank.N2, Rank.N9, Rank.N8],
    )
    deal, losers = play_deal_all_no_change(pot)
    pot.resolve_losers(deal, losers)

    events = pot.submit_continue_declare("B", False)
    assert events == [PlayerLeftPot(player_id="B", reason="declined")]
    assert pot.eliminated == {"B"}
    assert chips["B"] == 25  # declining doesn't cost the payment


def test_multiple_simultaneous_losers_are_prompted_in_dealer_first_order():
    chips = {"A": 25, "B": 25, "C": 25, "D": 25}
    # Tie the weakest cards between two players.
    pot = make_pot(
        ["A", "B", "C", "D"],
        dealer_id="A",
        chips=chips,
        deck_cards=[Rank.N3, Rank.N3, Rank.N9, Rank.N9],  # order [B, C, D, A]
    )
    deal, losers = play_deal_all_no_change(pot)
    assert set(losers) == {"B", "C"}

    events = pot.resolve_losers(deal, losers)
    prompted_order = [e.player_id for e in events if isinstance(e, ContinuePrompted)]
    # Dealer is A; seating order from A is [A, B, C, D] -- B before C.
    assert prompted_order == ["B", "C"]


def test_finalize_deal_raises_while_continue_declare_is_pending():
    chips = {"A": 25, "B": 25, "C": 25}
    pot = make_pot(["A", "B", "C"], dealer_id="A", chips=chips, deck_cards=[Rank.N2, Rank.N9, Rank.N8])
    deal, losers = play_deal_all_no_change(pot)
    pot.resolve_losers(deal, losers)
    with pytest.raises(IllegalAction):
        pot.finalize_deal()
