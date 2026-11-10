"""継続確認の支払い枚数は `continue_prompted` ブロードキャストから拾う。

子供の時間の継続支払いはディール番号と同じ枚数(1〜3枚、
docs/rules/final_rules.md「子供の時間」)。ところが当人宛の `continue_prompt`
には枚数が入らず、全員向けの `continue_prompted` にだけ乗る
(docs/protocol/design.md「イベント」)。かつて頭脳はプロンプトの payload だけを
見ていたので常に1枚と誤認し、3回に2回は支払いを1〜2枚安く見積もったまま
継続判断をしていた。ブラウザクライアントは以前からブロードキャストを拾って
いる(clients/web/src/state.js)。
"""

import pytest

from cucco.ai.bot import BotEvent, MockAI
from cucco.ai.context import CountingTracker
from cucco.ai.policies import BasePolicy, make_policy


def ev(type_, payload=None):
    return BotEvent(type=type_, payload=payload or {})


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


class RecordingPolicy(BasePolicy):
    """継続判断で見えた required_chips を記録するだけの方策。"""

    name = "recording"

    def __init__(self):
        self.seen: list[int] = []

    def decide_change_ctx(self, ctx) -> bool:
        return False

    def decide_continue_ctx(self, ctx) -> bool:
        self.seen.append(ctx.required_chips)
        return True


def test_tracker_records_the_required_amount_per_player():
    t = CountingTracker()
    t.observe(ev("pot_started", {"participants": ["me", "a"], "chips_now": {}, "pot_chips": 2, "dealer_id": "a"}))
    t.observe(ev("continue_prompted", {"player_id": "me", "required_chips": 2}))
    t.observe(ev("continue_prompted", {"player_id": "a", "required_chips": 3}))
    assert t.continue_required == {"me": 2, "a": 3}
    # 新しいポットでは持ち越さない。
    t.observe(ev("pot_started", {"participants": ["me", "a"], "chips_now": {}, "pot_chips": 2, "dealer_id": "a"}))
    assert t.continue_required == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("deal_number, required", [(1, 1), (2, 2), (3, 3)])
async def test_brain_uses_the_broadcast_amount_not_the_default(deal_number, required):
    events = [ev("pot_started", {"participants": ["me", "a", "b"], "chips_now": {}, "pot_chips": 3, "dealer_id": "a"})]
    for _ in range(deal_number):
        events.append(ev("deal_started", {"deck_remaining_count": 40}))
    events += [
        ev("continue_prompted", {"player_id": "me", "required_chips": required}),
        ev("continue_prompt", {"timeout_sec": 30.0}),   # 枚数は入っていない
    ]
    policy = RecordingPolicy()
    conn = ScriptedConn(events)
    with pytest.raises(RuntimeError):  # scripted stream ends before game_ended
        await MockAI(conn, policy).play()
    assert policy.seen == [required]
    assert ("continue_declare", {"continue": True}) in conn.sent


async def _declared(chips: int, required: int, policy_name: str) -> bool:
    events = [
        ev("pot_started", {"participants": ["me", "a", "b"], "chips_now": {"me": chips},
                           "pot_chips": 3, "dealer_id": "a"}),
    ]
    for _ in range(required):
        events.append(ev("deal_started", {"deck_remaining_count": 40}))
    events += [
        ev("continue_prompted", {"player_id": "me", "required_chips": required}),
        ev("continue_prompt", {"timeout_sec": 30.0}),
    ]
    conn = ScriptedConn(events)
    with pytest.raises(RuntimeError):
        await MockAI(conn, make_policy(policy_name)).play()
    answers = [pl["continue"] for t, pl in conn.sent if t == "continue_declare"]
    assert len(answers) == 1
    return answers[0]


@pytest.mark.asyncio
async def test_aggressive_pays_while_a_chip_survives_the_real_amount():
    agg = "counting_aggressive"
    assert await _declared(chips=4, required=3, policy_name=agg) is True   # 1枚残る
    assert await _declared(chips=2, required=1, policy_name=agg) is True   # 1枚残る
    # 手持ち3枚で3枚の支払い。常に1枚と誤認していた頃は「2枚残る」と判断して
    # 払い、既定の終了条件 chips_zero でゲームを終わらせていた。
    assert await _declared(chips=3, required=3, policy_name=agg) is False


@pytest.mark.asyncio
async def test_conservative_sizes_its_buffer_against_the_real_amount():
    # 堅実型は「払っても required+2 枚残る」ことを求める(pot_chips は3枚)。
    con = "counting_conservative"
    assert await _declared(chips=8, required=3, policy_name=con) is True
    # 手持ち6枚・支払い3枚では余裕が足りない。1枚と誤認していた頃は「5枚残る」
    # と判断して払っていた、まさにこの局面が直っている。
    assert await _declared(chips=6, required=3, policy_name=con) is False
