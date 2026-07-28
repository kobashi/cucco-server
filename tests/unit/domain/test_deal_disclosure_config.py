import pytest

from cucco.domain.cards import Rank
from cucco.domain.config import GameConfig
from cucco.domain.events import ExchangeRefused, PlayerDisqualified
from tests.unit.domain.helpers import build_deal

# _DISCLOSURE_FIELD_BY_CAUSE (deal.py) has five entries, but the tests above
# only exercise the two *_refusal causes -- a wrong mapping for either
# *_deck_draw cause would pass the whole suite undetected otherwise (all
# other deck-draw tests use the all-"deferred" default config, where every
# field reads the same). The two tests below close that gap.


def test_immediate_disclosure_names_the_card_but_leaves_it_on_the_table_until_open():
    # The disclosure setting decides WHEN the card is named, not when it joins
    # the discard pile: an immediately-disclosed card stays face-up in front
    # of its ex-holder for the rest of the deal and is collected at open(),
    # the same moment a deferred one is.
    config = GameConfig(joker_disclosure="immediate")
    deal = build_deal({"A": Rank.N5, "B": Rank.JOKER, "C": Rank.N7}, dealer_id="C", config=config)

    events = deal.submit_cambio("A")

    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.card is Rank.JOKER  # named immediately
    assert deal.deck.discard_pile == []  # but not in the pile yet
    assert [pid for pid, _ in deal.revealed_discards] == ["A"]
    assert deal.deferred_discards == []  # revealed, so not the hidden list

    deal.submit_no_change("B")
    deal.submit_no_change("C")
    deal.open()
    assert any(entry.card is Rank.JOKER for entry in deal.deck.discard_pile)
    assert deal.revealed_discards == []


def test_deferred_disclosure_hides_card_until_open():
    config = GameConfig(joker_disclosure="deferred")
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
    # Both settings now hold the card out of the pile until open(), so
    # disclosure timing is purely about what the table is told and when --
    # it cannot change which cards are available to a mid-deal reshuffle.
    # (The one rule that *does* move cards early is
    # config.reshuffle_includes_revealed; see test_deal_reshuffle.py.)
    for disclosure in ("immediate", "deferred"):
        config = GameConfig(joker_disclosure=disclosure)
        deal = build_deal({"A": Rank.N5, "B": Rank.JOKER, "C": Rank.N2}, dealer_id="C", config=config)
        deal.submit_cambio("A")  # A disqualified (received Joker)
        deal.submit_no_change("B")
        deal.submit_no_change("C")
        opened = deal.open()[0]
        assert opened.losers == ("C",)  # same outcome regardless of disclosure timing


def test_human_disclosure_is_independent_of_joker_disclosure():
    # joker_disclosure says "immediate", but this disqualification is
    # human-caused -- it must be governed by human_disclosure instead
    # (docs/rules/final_rules.md 「設定可能なルール」 is per-cause, not global).
    config = GameConfig(joker_disclosure="immediate", human_disclosure="deferred")
    deal = build_deal({"A": Rank.N5, "B": Rank.HUMAN, "C": Rank.N7}, dealer_id="C", config=config)

    events = deal.submit_cambio("A")  # A requests B (人間); A is disqualified

    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.cause == "human_refusal"
    assert dq.player_id == "A"
    assert dq.card is None  # human_disclosure=deferred applies, not joker's "immediate"
    assert deal.deck.discard_pile == []
    assert len(deal.deferred_discards) == 1


def test_cat_disclosure_is_independent_of_the_others():
    config = GameConfig(joker_disclosure="immediate", human_disclosure="immediate", cat_disclosure="deferred")
    # A still holds A's own dealt card (no prior swap), so requesting B (猫)
    # disqualifies A itself (the original holder of A's current card).
    deal = build_deal({"A": Rank.N5, "B": Rank.CAT, "C": Rank.N7}, dealer_id="C", config=config)

    events = deal.submit_cambio("A")

    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.cause == "cat_refusal"
    assert dq.player_id == "A"
    assert dq.card is None  # cat_disclosure=deferred, despite joker/human being immediate
    assert deal.deck.discard_pile == []
    assert len(deal.deferred_discards) == 1


