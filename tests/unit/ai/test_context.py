"""CountingTracker: the shared observation state for enhanced policies."""

from cucco.ai.bot import BotEvent
from cucco.ai.context import CountingTracker, PolicyContext


def ev(type_: str, payload: dict | None = None) -> BotEvent:
    return BotEvent(type=type_, payload=payload or {})


def fresh_tracker() -> CountingTracker:
    t = CountingTracker()
    t.observe(ev("pot_started", {"participants": ["a", "b", "c"], "chips_now": {}, "pot_chips": 3, "dealer_id": "a"}))
    t.observe(ev("deal_started", {"deck_remaining_count": 41}))
    return t


def test_full_deck_minus_own_hand():
    t = fresh_tracker()
    counts = t.unseen_counts("7")
    assert counts["7"] == 1
    assert counts["クク"] == 2
    assert sum(counts.values()) == 43


def test_deal_result_discards_are_authoritative_and_persist_across_deals():
    t = fresh_tracker()
    t.observe(
        ev(
            "deal_result",
            {
                "discarded_cards": [
                    {"card": "道化", "original_holder": "b", "discarded_via": "opened"},
                    {"card": "3", "original_holder": "c", "discarded_via": "opened"},
                ]
            },
        )
    )
    t.observe(ev("deal_started", {"deck_remaining_count": 38}))
    counts = t.unseen_counts(None)
    assert counts["道化"] == 1
    assert counts["3"] == 1


def test_mid_deal_reveals_do_not_double_count_after_deal_result():
    t = fresh_tracker()
    # A deck draw refusal reveals 人間 mid-deal...
    t.observe(ev("exchange_result", {"result": "deck_draw_refused", "actor": "a", "drawn_rank": "人間", "reason": "human_deck_draw"}))
    assert t.unseen_counts(None)["人間"] == 1
    # ...and the same card then appears in the authoritative discard list.
    t.observe(
        ev("deal_result", {"discarded_cards": [{"card": "人間", "original_holder": None, "discarded_via": "deck_draw"}]})
    )
    assert t.unseen_counts(None)["人間"] == 1  # not 0


def test_reshuffle_resets_the_counting():
    t = fresh_tracker()
    t.observe(ev("deal_result", {"discarded_cards": [{"card": "5", "original_holder": "b", "discarded_via": "opened"}]}))
    assert t.unseen_counts(None)["5"] == 1
    t.observe(ev("deck_reshuffled", {"remaining_count": 40}))
    assert t.unseen_counts(None)["5"] == 2


def test_refusal_reveals_the_targets_held_card_and_it_travels_on_exchange():
    t = fresh_tracker()
    t.observe(
        ev("exchange_result", {"result": "refused", "requester": "a", "target": "b", "reason": "cat_meow", "revealed_rank": "猫"})
    )
    assert t.known_held == {"b": "猫"}
    assert t.unseen_counts(None, {"a", "b", "c"})["猫"] == 1
    # b later exchanges with c: the known card moves with it.
    t.observe(ev("exchange_result", {"result": "accepted", "requester": "b", "target": "c"}))
    assert t.known_held == {"c": "猫"}
    # A dead holder's card is no longer "held" (covered by discard layers).
    t.observe(ev("player_disqualified", {"player_id": "c", "cause": "received_joker", "card": None}))
    assert t.known_held == {}


def test_opened_hands_count_until_the_deal_result_supersedes_them():
    t = fresh_tracker()
    t.observe(ev("deal_opened", {"hands": {"a": "12", "b": "道化", "c": "家"}, "elevated_joker_holders": [], "losers": ["b"]}))
    counts = t.unseen_counts(None)
    assert counts["12"] == 1 and counts["道化"] == 1 and counts["家"] == 1


def test_immediate_disclosure_disqualification_counts_the_card():
    t = fresh_tracker()
    t.observe(ev("player_disqualified", {"player_id": "b", "cause": "received_joker", "card": "道化"}))
    assert t.unseen_counts(None)["道化"] == 1
    # Deferred disclosure sends null: nothing to count yet.
    t.observe(ev("player_disqualified", {"player_id": "c", "cause": "cat_refusal", "card": None}))
    assert sum(t.unseen_counts(None).values()) == 43


