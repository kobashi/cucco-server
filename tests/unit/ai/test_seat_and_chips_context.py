"""席・チップ・交換履歴が方策に届くこと(狙い撃ちの方策に必要な材料)。

どれも公開イベントに載っている情報だが、`PolicyContext` に出していなかった
ため、「チップ首位が交換の対象になって弱くなった瞬間にクク宣言」のような席を
名指しする方策が書けなかった。ここで検証するのは:

- `chips` が `chips_now` を載せる全イベント(pot_started / chips_paid /
  pot_won / deal_result)から最新に保たれること
- `turn_order` が domain と同じ「親が最後・循環しない」並びになること
- `exchange_target` が `is_dealer` と違い、親が途中失格した場合も正しいこと
- `unacted_before_me()` が「自分を狙える席」だけを返すこと
- `exchanges_this_deal` / `was_taken_from()` が交換の対象を拾うこと
"""

import pytest

from cucco.ai.bot import BotEvent, MockAI
from cucco.ai.context import CountingTracker, ExchangeRecord, PolicyContext


def ev(type_, payload=None):
    return BotEvent(type=type_, payload=payload or {})


def ctx(**kw) -> PolicyContext:
    base = dict(
        own_rank="5", alive_count=4, deal_number=1, pot_chips=4, my_chips=20,
        is_dealer=False, unseen_counts={}, known_held={}, turn_actions_this_deal=0,
    )
    base.update(kw)
    return PolicyContext(**base)


def pot_started(participants, dealer, chips):
    return ev("pot_started", {"participants": list(participants), "dealer_id": dealer,
                              "chips_now": dict(chips), "pot_chips": len(participants)})


# -- トラッカー -------------------------------------------------------------

def test_chips_follow_every_event_that_carries_chips_now():
    t = CountingTracker()
    t.observe(pot_started(["A", "B", "C"], "C", {"A": 25, "B": 25, "C": 25}))
    assert t.chips == {"A": 25, "B": 25, "C": 25}
    # 子供の時間の支払いはポットの途中で効く。ここを拾わないと首位が古くなる。
    t.observe(ev("chips_paid", {"player_id": "A", "amount": 2, "chips_now": {"A": 23, "B": 25, "C": 25}}))
    assert t.chips["A"] == 23
    t.observe(ev("deal_result", {"chips_now": {"A": 23, "B": 24, "C": 25}, "pot_chips": 5}))
    assert t.chips["B"] == 24
    t.observe(ev("pot_won", {"winner": "C", "amount": 6, "chips_now": {"A": 23, "B": 24, "C": 31}}))
    assert t.chips["C"] == 31


def test_turn_order_puts_the_dealer_last_and_does_not_wrap():
    t = CountingTracker()
    t.observe(pot_started(["A", "B", "C", "D"], "B", {}))
    # 親 B の次の席から始まり、B が最後。domain の _rotate_order_dealer_last と同じ。
    assert t.turn_order() == ("C", "D", "A", "B")


def test_a_seat_that_leaves_the_pot_drops_out_of_the_order():
    t = CountingTracker()
    t.observe(pot_started(["A", "B", "C", "D"], "D", {}))
    t.observe(ev("player_left_pot", {"player_id": "B", "reason": "declined"}))
    assert t.turn_order() == ("A", "C", "D")


def test_acted_and_exchanges_are_recorded_and_cleared_each_deal():
    t = CountingTracker()
    t.observe(pot_started(["A", "B", "C"], "C", {}))
    t.observe(ev("deal_started", {"deck_remaining_count": 41}))
    t.observe(ev("exchange_result", {"result": "accepted", "requester": "A", "target": "B"}))
    t.observe(ev("no_change_declared", {"player_id": "B"}))
    assert t.acted_this_deal == {"A", "B"}
    assert t.exchanges_this_deal == [ExchangeRecord("A", "B", "accepted", None)]
    t.observe(ev("deal_started", {"deck_remaining_count": 38}))
    assert t.acted_this_deal == set() and t.exchanges_this_deal == []


def test_a_refusal_chain_records_each_hop_under_one_requester():
    t = CountingTracker()
    t.observe(pot_started(["A", "B", "C"], "C", {}))
    t.observe(ev("deal_started", {}))
    # 馬で拒否されて要求が次の席へ流れる。要求元は連鎖のあいだ変わらない。
    t.observe(ev("exchange_result", {"result": "refused", "requester": "A", "target": "B",
                                     "reason": "horse", "revealed_rank": "馬"}))
    t.observe(ev("exchange_result", {"result": "accepted", "requester": "A", "target": "C"}))
    assert [e.target for e in t.exchanges_this_deal] == ["B", "C"]
    assert [e.result for e in t.exchanges_this_deal] == ["refused", "accepted"]
    assert t.exchanges_this_deal[0].revealed_rank == "馬"
    assert t.acted_this_deal == {"A"}