def test_human_deck_draw_disclosure_is_independent_of_joker_disclosure():
    # joker_disclosure says "immediate", but this disqualification is
    # caused by drawing 人間 from the deck -- must be governed by
    # human_disclosure instead.
    config = GameConfig(joker_disclosure="immediate", human_disclosure="deferred")
    deal = build_deal(
        {"A": Rank.N5, "B": Rank.N3, "C": Rank.N7},
        dealer_id="C",
        deck_tail=[Rank.HUMAN],
        config=config,
    )
    deal.submit_no_change("A")
    deal.submit_no_change("B")

    events = deal.submit_cambio("C")  # C (dealer) draws HUMAN from the deck

    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.cause == "human_deck_draw"
    assert dq.player_id == "C"
    assert dq.card is None  # human_disclosure=deferred applies, not joker's "immediate"
    # C's own hand (N7) is deferred, not yet in the shared discard pile.
    assert any(entry.card is Rank.N7 and entry.original_holder == "C" for entry in deal.deferred_discards)


def test_cat_deck_draw_disclosure_is_independent_of_the_others():
    config = GameConfig(joker_disclosure="immediate", human_disclosure="immediate", cat_disclosure="deferred")
    # A <-> B moves A's original N5 to B; B <-> C moves that same card on to
    # C, who then draws CAT from the deck as the dealer -- the original
    # holder of C's current card (A) is disqualified.
    deal = build_deal({"A": Rank.N5, "B": Rank.N6, "C": Rank.N7}, dealer_id="C", deck_tail=[Rank.CAT], config=config)
    deal.submit_cambio("A")
    deal.submit_cambio("B")

    events = deal.submit_cambio("C")

    dq = next(e for e in events if isinstance(e, PlayerDisqualified))
    assert dq.cause == "cat_deck_draw"
    assert dq.player_id == "A"
    assert dq.card is None  # cat_disclosure=deferred, despite joker/human being immediate
    # A's *current* hand (N6, received in the A<->B swap) is what gets
    # deferred -- not A's original N5, which A no longer holds.
    assert any(entry.card is Rank.N6 for entry in deal.deferred_discards)


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


# -- 失格札はディール終了まで場に残る(全5原因) ------------------------------------
#
# The "card stays in front of its ex-holder until the open" rule is a property
# of _disqualify(), which every cause funnels through -- but 道化/人間/猫 have
# three independent disclosure settings and three different ways of picking WHO
# is disqualified, so each is exercised here rather than trusting the shared
# path. In every case the card must be out of `hands` (out of play) and out of
# `deck.discard_pile` (not collected yet) until open() runs.


def _assert_held_until_open(deal, player_id, card, *, revealed):
    """The card left the player's hand but has NOT reached the discard pile --
    it is still sitting in front of them, face-up or face-down."""
    assert player_id in deal.disqualified
    assert player_id not in deal.hands
    assert not any(e.card is card for e in deal.deck.discard_pile), "捨て札に先に入ってしまっている"
    face_up = [(pid, entry.card) for pid, entry in deal.revealed_discards]
    face_down = [entry.card for entry in deal.deferred_discards]
    if revealed:
        assert (player_id, card) in face_up, "表向きで場に残っていない"
        assert card not in face_down, "伏せ札の側に入っている"
    else:
        assert card in face_down, "伏せたまま場に残っていない"
        assert card not in [c for _, c in face_up], "表向きの側に入っている"


@pytest.mark.parametrize("disclosure,revealed", [("immediate", True), ("deferred", False)])
def test_joker_disqualified_card_is_held_until_open(disclosure, revealed):
    config = GameConfig(joker_disclosure=disclosure)
    deal = build_deal({"A": Rank.N5, "B": Rank.JOKER, "C": Rank.N7}, dealer_id="C", config=config)

    deal.submit_cambio("A")  # A receives the Joker and is disqualified

    _assert_held_until_open(deal, "A", Rank.JOKER, revealed=revealed)


