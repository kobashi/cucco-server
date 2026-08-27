"""卓を去った参加者をポットから外す(Pot.withdraw)。

ゲームのルールではなく、席にもう誰もいないという現実の反映。以後のディールで
札が配られなくなることを押さえる。
"""

from cucco.domain.cards import Rank
from cucco.domain.events import PlayerLeftPot

from tests.unit.domain.pot_helpers import make_pot, play_deal_all_no_change


def _pot():
    # 4人・各25枚。1ディール目は子供の時間(敗者は払えば残る)。
    return make_pot(
        ["A", "B", "C", "D"],
        dealer_id="A",
        chips={"A": 25, "B": 25, "C": 25, "D": 25},
        deck_cards=[Rank.N9, Rank.N8, Rank.N7, Rank.N2],
    )


def test_a_withdrawn_seat_is_not_dealt_into_the_next_deal():
    pot = _pot()
    deal, losers = play_deal_all_no_change(pot)
    assert set(deal.hands) == {"A", "B", "C", "D"}

    assert pot.withdraw("B") == [PlayerLeftPot(player_id="B", reason="left")]
    # 進行中のディールはそのまま -- 配った札を取り上げると場の枚数が合わない。
    assert set(deal.hands) == {"A", "B", "C", "D"}

    pot.resolve_losers(deal, losers)
    for pid in list(pot.awaiting_continue_declare):
        pot.submit_continue_declare(pid, True)
    pot.finalize_deal()

    next_deal = pot.start_next_deal()
    assert "B" not in next_deal.hands
    assert set(next_deal.hands) == {"A", "C", "D"}
    assert "B" not in pot.active_participants()


def test_withdrawing_twice_or_a_stranger_does_nothing():
    pot = _pot()
    pot.start_next_deal()
    assert pot.withdraw("B") != []
    assert pot.withdraw("B") == []      # 2回目は何も起きない
    assert pot.withdraw("zzz") == []    # そもそも参加していない


def test_withdrawing_clears_a_pending_continue_decision():
    """返答待ちのまま去られると、待ったままで次のディールを始められなくなる。"""
    pot = _pot()
    deal, losers = play_deal_all_no_change(pot)
    pot.resolve_losers(deal, losers)
    waiting = set(pot.awaiting_continue_declare)
    assert waiting, "子供の時間の敗者は返答待ちになる"

    for pid in waiting:
        pot.withdraw(pid)
    assert pot.awaiting_continue_declare == frozenset()
    pot.finalize_deal()
    pot.start_next_deal()   # 待ちが残っていれば IllegalAction になる


def test_the_dealer_leaving_hands_the_deal_to_the_next_seat():
    """親が抜けたら、その場で次へ回す。親を決め直すのは本来ディールの終わりだが、
    そこで回したあとに親が抜けると、次のディールが「席にいない親」で始まる。"""
    from cucco.domain.events import DealerChanged

    pot = _pot()                      # 親は A、席順 A B C D
    pot.start_next_deal()
    events = pot.withdraw("A")

    assert events == [
        PlayerLeftPot(player_id="A", reason="left"),
        DealerChanged(player_id="B"),
    ]
    assert pot.dealer_id == "B"


def test_a_deal_never_starts_with_an_eliminated_dealer():
    """保険。何かの経路で親が脱落したまま残っても、配る前に回す。"""
    pot = _pot()
    deal, losers = play_deal_all_no_change(pot)
    pot.resolve_losers(deal, losers)
    for pid in list(pot.awaiting_continue_declare):
        pot.submit_continue_declare(pid, True)
    pot.finalize_deal()

    pot.eliminated.add(pot.dealer_id)   # 親が脱落した状態を直接作る
    stale = pot.dealer_id
    next_deal = pot.start_next_deal()

    assert pot.dealer_id != stale
    assert pot.dealer_id not in pot.eliminated
    assert pot.dealer_id in next_deal.hands
