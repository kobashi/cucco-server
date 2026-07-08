from cucco.domain.cards import Rank
from cucco.domain.events import DealerChanged
from tests.unit.domain.pot_helpers import make_pot, play_deal_all_no_change


def test_dealer_rotation_skips_eliminated_seats():
    chips = {"A": 25, "B": 25, "C": 25, "D": 25}
    pot = make_pot(
        ["A", "B", "C", "D"],
        dealer_id="A",
        chips=chips,
        deck_cards=[Rank.N2, Rank.N9, Rank.N8, Rank.N9],  # order [B, C, D, A]: B weakest
    )
    pot.deal_number = 3  # next deal is 4 -- adult time, immediate elimination

    deal, losers = play_deal_all_no_change(pot)
    assert losers == ("B",)
    pot.resolve_losers(deal, losers)
    assert pot.eliminated == {"B"}

    events = pot.finalize_deal()
    assert events == [DealerChanged(player_id="C")]  # B (next after A) is skipped
    assert pot.dealer_id == "C"


def test_dealer_rotation_wraps_around_the_table():
    chips = {"A": 25, "B": 25, "C": 25}
    pot = make_pot(
        ["A", "B", "C"],
        dealer_id="C",
        chips=chips,
        deck_cards=[Rank.N2, Rank.N9, Rank.N8],  # order for dealer=C: [A, B, C]
    )
    deal, losers = play_deal_all_no_change(pot)
    events = pot.resolve_losers(deal, losers)
    pot.submit_continue_declare(losers[0], True)

    finalize_events = pot.finalize_deal()
    assert finalize_events == [DealerChanged(player_id="A")]  # wraps from C back to A