@pytest.mark.parametrize("disclosure,revealed", [("immediate", True), ("deferred", False)])
def test_human_refusal_disqualified_card_is_held_until_open(disclosure, revealed):
    config = GameConfig(human_disclosure=disclosure)
    deal = build_deal({"A": Rank.N5, "B": Rank.HUMAN, "C": Rank.N7}, dealer_id="C", config=config)

    deal.submit_cambio("A")  # 人間 refuses: the REQUESTER (A) is disqualified

    # A's own card (N5) is what leaves play -- the 人間 stays with B.
    _assert_held_until_open(deal, "A", Rank.N5, revealed=revealed)
    assert deal.hands["B"] is Rank.HUMAN


@pytest.mark.parametrize("disclosure,revealed", [("immediate", True), ("deferred", False)])
def test_human_deck_draw_disqualified_card_is_held_until_open(disclosure, revealed):
    config = GameConfig(human_disclosure=disclosure)
    deal = build_deal({"A": Rank.N5, "B": Rank.N6, "C": Rank.N7}, dealer_id="C",
                      deck_tail=[Rank.HUMAN], config=config)

    deal.submit_no_change("A")
    deal.submit_no_change("B")
    deal.submit_cambio("C")  # dealer draws 人間 from the deck

    _assert_held_until_open(deal, "C", Rank.N7, revealed=revealed)


@pytest.mark.parametrize("disclosure,revealed", [("immediate", True), ("deferred", False)])
def test_cat_refusal_disqualified_card_is_held_until_open(disclosure, revealed):
    # 猫 disqualifies the ORIGINAL holder of the requester's card -- a third
    # player who is not even part of the exchange.
    config = GameConfig(cat_disclosure=disclosure)
    deal = build_deal({"A": Rank.N5, "B": Rank.N6, "C": Rank.CAT, "D": Rank.N9},
                      dealer_id="D", config=config)

    deal.submit_cambio("A")  # A <-> B: B now holds A's N5
    deal.submit_cambio("B")  # B asks C (the cat) -> A is disqualified

    # A is holding N6 by then; that is the card that leaves play.
    _assert_held_until_open(deal, "A", Rank.N6, revealed=revealed)
    assert deal.hands["C"] is Rank.CAT


@pytest.mark.parametrize("disclosure,revealed", [("immediate", True), ("deferred", False)])
def test_cat_deck_draw_disqualified_card_is_held_until_open(disclosure, revealed):
    config = GameConfig(cat_disclosure=disclosure)
    deal = build_deal({"A": Rank.N5, "B": Rank.N6, "C": Rank.N7}, dealer_id="C",
                      deck_tail=[Rank.CAT], config=config)

    deal.submit_cambio("A")  # A <-> B: B holds A's N5
    deal.submit_cambio("B")  # B <-> C: C holds A's N5
    deal.submit_cambio("C")  # dealer draws 猫 -> A (original holder) is out

    _assert_held_until_open(deal, "A", Rank.N6, revealed=revealed)


@pytest.mark.parametrize("disclosure", ["immediate", "deferred"])
def test_every_cause_reaches_the_discard_pile_at_the_open(disclosure):
    # The other half of the rule: held back, but not lost -- the open collects
    # it. (人間 refusal path; the shared open() flush covers the rest.)
    config = GameConfig(human_disclosure=disclosure)
    deal = build_deal({"A": Rank.N5, "B": Rank.HUMAN, "C": Rank.N7}, dealer_id="C", config=config)

    deal.submit_cambio("A")
    assert deal.deck.discard_pile == []

    deal.submit_no_change("B")
    deal.submit_no_change("C")
    deal.open()

    assert any(e.card is Rank.N5 for e in deal.deck.discard_pile)
    assert deal.revealed_discards == [] and deal.deferred_discards == []