def test_disqualified_seats_are_tracked_for_the_deal():
    t = CountingTracker()
    t.observe(pot_started(["A", "B", "C"], "C", {}))
    t.observe(ev("deal_started", {}))
    t.observe(ev("player_disqualified", {"player_id": "B", "cause": "human_refusal", "card": "人間"}))
    assert t.disqualified == {"B"}
    t.observe(ev("deal_started", {}))
    assert t.disqualified == set()


# -- PolicyContext の補助 ----------------------------------------------------

def test_chip_leader_is_none_while_the_top_is_tied():
    assert ctx(chips={"A": 30, "B": 20, "C": 25}).chip_leader == "A"
    assert ctx(chips={"A": 30, "B": 30, "C": 25}).chip_leader is None
    assert ctx(chips={}).chip_leader is None


def test_exchange_target_is_the_next_surviving_seat_and_none_means_the_deck():
    order = ("P1", "P2", "P3", "DEALER")
    assert ctx(my_id="P1", turn_order=order).exchange_target == "P2"
    # 失格した席は読み飛ばす(domain の next_target と同じ)。
    assert ctx(my_id="P1", turn_order=order, disqualified=frozenset({"P2"})).exchange_target == "P3"
    # 末尾から溢れたら山札。`turn_order` は循環しないので P1 には戻らない。
    assert ctx(my_id="DEALER", turn_order=order, is_dealer=True).exchange_target is None


def test_exchange_target_is_right_even_when_the_dealer_was_disqualified():
    # 親が途中失格すると山札交換役は末尾の生存席に移る。`is_dealer` は False の
    # ままなので、そこだけ見ていると対人交換だと誤認する。
    order = ("P1", "P2", "DEALER")
    c = ctx(my_id="P2", turn_order=order, disqualified=frozenset({"DEALER"}), is_dealer=False)
    assert c.is_dealer is False
    assert c.exchange_target is None  # = 山札


def test_unacted_before_me_is_exactly_the_set_that_can_take_my_cucco():
    order = ("P1", "P2", "P3", "DEALER")
    # 第1プレイヤーを狙える席は存在しない(循環しないので)。
    assert ctx(my_id="P1", turn_order=order).unacted_before_me() == ()
    # 中間の席は、自分より前で未行動の席に狙われる。
    assert ctx(my_id="P3", turn_order=order).unacted_before_me() == ("P1", "P2")
    assert ctx(my_id="P3", turn_order=order,
               acted_this_deal=frozenset({"P1"})).unacted_before_me() == ("P2",)
    # 失格した席は狙ってこない。
    assert ctx(my_id="P3", turn_order=order, acted_this_deal=frozenset({"P1"}),
               disqualified=frozenset({"P2"})).unacted_before_me() == ()


def test_was_taken_from_sees_only_completed_exchanges_against_that_seat():
    c = ctx(exchanges_this_deal=(
        ExchangeRecord("A", "B", "refused", "馬"),
        ExchangeRecord("A", "C", "accepted", None),
    ))
    assert c.was_taken_from("C") is True
    assert c.was_taken_from("B") is False   # 拒否された: 札は動いていない
    assert c.was_taken_from("A") is False   # 要求した側はこの判定の対象ではない
    assert c.last_exchange == ExchangeRecord("A", "C", "accepted", None)


# -- 頭脳が実際に詰めて渡すか -------------------------------------------------

class SpyPolicy:
    name = "spy"

    def __init__(self):
        self.seen: list[PolicyContext] = []

    def decide_change_ctx(self, c):
        self.seen.append(c)
        return False

    def declare_cucco_eagerly(self, c):
        return False


class ScriptedConn:
    def __init__(self, events):
        self.player_id = "me"
        self._events = list(events)
        self.sent: list[tuple[str, dict]] = []

    async def send(self, type_, payload=None):
        self.sent.append((type_, payload or {}))

    async def events(self):
        for event in self._events:
            yield event


@pytest.mark.asyncio
async def test_the_brain_fills_the_seat_fields_in_the_context():
    events = [
        pot_started(["me", "x", "y"], "y", {"me": 25, "x": 30, "y": 20}),
        ev("deal_started", {"your_hand": "5", "deck_remaining_count": 41}),
        ev("exchange_result", {"result": "accepted", "requester": "me", "target": "x"}),
        ev("turn_prompt", {"timeout_sec": 30.0}),
    ]
    policy = SpyPolicy()
    conn = ScriptedConn(events)
    with pytest.raises(RuntimeError):
        await MockAI(conn, policy).play()
    (c,) = policy.seen
    assert c.my_id == "me"
    assert c.chips == {"me": 25, "x": 30, "y": 20}
    assert c.chip_leader == "x"
    assert c.turn_order == ("me", "x", "y")   # 親 y が最後
    assert c.exchange_target == "x"
    assert c.was_taken_from("x") is True
    assert c.acted_this_deal == frozenset({"me"})
