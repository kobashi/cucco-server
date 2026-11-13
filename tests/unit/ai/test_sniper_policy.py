"""クク宣言のタイミング(counting_sniper)。

クク宣言はディールを即終了させるので、**凍結した瞬間の最弱者が敗者になる**。
counting_sniper は「自分以上にチップを持つ席が交換の対象にされて成立した」
瞬間まで待ってから凍結する。ここで検証するのは:

- 取られる危険があるあいだ(`unacted_before_me()` が空でない)は必ず宣言する。
  クク は交換を拒否できないので、待つと直前の席に持っていかれる
- 自分の手番を過ぎたら待つ。そこから先は誰も自分を狙えない
- 裕福な席が弱くなった瞬間に凍結する。貧しい席が弱くなっても待つ
- **全員同じチップでも発火する**(ここが `chip_leader` 版との違い)
- 交換判断は counting_conservative のまま
"""

from cucco.ai.context import FULL_DECK_COUNTS, ExchangeRecord, PolicyContext
from cucco.ai.policies import CountingConservative, CountingSniper


def ctx(**kw) -> PolicyContext:
    base = dict(
        own_rank="クク", alive_count=4, deal_number=4, pot_chips=4, my_chips=25,
        is_dealer=False, unseen_counts=dict(FULL_DECK_COUNTS), known_held={},
        turn_actions_this_deal=0,
        my_id="me", turn_order=("a", "me", "b", "c"),
        chips={"me": 25, "a": 25, "b": 30, "c": 10},
    )
    base.update(kw)
    return PolicyContext(**base)


def took(requester, target, result="accepted"):
    return ExchangeRecord(requester=requester, target=target, result=result)


POLICY = CountingSniper()


def test_it_declares_while_a_seat_before_us_can_still_take_the_cucco():
    # a はまだ動いていない。a のカンビオの相手は自分なので、待てば クク は渡る。
    c = ctx(acted_this_deal=frozenset())
    assert POLICY.declare_cucco_eagerly(c) is True
    assert POLICY.decide_cucco_declare_ctx(c) is True


def test_it_waits_once_our_turn_has_passed():
    # a が動き終われば、交換の要求は前へしか進まないので誰も自分を狙えない。
    c = ctx(acted_this_deal=frozenset({"a"}))
    assert POLICY.declare_cucco_eagerly(c) is False
    assert POLICY.decide_cucco_declare_ctx(c) is False


def test_it_freezes_the_moment_a_richer_seat_has_been_taken_from():
    # b(30枚)が交換の対象にされて成立した = 弱い札を受け取った公算が大きい。
    c = ctx(acted_this_deal=frozenset({"a"}), exchanges_this_deal=(took("c", "b"),))
    assert POLICY.declare_cucco_eagerly(c) is True


def test_a_poorer_seat_going_weak_is_not_worth_freezing_for():
    # c(10枚)を倒してもチップ差は詰まらない。まだ待つ。
    c = ctx(acted_this_deal=frozenset({"a"}), exchanges_this_deal=(took("b", "c"),))
    assert POLICY.declare_cucco_eagerly(c) is False


def test_a_refused_exchange_is_not_a_hit():
    # 拒否されていれば相手の札は変わっていない。弱くなっていないので待つ。
    c = ctx(acted_this_deal=frozenset({"a"}),
            exchanges_this_deal=(took("c", "b", result="refused"),))
    assert POLICY.declare_cucco_eagerly(c) is False


def test_it_still_fires_when_everyone_has_the_same_chips():
    """ゲーム開始直後は全員同点で `chip_leader` は `None` になる。

    単独首位だけを狙うと、いちばん長い序盤がまるごと死ぬ。counting_sniper が
    見るのは「自分**以上**」なので、同点でも相手になる。
    """
    flat = {"me": 25, "a": 25, "b": 25, "c": 25}
    c = ctx(chips=flat, acted_this_deal=frozenset({"a"}))
    assert c.chip_leader is None
    assert POLICY.declare_cucco_eagerly(c) is False            # まだ誰も弱くない
    c = ctx(chips=flat, acted_this_deal=frozenset({"a"}),
            exchanges_this_deal=(took("c", "b"),))
    assert POLICY.declare_cucco_eagerly(c) is True


def test_a_disqualified_rich_seat_is_not_a_target():
    c = ctx(acted_this_deal=frozenset({"a"}), disqualified=frozenset({"b"}),
            exchanges_this_deal=(took("c", "b"),))
    assert POLICY.declare_cucco_eagerly(c) is False


def test_without_seat_information_it_behaves_like_the_baseline():
    """席が分からない呼び出し(古いテストなど)では `unacted_before_me()` が空に
    なる。黙って待ち続けるのではなく、裕福な席の弱りだけを見て判断する。"""
    c = ctx(turn_order=(), exchanges_this_deal=(took("c", "b"),))
    assert POLICY.declare_cucco_eagerly(c) is True


def test_the_change_decision_is_untouched():
    """強さの土台は counting_conservative のまま。変えたのは宣言のタイミングだけ。"""
    baseline = CountingConservative()
    for rank in ("0", "3", "5", "7", "9", "12", "桶", "仮面", "獅子", "家"):
        for alive in (2, 3, 4, 5):
            unseen = dict(FULL_DECK_COUNTS)
            unseen[rank] -= 1
            c = ctx(own_rank=rank, alive_count=alive, unseen_counts=unseen)
            assert POLICY.decide_change_ctx(c) == baseline.decide_change_ctx(c), (rank, alive)
