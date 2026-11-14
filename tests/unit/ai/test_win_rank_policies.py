"""勝率型(counting_win)と順位型(counting_rank)。2〜10人卓。

2つは交換とクク宣言が同じで、**継続だけが逆を向く**。ここで固定するのは
数値の細部ではなく、地図から読み取った向きである:

- 交換は他席の動きからの推論で決める(ノーチェンジした席は弱くない)
- 弱い札を持つ席が分かっているなら交換しない
- 勝率型は払えるかぎり払う。例外は首位を守るとき
- 順位型は7人以上の卓で継続を払わない。6人以下では勝率型と同じ
"""

from cucco.ai.context import FULL_DECK_COUNTS, ExchangeRecord, PolicyContext
from cucco.ai.policies import CountingRank, CountingWin

WIN, RANK = CountingWin(), CountingRank()
SEATS = ("a", "b", "c", "me", "d", "e")


def ctx(**kw) -> PolicyContext:
    base = dict(
        own_rank="5", alive_count=6, deal_number=4, pot_chips=6, my_chips=25,
        is_dealer=False, unseen_counts=dict(FULL_DECK_COUNTS), known_held={},
        turn_actions_this_deal=0,
        my_id="me", turn_order=SEATS,
        chips={p: 25 for p in SEATS},
    )  # fmt: skip
    base.update(kw)
    return PolicyContext(**base)


def _changes_at(policy, **kw) -> set[str]:
    return {r for r in map(str, range(13)) if policy.decide_change_ctx(ctx(own_rank=r, **kw))}


def test_it_changes_more_when_everyone_before_it_kept():
    """先の席が全員ノーチェンジ = 弱い札は自分のところにある見込みが上がる。"""
    nobody_moved_yet = _changes_at(WIN)  # 先の3席の札は手がかりなし、と置いた場合
    all_kept = _changes_at(WIN, acted_this_deal=frozenset({"a", "b", "c"}))
    assert nobody_moved_yet < all_kept


def test_it_keeps_when_it_knows_a_weaker_card_is_out_there():
    # a が 1 を持っていると分かっている。3 を持つ自分は最弱ではない。
    kept = frozenset({"a", "b", "c"})
    assert WIN.decide_change_ctx(ctx(own_rank="3", acted_this_deal=kept))
    assert not WIN.decide_change_ctx(ctx(own_rank="3", acted_this_deal=kept, known_held={"a": "1"}))


def test_both_types_exchange_identically():
    for kw in (dict(), dict(acted_this_deal=frozenset({"a"})), dict(deal_number=2), dict(turn_order=("me", "a", "b", "c", "d", "e"))):
        assert _changes_at(WIN, **kw) == _changes_at(RANK, **kw)


def cont(policy, my, others, required=2, pot=4) -> bool:
    chips = {"me": my, **dict(zip("abcdefghi", others))}
    return policy.decide_continue_ctx(ctx(deal_number=required, my_chips=my, chips=chips, pot_chips=pot, required_chips=required))


def test_win_type_pays_down_to_zero():
    assert cont(WIN, 2, (30, 30, 38))
    assert not cont(WIN, 1, (30, 30, 39))  # 払えない


def test_win_type_sits_out_to_protect_a_big_lead():
    assert not cont(WIN, 40, (2, 30, 28))  # 大差の首位で、誰かが飛びそう
    assert not cont(WIN, 60, (14, 13, 13))  # 他席の平均の2倍超
    assert cont(WIN, 60, (14, 13, 13), pot=14)  # 大きいポットは取りに行く
    assert cont(WIN, 30, (25, 23, 22))  # 競っているなら払う


def test_rank_type_pays_like_the_win_type_up_to_six_players():
    for others in ((47,), (30, 30, 28), (25, 25, 22), (10, 10, 10), (25,) * 5):
        for my in (3, 12, 28, 70):
            assert cont(RANK, my, others) == cont(WIN, my, others)


def test_rank_type_never_pays_at_seven_or_more():
    assert cont(WIN, 40, (25,) * 6)
    assert not cont(RANK, 40, (25,) * 6)
    assert not cont(RANK, 40, (25,) * 9, pot=30)


# -- クク宣言: 誰が最弱かが分かっているときだけ凍結する ------------------------


def cucco(**kw) -> PolicyContext:
    return ctx(own_rank="クク", **kw)


def test_it_declares_while_a_seat_before_it_can_still_take_the_cucco():
    assert WIN._declare_now(cucco(acted_this_deal=frozenset({"a", "b"})))  # c がまだ動いていない


def test_it_waits_in_the_safe_window_when_nobody_is_known_to_be_weakest():
    assert not WIN._declare_now(cucco(acted_this_deal=frozenset({"a", "b", "c"})))


def test_it_does_not_fire_just_because_a_rich_seat_was_taken_from():
    """counting_sniper の条件。相手のチップは測定では結果に関係がなかった。"""
    c = cucco(
        acted_this_deal=frozenset({"a", "b", "c", "me", "d"}),
        exchanges_this_deal=(ExchangeRecord(requester="d", target="e", result="accepted"),),
        chips={**{p: 25 for p in SEATS}, "e": 60},
    )
    assert not WIN._declare_now(c)


def test_it_freezes_when_a_seat_is_known_to_hold_the_weakest_card():
    # 道化より弱い札は無い。a がそれを持っていると分かっているなら、いま凍結すれば a が負ける。
    c = cucco(acted_this_deal=frozenset({"a", "b", "c", "me"}), known_held={"a": "道化"})
    assert WIN._declare_now(c)
    assert RANK._declare_now(c)
