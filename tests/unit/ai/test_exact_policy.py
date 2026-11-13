"""交換判断の厳密解(counting_exact)。

線形しきい値(cutoff_base - slope*人数 - danger_weight*危険度)は、
「このディールで自分が敗者になる確率」の近似でしかない。counting_exact は
その確率を cucco.ai.exact で直接計算して小さいほうを選ぶ。ここで検証するのは:

- 厳密解に対するリグレットが**構成上ゼロ**であること(採点ツールと同じ式)
- 猫の自爆(`own_card_original`)を数えること。配られた札をまだ持っている
  あいだは、交換を仕掛けると「自分の札の元の持ち主=自分」が失格しうる
- 交換相手が山札かどうかを `is_dealer` ではなく**席**から見ること
- 頭脳が `own_card_original` を正しく畳むこと(配られたまま=True、
  一度でも交換すれば False)
"""

import random
from collections import Counter

import pytest

from cucco.ai.bot import BotEvent, MockAI
from cucco.ai.context import FULL_DECK_COUNTS, PolicyContext
from cucco.ai.exact import p_lose_deck, p_lose_keep, p_lose_swap
from cucco.ai.policies import CountingExact


def ctx(**kw) -> PolicyContext:
    base = dict(
        own_rank="5", alive_count=4, deal_number=4, pot_chips=4, my_chips=20,
        is_dealer=False, unseen_counts=dict(FULL_DECK_COUNTS), known_held={},
        turn_actions_this_deal=0,
    )
    base.update(kw)
    return PolicyContext(**base)


def test_exact_has_zero_regret_against_the_oracle():
    """無作為な局面で、厳密解が小さいほうの手を必ず選ぶ。

    tools/oracle_check.py が出す「平均リグレット」が 0.0000 になることの、
    サーバーを起動しない版。
    """
    policy = CountingExact()
    rng = random.Random(20261113)
    checked = 0
    for _ in range(300):
        deck = [r for r, c in FULL_DECK_COUNTS.items() for _ in range(c)]
        rng.shuffle(deck)
        own = deck.pop()
        for _ in range(rng.choice((0, 6, 12, 18))):
            deck.pop()
        unseen = Counter(deck)
        alive = rng.choice((2, 3, 4, 5))
        k = alive - 1
        original = rng.choice((False, True))
        for is_dealer in (False, True):
            keep = p_lose_keep(own, unseen, k)
            solve = p_lose_deck if is_dealer else p_lose_swap
            change = solve(own, unseen, k, original)
            got = policy.decide_change_ctx(ctx(
                own_rank=own, alive_count=alive, is_dealer=is_dealer,
                unseen_counts=dict(unseen), own_card_original=original,
            ))
            if own in ("クク", "人間", "馬", "猫", "家"):
                assert got is False  # 強い特殊札は規則どおり持ち続ける
                continue
            assert got == (change < keep), (own, alive, is_dealer, original, keep, change)
            checked += 1
    assert checked > 400  # 空振りしていないこと


def test_the_cat_self_bomb_turns_a_change_into_a_keep():
    """配られた札をまだ持っているなら、交換は自爆の危険を伴う。

    4人・手札「2」・新品の山札では、交換後に敗者になる確率は 0.3465 で、
    持ち続ける 0.3642 より低い(=交換すべき)。ところが自分が自分の札の元の
    持ち主だと、猫を引かれた/猫に拒否された瞬間に自分が失格するぶんが乗って
    0.3695 になり、持ち続けるほうが良くなる。線形版はこの差を見ていない。
    """
    policy = CountingExact()
    fresh = dict(FULL_DECK_COUNTS)
    fresh["2"] -= 1
    assert policy.decide_change_ctx(ctx(own_rank="2", unseen_counts=fresh, own_card_original=False)) is True
    assert policy.decide_change_ctx(ctx(own_rank="2", unseen_counts=fresh, own_card_original=True)) is False


