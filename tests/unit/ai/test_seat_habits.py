"""相手の癖を学ぶ: 開示された札から、各席の交換の境目のずれを見積もる。

推論は「ノーチェンジした席は弱い札を持っていない」と読むが、これは相手が標準的に
打つときだけ正しい。慎重な相手(弱い札でも持つ)には外れる。開示のたびに誰が何を
持ったかは全員に見えるので、それを席ごとに覚えて読みを直す。
"""

from cucco.ai.bot import BotEvent
from cucco.ai.context import (
    FULL_DECK_COUNTS,
    CountingTracker,
    PolicyContext,
    seat_shift_posterior,
    standard_dump_count,
)
from cucco.ai.policies import CountingWin


def ev(type_, payload=None) -> BotEvent:
    return BotEvent(type=type_, payload=payload or {})


def _mean(samples) -> float:
    return sum(d * w for d, w in seat_shift_posterior(tuple(samples)).items())


def _tracker_after(*events) -> CountingTracker:
    t = CountingTracker()
    t.observe(ev("pot_started", {"participants": ["a", "b", "c", "d"], "dealer_id": "d", "chips_now": {}, "pot_chips": 4}))
    t.observe(ev("deal_started", {}))
    for e in events:
        t.observe(e)
    return t


# -- 記録 -------------------------------------------------------------------


def test_a_seat_that_kept_is_recorded_with_the_card_it_showed():
    t = _tracker_after(
        ev("no_change_declared", {"player_id": "a"}),
        ev("deal_opened", {"hands": {"a": "1", "b": "9", "c": "5", "d": "7"}}),
    )
    standard = standard_dump_count(4, child_time=True)
    # 動いたと分かっているのは a だけ。1 は普通の札の弱いほうから5番目(獅子・仮面・桶・0・1)
    assert t.seat_samples == {"a": [(4 - standard, True)]}


def test_the_card_thrown_to_the_deck_is_recorded_as_given_up():
    t = _tracker_after(
        ev("exchange_result", {"result": "deck_exchange_accepted", "actor": "d", "new_card": "10", "given_up_card": "6"}),
        ev("deal_opened", {"hands": {"a": "3", "b": "9", "c": "5", "d": "10"}}),
    )
    assert t.seat_samples["d"] == [(9 - standard_dump_count(4, child_time=True), False)]


def test_the_card_passed_to_the_next_seat_is_recorded_when_it_stayed_there():
    # a が b と交換し、b はそのあと動かなかった。b が開示した 2 は a が手放した札。
    t = _tracker_after(
        ev("exchange_result", {"result": "accepted", "requester": "a", "target": "b"}),
        ev("no_change_declared", {"player_id": "b"}),
        ev("deal_opened", {"hands": {"a": "8", "b": "2", "c": "5", "d": "7"}}),
    )
    standard = standard_dump_count(4, child_time=True)
    assert t.seat_samples["a"] == [(5 - standard, False)]
    assert t.seat_samples["b"] == [(5 - standard, True)]  # b はその 2 を持った


def test_special_cards_tell_nothing_about_habits():
    t = _tracker_after(
        ev("no_change_declared", {"player_id": "a"}),
        ev("deal_opened", {"hands": {"a": "馬", "b": "9"}}),
    )
    assert t.seat_samples == {}


# -- 見積もり ---------------------------------------------------------------


def test_keeping_cards_a_standard_player_would_dump_marks_a_cautious_seat():
    assert _mean([(-3, True)] * 3) < -2


def test_dumping_cards_a_standard_player_would_keep_marks_an_eager_seat():
    assert _mean([(2, False)] * 3) > 2


def test_ordinary_play_leaves_the_estimate_at_standard():
    ordinary = [(1, True), (4, True), (-2, False), (0, True), (-1, False)]
    assert abs(_mean(ordinary)) < 0.5


def test_one_odd_move_is_treated_as_a_whim():
    # 境目のすぐ下を手放し、すぐ上を持つ席が、1回だけ弱い札を持った。
    steady = [(-1, False)] * 4 + [(1, True)] * 4
    assert abs(_mean(steady + [(-4, True)])) < 0.5


# -- 使い道 -----------------------------------------------------------------

SEATS = ("a", "b", "c", "me", "d")


def ctx(**kw) -> PolicyContext:
    base = dict(
        own_rank="2", alive_count=5, deal_number=4, pot_chips=5, my_chips=25,
        is_dealer=False, unseen_counts=dict(FULL_DECK_COUNTS), known_held={},
        turn_actions_this_deal=0, my_id="me", turn_order=SEATS,
        acted_this_deal=frozenset({"a", "b", "c"}),
    )  # fmt: skip
    base.update(kw)
    return PolicyContext(**base)


def test_a_cautious_seat_that_kept_may_still_be_weaker():
    """標準なら「ノーチェンジ3席は 2 より弱くない」と読む。弱い札でも持つ席なら別。"""
    cautious = {p: ((-6, True),) * 6 for p in ("a", "b", "c")}
    standard = ctx().loss_model(0.4, adapt=True).lose_if_keep()
    learned = ctx(seat_samples=cautious).loss_model(0.4, adapt=True).lose_if_keep()
    assert learned < standard
    assert ctx(seat_samples=cautious).loss_model(0.4).lose_if_keep() == standard  # adapt を切れば同じ


def test_the_policy_keeps_more_against_cautious_seats():
    cautious = {p: ((-6, True),) * 6 for p in ("a", "b", "c")}
    win = CountingWin()

    def changes(**kw) -> set[str]:
        return {r for r in map(str, range(13)) if win.decide_change_ctx(ctx(own_rank=r, **kw))}

    assert changes(seat_samples=cautious) < changes()
