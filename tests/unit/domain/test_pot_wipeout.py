from cucco.domain.cards import Rank
from cucco.domain.events import PlayerLeftPot, PotWipedOut, PotWon
from tests.unit.domain.pot_helpers import make_pot, play_deal_all_no_change


def test_simultaneous_elimination_of_all_remaining_participants_wipes_out_the_pot():
    chips = {"A": 25, "B": 25}
    pot = make_pot(
        ["A", "B"],
        dealer_id="A",
        chips=chips,
        deck_cards=[Rank.N5, Rank.N5],  # order [B, A]: tied at N5
        carried_chips=6,
    )
    pot.deal_number = 3  # next deal is 4 -- adult time

    deal, losers = play_deal_all_no_change(pot)
    assert set(losers) == {"A", "B"}  # tied for weakest

    events = pot.resolve_losers(deal, losers)
    assert {e.player_id for e in events if isinstance(e, PlayerLeftPot)} == {"A", "B"}
    assert pot.eliminated == {"A", "B"}
    assert pot.active_participants() == []

    finalize_events = pot.finalize_deal()
    assert finalize_events == [PotWipedOut(amount=6)]
    assert pot.pot_chips == 6  # unclaimed, left for the caller (Game) to carry forward
    assert chips == {"A": 25, "B": 25}  # nobody's balance changed


def test_last_remaining_participant_wins_the_pooled_chips():
    chips = {"A": 25, "B": 25, "C": 25}
    pot = make_pot(
        ["A", "B", "C"],
        dealer_id="A",
        chips=chips,
        deck_cards=[
            Rank.N2, Rank.N9, Rank.N8,  # deal 4 (adult time), order [B, C, A]: B weakest
            Rank.N9, Rank.N3,           # deal 5, dealer now C -> order [A, C]: C weakest
        ],
    )
    pot.pot_chips = 5  # pretend earlier deals already accumulated this
    pot.deal_number = 3  # next deal is 4 -- adult time

    deal, losers = play_deal_all_no_change(pot)
    assert losers == ("B",)
    pot.resolve_losers(deal, losers)
    assert pot.eliminated == {"B"}
    assert pot.active_participants() == ["A", "C"]
    pot.finalize_deal()
    assert pot.dealer_id == "C"  # rotated (B skipped), pot continues

    deal2, losers2 = play_deal_all_no_change(pot)
    assert losers2 == ("C",)
    pot.resolve_losers(deal2, losers2)
    assert pot.eliminated == {"B", "C"}
    assert pot.active_participants() == ["A"]

    events = pot.finalize_deal()
    assert events == [PotWon(winner="A", amount=5, chips_now=30)]
    assert pot.pot_chips == 0
