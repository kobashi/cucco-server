from cucco.domain.cards import Rank
from cucco.domain.events import DeckExchangeAccepted
from tests.unit.domain.helpers import build_deal


def test_deck_drawn_joker_is_elevated_and_kept_by_the_actor():
    deal = build_deal({"A": Rank.N5, "B": Rank.N6, "C": Rank.N7}, dealer_id="C", deck_tail=[Rank.JOKER])
    deal.submit_no_change("A")
    deal.submit_no_change("B")

    events = deal.submit_cambio("C")

    deck_ex = next(e for e in events if isinstance(e, DeckExchangeAccepted))
    assert deck_ex.new_card is Rank.JOKER
    assert deal.hands["C"] is Rank.JOKER
    assert deal.elevated_joker_holders == {"C"}
    assert deal.disqualified == set()  # the actor is NOT disqualified for keeping it


def test_elevated_joker_beats_a_plain_joker_held_elsewhere_at_open():
    # Only one Joker is in play as a live hand (the other copy sits
    # elsewhere in the deck); after the elevated draw, the elevated holder
    # must NOT be the weakest, even though Joker is normally the weakest rank.
    deal = build_deal({"A": Rank.JOKER, "B": Rank.N6, "C": Rank.N7}, dealer_id="C", deck_tail=[Rank.JOKER])
    deal.submit_no_change("A")
    deal.submit_no_change("B")
    deal.submit_cambio("C")  # C draws the second Joker and becomes elevated

    opened = deal.open()[0]

    assert opened.losers == ("A",)  # the plain, non-elevated Joker is weakest
    assert "C" not in opened.losers