def test_turn_action_and_dealer_tracking():
    t = fresh_tracker()
    assert t.dealer_id == "a" and t.deal_number == 1
    t.observe(ev("no_change_declared", {"player_id": "b"}))
    t.observe(ev("exchange_result", {"result": "accepted", "requester": "c", "target": "a"}))
    assert t.turn_actions_this_deal == 2
    t.observe(ev("deal_started", {"deck_remaining_count": 35}))
    assert t.turn_actions_this_deal == 0 and t.deal_number == 2
    t.observe(ev("dealer_changed", {"player_id": "b"}))
    assert t.dealer_id == "b"


def test_policy_context_derived_properties():
    ctx = PolicyContext(
        own_rank="4",
        alive_count=3,
        deal_number=2,
        pot_chips=3,
        my_chips=10,
        is_dealer=False,
        unseen_counts={"道化": 2, "3": 1, "4": 1, "クク": 2},
        known_held={},
        turn_actions_this_deal=0,
    )
    assert ctx.is_child_time
    assert ctx.unseen_total == 6
    assert ctx.unseen_weaker_than_own() == 3  # 道化x2 + 3


# -- 途中失格者の表向きの札とリシャッフル ------------------------------------------
#
# The card sits on the TABLE (not in the pile) until the deal opens, so a
# reshuffle -- which empties the pile back into the deck -- must not forget it.


def test_a_face_up_disqualified_card_survives_a_reshuffle_as_seen():
    t = fresh_tracker()
    t.observe(ev("player_disqualified", {"player_id": "b", "cause": "received_joker", "card": "道化"}))
    assert t.unseen_counts("7")["道化"] == 1  # one of the two is accounted for

    # The pile goes back into the deck, but b's card is still face-up on the
    # table, so it stays counted as seen.
    t.observe(ev("deck_reshuffled", {"remaining_count": 30, "swept_seats": []}))
    assert t.unseen_counts("7")["道化"] == 1


def test_a_swept_disqualified_card_becomes_unseen_again():
    t = fresh_tracker()
    t.observe(ev("player_disqualified", {"player_id": "b", "cause": "received_joker", "card": "道化"}))
    assert t.unseen_counts("7")["道化"] == 1

    # reshuffle_includes_revealed: b's card was gathered into the rebuild, so
    # it is back in the deck and could be drawn again.
    t.observe(ev("deck_reshuffled", {"remaining_count": 31, "swept_seats": ["b"]}))
    assert t.unseen_counts("7")["道化"] == 2


def test_a_reshuffle_still_clears_what_the_pile_held():
    t = fresh_tracker()
    # A deck-draw refusal puts its card straight on the pile.
    t.observe(ev("exchange_result", {"result": "deck_draw_refused", "actor": "b", "drawn_rank": "猫"}))
    t.observe(ev("player_disqualified", {"player_id": "b", "cause": "cat_deck_draw", "card": "5"}))
    assert t.unseen_counts("7")["猫"] == 1
    assert t.unseen_counts("7")["5"] == 1

    t.observe(ev("deck_reshuffled", {"remaining_count": 30, "swept_seats": []}))
    assert t.unseen_counts("7")["猫"] == 2  # went back into the deck
    assert t.unseen_counts("7")["5"] == 1  # still face-up in front of b


def test_a_new_deal_forgets_the_previous_deals_face_up_cards():
    t = fresh_tracker()
    t.observe(ev("player_disqualified", {"player_id": "b", "cause": "received_joker", "card": "道化"}))
    t.observe(ev("deal_started", {"deck_remaining_count": 20}))
    t.observe(ev("deck_reshuffled", {"remaining_count": 30, "swept_seats": []}))
    # The card was collected at the previous open and is now tracked (if at
    # all) by discard_counts from deal_result -- never resurrected here.
    assert t.unseen_counts("7")["道化"] == 2
