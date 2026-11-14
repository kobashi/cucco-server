"""他席の札の推論: 動きから読む(seat_roles / loss_model)、渡した札を覚える。

数え上げだけだと「見えていない札は全席に均等」と置くことになり、大人数卓で
安全を大きく見誤る(8人卓で先の7席が全員ノーチェンジのとき、均等の仮定は
「誰かが自分より弱い」を 0.95 と言うが、実際は 0.66 だった)。
"""

import pytest

from cucco.ai.bot import BotEvent, MockAI
from cucco.ai.context import FULL_DECK_COUNTS, ExchangeRecord, PolicyContext
from cucco.ai.policies import make_policy

SEATS = ("a", "b", "c", "me", "d")


def ctx(**kw) -> PolicyContext:
    base = dict(
        own_rank="3", alive_count=5, deal_number=4, pot_chips=5, my_chips=25,
        is_dealer=False, unseen_counts=dict(FULL_DECK_COUNTS), known_held={},
        turn_actions_this_deal=0, my_id="me", turn_order=SEATS,
    )  # fmt: skip
    base.update(kw)
    return PolicyContext(**base)


def test_seats_are_classified_by_what_they_did_this_deal():
    c = ctx(
        acted_this_deal=frozenset({"a", "b", "c"}),
        exchanges_this_deal=(
            ExchangeRecord(requester="b", target="c", result="refused", revealed_rank="人間"),
            ExchangeRecord(requester="c", target="me", result="accepted"),
        ),
        known_held={"c": "9"},
    )
    assert c.seat_roles() == {"a": "kept", "b": "stuck", "c": "known", "d": "unacted"}


def test_a_seat_whose_exchange_went_through_tells_us_nothing():
    c = ctx(acted_this_deal=frozenset({"a"}), exchanges_this_deal=(ExchangeRecord(requester="a", target="b", result="accepted"),))
    assert c.seat_roles()["a"] == "moved"


def test_with_no_moves_the_estimate_is_plain_counting():
    c = ctx()
    uniform = c.unseen_weaker_than_own() / c.unseen_total
    assert c.p_someone_weaker(keep_cutoff=0.4) == pytest.approx(1 - (1 - uniform) ** 4)


def test_seats_that_kept_are_less_likely_to_be_weaker():
    nobody = ctx().p_someone_weaker(keep_cutoff=0.4)
    kept = ctx(acted_this_deal=frozenset({"a", "b", "c"})).p_someone_weaker(keep_cutoff=0.4)
    assert kept < nobody
    # 3 は「交換されるはずの札」より弱い側。ノーチェンジ3席がそれより弱いことはない、
    # と読むので、残るのは未行動の1席ぶんだけ。
    uniform = ctx().unseen_weaker_than_own() / ctx().unseen_total
    assert kept == pytest.approx(uniform)


def test_a_seat_stuck_with_a_card_it_wanted_gone_is_likely_weaker():
    stuck = ctx(
        own_rank="8",
        acted_this_deal=frozenset({"a"}),
        exchanges_this_deal=(ExchangeRecord(requester="a", target="b", result="refused", revealed_rank="人間"),),
        known_held={"b": "人間"},
    )
    plain = ctx(own_rank="8", known_held={"b": "人間"})
    assert stuck.p_someone_weaker(keep_cutoff=0.4) > plain.p_someone_weaker(keep_cutoff=0.4)


def test_a_known_weaker_card_settles_it():
    c = ctx(known_held={"a": "1"})
    assert c.p_someone_weaker(keep_cutoff=0.4) == 1.0
    assert c.loss_model(0.4).lose_if_keep() == 0.0


def test_changing_with_a_seat_cannot_lose_when_the_card_coming_is_stronger():
    # 相手(d)の札が 9 と分かっている。交換すれば相手が自分の 3 を持つので、自分は最弱にならない。
    assert ctx(known_held={"d": "9"}).loss_model(0.4).lose_if_change() == 0.0


# -- 頭脳: 交換で渡した札を覚える ---------------------------------------------


class ScriptedConn:
    def __init__(self, events):
        self.player_id = "me"
        self._events = list(events)
        self.sent = []

    async def send(self, type_, payload=None):
        self.sent.append((type_, payload or {}))

    async def events(self):
        for event in self._events:
            yield event


def ev(type_, payload=None):
    return BotEvent(type=type_, payload=payload or {})


POT = ev("pot_started", {"participants": ["a", "me", "b"], "chips_now": {}, "pot_chips": 3, "dealer_id": "b"})


