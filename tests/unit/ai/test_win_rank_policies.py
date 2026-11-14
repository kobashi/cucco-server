"""勝率型(counting_win)と順位型(counting_rank)。

2つは交換とクク宣言が同じで、**継続だけが逆を向く**。ここで固定するのは
数値の細部ではなく、地図から読み取った向きである:

- 先に動いた席が全員ノーチェンジなら、交換が増える
- 大人の時間の先頭手番、および先に交換があった後は、交換が減る
- 勝率型は払えるかぎり払う。例外は「大差の首位で、誰かが飛びそう」なとき
- 順位型は、貧しいときと大差の首位のときに払わない
"""

from cucco.ai.context import FULL_DECK_COUNTS, ExchangeRecord, PolicyContext
from cucco.ai.policies import CountingRank, CountingSniper, CountingWin

WIN, RANK, SNIPER = CountingWin(), CountingRank(), CountingSniper()


def ctx(**kw) -> PolicyContext:
    base = dict(
        own_rank="5", alive_count=4, deal_number=4, pot_chips=4, my_chips=25,
        is_dealer=False, unseen_counts=dict(FULL_DECK_COUNTS), known_held={},
        turn_actions_this_deal=0,
        my_id="me", turn_order=("a", "b", "me", "c"),
        chips={"me": 25, "a": 25, "b": 25, "c": 25},
    )  # fmt: skip
    base.update(kw)
    return PolicyContext(**base)


def _changes_at(policy, **kw) -> set[str]:
    return {r for r in map(str, range(13)) if policy.decide_change_ctx(ctx(own_rank=r, **kw))}


def test_it_changes_more_when_everyone_before_it_kept():
    kept = dict(acted_this_deal=frozenset({"a", "b"}))
    assert _changes_at(SNIPER, **kept) < _changes_at(WIN, **kept)


def test_it_changes_less_as_the_first_to_act_in_adult_time():
    first = dict(turn_order=("me", "a", "b", "c"))
    assert _changes_at(WIN, **first) < _changes_at(SNIPER, **first)


def test_it_changes_less_after_an_exchange_already_happened_in_adult_time():
    after = dict(
        acted_this_deal=frozenset({"a", "b"}),
        exchanges_this_deal=(ExchangeRecord(requester="a", target="b", result="accepted"),),
    )
    assert _changes_at(WIN, **after) < _changes_at(SNIPER, **after)


def test_both_types_exchange_identically():
    for kw in (dict(), dict(acted_this_deal=frozenset({"a"})), dict(deal_number=2), dict(turn_order=("me", "a", "b", "c"))):
        assert _changes_at(WIN, **kw) == _changes_at(RANK, **kw)


def cont(policy, my, others, required=2, pot=4) -> bool:
    chips = {"me": my, **dict(zip("abc", others))}
    return policy.decide_continue_ctx(ctx(deal_number=required, my_chips=my, chips=chips, pot_chips=pot, required_chips=required))


def test_win_type_pays_down_to_zero():
    assert cont(WIN, 2, (30, 30, 38))
    assert not cont(WIN, 1, (30, 30, 39))  # 払えない


def test_win_type_sits_out_as_a_runaway_leader_when_someone_is_about_to_bust():
    assert not cont(WIN, 50, (2, 20, 28))
    assert cont(WIN, 50, (5, 20, 25))  # まだ誰も飛ばない
    assert cont(WIN, 50, (2, 20, 28), pot=12)  # 大きいポットは取りに行く


def test_rank_type_keeps_its_chips_when_poor_or_far_ahead():
    assert not cont(RANK, 12, (30, 30, 28))  # 貧しい
    assert not cont(RANK, 50, (20, 15, 15))  # 大差の首位
    assert cont(RANK, 26, (25, 25, 24))  # 余裕があり、競っている
    assert cont(RANK, 12, (30, 30, 28), pot=12)  # ポットが大きければ払う