# 山札と交換する場合とプレイヤーと交換する場合で、判断が割れる局面。
# 手札「1」・5人・未見25枚: そのまま 0.1881 / プレイヤー交換 0.2705(悪化)/
# 山札交換 0.1759(改善)。当たる札が別物なので、相手が誰かで答えが変わる。
_SPLIT_UNSEEN = {
    "0": 1, "1": 1, "2": 1, "3": 1, "4": 2, "5": 1, "6": 1, "9": 1, "10": 2, "12": 1,
    "クク": 2, "仮面": 1, "家": 2, "桶": 2, "獅子": 2, "道化": 2, "馬": 2,
}


def test_the_deck_or_player_choice_comes_from_the_seat_not_the_is_dealer_flag():
    policy = CountingExact()
    common = dict(own_rank="1", alive_count=5, unseen_counts=dict(_SPLIT_UNSEEN), my_id="me")

    # 席順の最後=相手は山札。親の札が降りたなどで is_dealer が False でも、
    # 実際に当たるのは山札なので山札の式で判断しなければならない。
    assert policy.decide_change_ctx(ctx(
        **common, is_dealer=False, turn_order=("a", "b", "c", "d", "me"),
    )) is True

    # 自分の後ろに生存席がある=相手はプレイヤー。is_dealer が True でも
    # (親が交代した直後の取りこぼしなど)プレイヤー交換の式で判断する。
    assert policy.decide_change_ctx(ctx(
        **common, is_dealer=True, turn_order=("me", "a", "b", "c", "d"),
    )) is False

    # 席情報が無い呼び出し(古いテスト・採点ツール)は is_dealer に戻る。
    assert policy.decide_change_ctx(ctx(**common, is_dealer=True, turn_order=())) is True


def test_a_publicly_known_weaker_card_still_short_circuits():
    """誰かが自分より弱い札を公開して持っているなら、自分は単独最弱になりえない。
    確率計算に入る前にここで切る(counting と同じ)。"""
    policy = CountingExact()
    weak = dict(FULL_DECK_COUNTS)
    weak["桶"] -= 1
    assert policy.decide_change_ctx(ctx(own_rank="桶", unseen_counts=weak)) is True
    assert policy.decide_change_ctx(ctx(
        own_rank="桶", unseen_counts=weak, known_held={"a": "道化"},
    )) is False


# -- 頭脳が provenance を畳めているか ---------------------------------------

class SpyPolicy:
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


def ev(type_, payload=None):
    return BotEvent(type=type_, payload=payload or {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "swap_event, expected",
    [
        (None, True),                                               # 配られたまま
        (ev("exchange_result", {"result": "accepted", "requester": "x",
                                "target": "me", "your_new_card": "7"}), False),   # 取られた
        (ev("exchange_result", {"result": "deck_exchange_accepted", "actor": "me",
                                "new_card": "7"}), False),                        # 山札から来た
    ],
)
async def test_the_brain_tracks_whether_our_card_is_still_the_dealt_one(swap_event, expected):
    events = [
        ev("pot_started", {"participants": ["me", "x"], "dealer_id": "x",
                           "chips_now": {"me": 25, "x": 25}, "pot_chips": 2}),
        ev("deal_started", {"your_hand": "5", "deck_remaining_count": 42}),
    ]
    if swap_event is not None:
        events.append(swap_event)
    events.append(ev("turn_prompt", {"timeout_sec": 30.0}))

    policy = SpyPolicy()
    with pytest.raises(RuntimeError):
        await MockAI(ScriptedConn(events), policy).play()
    (c,) = policy.seen
    assert c.own_card_original is expected


@pytest.mark.asyncio
async def test_a_new_deal_puts_provenance_back_to_us():
    """ディールが変われば配り直しなので、前のディールの交換を引きずらない。"""
    events = [
        ev("pot_started", {"participants": ["me", "x"], "dealer_id": "x",
                           "chips_now": {"me": 25, "x": 25}, "pot_chips": 2}),
        ev("deal_started", {"your_hand": "5", "deck_remaining_count": 42}),
        ev("exchange_result", {"result": "accepted", "requester": "x",
                               "target": "me", "your_new_card": "7"}),
        ev("deal_started", {"your_hand": "9", "deck_remaining_count": 40}),
        ev("turn_prompt", {"timeout_sec": 30.0}),
    ]
    policy = SpyPolicy()
    with pytest.raises(RuntimeError):
        await MockAI(ScriptedConn(events), policy).play()
    (c,) = policy.seen
    assert c.own_card_original is True