async def _brain(events) -> MockAI:
    ai = MockAI(ScriptedConn(events), make_policy("counting_win"))
    with pytest.raises(RuntimeError):  # 台本は game_ended の前に終わる
        await ai.play()
    return ai


@pytest.mark.asyncio
async def test_the_brain_remembers_the_card_it_was_made_to_hand_over():
    ai = await _brain([
        POT,
        ev("deal_started", {"your_hand": "7", "deck_remaining_count": 41}),
        ev("exchange_result", {"result": "accepted", "requester": "a", "target": "me", "your_new_card": "2"}),
    ])  # fmt: skip
    assert ai.tracker.known_held["a"] == "7"  # 公開情報ではないが、自分は知っている


@pytest.mark.asyncio
async def test_the_brain_remembers_the_card_it_gave_away_itself():
    ai = await _brain([
        POT,
        ev("deal_started", {"your_hand": "2", "deck_remaining_count": 41}),
        ev("exchange_result", {"result": "accepted", "requester": "me", "target": "b", "your_new_card": "9"}),
    ])  # fmt: skip
    assert ai.tracker.known_held["b"] == "2"


@pytest.mark.asyncio
async def test_it_does_not_change_a_card_that_came_from_a_seat_now_holding_a_weaker_one():
    ai = await _brain([
        POT,
        ev("deal_started", {"your_hand": "1", "deck_remaining_count": 41}),
        ev("exchange_result", {"result": "accepted", "requester": "a", "target": "me", "your_new_card": "3"}),
        ev("turn_prompt", {"player_id": "me", "timeout_sec": 30}),
    ])  # fmt: skip
    assert [t for t, _ in ai.conn.sent if t.endswith("_declare")] == ["no_change_declare"]


# -- 判定時の見積もり(札の流れ) ----------------------------------------------


def test_with_nobody_left_to_act_the_final_estimate_is_the_snapshot():
    """自分が最後なら、札はもう動かない。判定時の見積もりは「いま開いたら」と同じ。"""
    c = ctx(turn_order=("a", "b", "c", "d", "me"), acted_this_deal=frozenset({"a", "b", "c", "d"}))
    model = c.loss_model(0.4)
    assert model.final_lose_if_keep() == pytest.approx(model.lose_if_keep())


def test_weak_cards_behind_me_drain_away_before_the_showdown():
    """後ろの席の弱い札は、判定までに山札へ抜けるか、持ち主ごと失格で消える。

    「いま開いたら」の見積もりは後ろの4席に弱い札が残ると数えるので、持ち続けた
    ときの負けを低く見すぎる(4人卓の実測: 見積もり 0.11 に対し実際 0.24)。
    """
    c = ctx(own_rank="5", turn_order=("me", "a", "b", "c", "d"))
    model = c.loss_model(0.4)
    assert model.final_lose_if_keep() > 1.5 * model.lose_if_keep()


def test_a_known_weaker_card_still_settles_the_final_estimate():
    c = ctx(own_rank="5", turn_order=("a", "me", "b", "c", "d"), acted_this_deal=frozenset({"a"}), known_held={"a": "1"})
    assert c.loss_model(0.4).final_lose_if_keep() == 0.0


def test_changing_counts_the_ways_an_exchange_gets_you_disqualified():
    """人間に仕掛ける・道化を受け取る・配られた札で猫に当たると失格。負けに数える。"""
    c = ctx(own_rank="5", turn_order=("me", "a", "b", "c", "d"))
    floor = (2 + 2) / 43  # 次の席が人間か道化(見えていない43枚のうち2枚ずつ)
    dealt = c.loss_model(0.4).final_lose_if_change()
    assert floor < dealt < 1.0
    passed_on = ctx(own_rank="5", turn_order=("me", "a", "b", "c", "d"), own_card_original=True)
    assert passed_on.loss_model(0.4).final_lose_if_change() > dealt  # 配られた札だと猫でも失格


def test_final_estimates_are_probabilities_at_every_seat():
    for i in range(5):
        order = tuple(["a", "b", "c", "d"][:i] + ["me"] + ["a", "b", "c", "d"][i:])
        for rank in ("道化", "獅子", "0", "5", "12"):
            model = ctx(own_rank=rank, turn_order=order, acted_this_deal=frozenset(order[:i])).loss_model(0.4)
            assert 0.0 <= model.final_lose_if_keep() <= 1.0
            assert 0.0 <= model.final_lose_if_change() <= 1.0
