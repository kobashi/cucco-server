"""同期の高速評価器。評価モードと同じ対局を、JSON も asyncio も通さずに回す。

なぜ要るか: 評価モード(`cucco.evaluation.runner`)は内蔵ボットにもワイヤ
プロトコルをそのまま喋らせる。人間向けの経路を迂回しないための設計だが、
方策の強さを測る用途では、時間のほとんどが封筒の組み立て・JSON の往復・
イベントループに消える(ゲームの進行そのものは一部でしかない)。勝率で
0.3pt の差を見るには 20万ゲーム級が必要で、その速度では測れない。

ここでは**進行と判断の部品は本物をそのまま使い**、運ぶ経路だけを抜く:

- 進行は `cucco.domain`(Game / Pot / Deal)。`TableRunner` と同じ順で呼ぶ
- イベントは `cucco.protocol.wire_events.translate` で本物と同じペイロードにする
- 判断は `cucco.ai.bot.MockAI._handle` をそのまま呼ぶ。頭脳は通信路に
  `send` しか要求しないので、同期の受け皿を渡せばイベントループは要らない

同じシードなら評価モードと**同じ着席順・同じ最終順位**になる(着席順の
シャッフルも `Game` に渡す乱数も `EvaluationRunner` と同じ作り方)。これは
`tools/fast_eval.py --verify` で本物と突き合わせて確かめられる。

評価モードと違う点(意図したもの):

- 人が絡む処理は無い。切断・退出・タイムアウト待ち・結果表示の間・
  `state_snapshot` は扱わない。答えを返さない方策は、本物のタイムアウトと
  同じ既定(手番はノーチェンジ、継続は辞退)に倒す
- 頭脳と方策は**ゲームごとに作り直す**。評価モードでは走行中ずっと同じ
  インスタンスだが、ゲームをまたいで状態を持つ方策でなければ結果は同じ。
  ゲームを独立にしておくと、並列化と対比較(同じシードで1席だけ差し替える)
  ができる
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from cucco.ai.bot import BotEvent, MockAI
from cucco.ai.policies import make_policy
from cucco.domain.cards import Rank
from cucco.domain.config import GameConfig
from cucco.domain.deal import DECLARABLE_RANKS, Deal
from cucco.domain.events import ChipsPaid, ContinuePrompted, DealerChanged, PlayerLeftPot, PotWipedOut, PotWon
from cucco.domain.game import Game
from cucco.domain.pot import Pot
from cucco.protocol.wire_events import translate

# プロンプトに載せる持ち時間。頭脳は読まないが、本物と同じ形にしておく。
_TIMEOUT_SEC = 10.0

Observer = Callable[[Game, str, dict], None]


def _run(coro):
    """待ちの入らないコルーチンを、イベントループ無しで最後まで進める。"""
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    coro.close()
    raise RuntimeError("頭脳が途中で待ちに入った。高速評価器は待ちの入る方策・頭脳を回せない")


class _Seat:
    """頭脳1つと、その通信路の代わり(`send` と `player_id` だけ)。"""

    __slots__ = ("player_id", "brain", "out", "pending_cucco")

    def __init__(self, player_id: str, policy_name: str) -> None:
        self.player_id = player_id
        self.out: list[tuple[str, dict]] = []
        # dispatch._route_to_inbox と同じ扱い: クク宣言はプロンプトの答えでは
        # なく、旗を立てておいて進行側が安全な切れ目で拾う。
        self.pending_cucco = False
        self.brain = MockAI(self, make_policy(policy_name), mode="evaluation")

    async def send(self, type_: str, payload: dict | None = None) -> None:
        if type_ == "cucco_declare":
            self.pending_cucco = True
        else:
            self.out.append((type_, payload or {}))

    def deliver(self, type_: str, payload: dict) -> None:
        _run(self.brain._handle(BotEvent(type_, payload)))

    def ask(self, type_: str, expected: tuple[str, ...], payload: dict | None = None) -> tuple[str, dict] | None:
        """プロンプトを渡して答えを取る。期待した種類の答えが無ければ `None`
        (本物ではタイムアウトに当たる)。前のプロンプトの残りは捨てる。"""
        self.out.clear()
        self.deliver(type_, {**(payload or {}), "timeout_sec": _TIMEOUT_SEC})
        for answer in self.out:
            if answer[0] in expected:
                return answer
        return None


@dataclass(frozen=True)
class GameResult:
    seed: int
    seats: tuple[str, ...]  # このゲームの着席順
    ranking: tuple[tuple[str, int], ...]  # (player_id, 最終チップ) を上位から
    deals: int
    pots: int

    def rank_of(self, player_id: str) -> int:
        """1始まりの順位。同点は評価モードと同じく並び順で割る。"""
        for i, (pid, _) in enumerate(self.ranking, start=1):
            if pid == player_id:
                return i
        raise KeyError(player_id)

    def chips_of(self, player_id: str) -> int:
        return dict(self.ranking)[player_id]


class _FastTable:
    """`TableRunner` の同期版。メソッドの並びと呼ぶ順は `cucco.server.runner`
    に合わせてある — 進行を変えるときは両方を直すこと。"""

    def __init__(self, seats: list[_Seat], game: Game, observer: Observer | None) -> None:
        self.seats = seats
        self.by_id = {s.player_id: s for s in seats}
        self.game = game
        self.observer = observer

    # -- 送る ---------------------------------------------------------------

    def _broadcast(self, type_: str, payload_for: Callable[[str], dict]) -> None:
        if self.observer is not None:
            self.observer(self.game, type_, payload_for(""))
        for seat in self.seats:
            seat.deliver(type_, payload_for(seat.player_id))

    def _send_event(self, event) -> None:
        wire = translate(event)
        if wire is not None:
            self._broadcast(wire.type, wire.for_recipient)

    def _send_events(self, events: Iterable) -> None:
        for event in events:
            self._send_event(event)

    # -- ゲーム ---------------------------------------------------------------

    def run(self) -> None:
        game = self.game
        self._send_events(game.start_first_pot())
        while not game.is_finished:
            self._run_pot(game)

    def _run_pot(self, game: Game) -> None:
        pot = game.current_pot
        assert pot is not None
        while True:
            if len(pot.active_participants()) < 2:
                self._send_events(game.force_end())
                return
            discard_before = len(pot.deck.discard_pile)
            reshuffle_count_before = pot.deck.reshuffle_count
            deal = self._run_deal(pot, game)
            opened = deal.open()[0] if not deal.is_opened else None
            if opened is not None:
                self._send_event(opened)
            losers = opened.losers if opened is not None else ()

            loser_events = pot.resolve_losers(deal, losers)
            self._send_events(loser_events)
            resolution_events = list(loser_events)
            for event in loser_events:
                if isinstance(event, ContinuePrompted):
                    resolution_events += self._handle_continue_prompt(pot, event.player_id)

            pot_chips_after_payments = pot.pot_chips
            outcome_events = pot.finalize_deal()
            self._send_events(outcome_events)
            reshuffled = pot.deck.reshuffle_count != reshuffle_count_before
            self._send_deal_result(
                pot, deal, losers, resolution_events, outcome_events, discard_before, reshuffled, pot_chips_after_payments
            )

            conclusion = next((e for e in outcome_events if isinstance(e, (PotWon, PotWipedOut))), None)
            if conclusion is not None:
                self._send_pot_result(pot.chips, conclusion)
                game_events = game.process_pot_outcome(conclusion)
                self._send_events(game_events)
                instant_win = next((e for e in game_events if isinstance(e, PotWon)), None)
                if instant_win is not None:
                    self._send_pot_result(game.chips, instant_win)
                return

    def _handle_continue_prompt(self, pot: Pot, player_id: str) -> list:
        answer = self.by_id[player_id].ask("continue_prompt", ("continue_declare",))
        continue_playing = bool(answer[1].get("continue")) if answer is not None else False
        events = pot.submit_continue_declare(player_id, continue_playing)
        self._send_events(events)
        return events

    def _send_deal_result(
        self,
        pot: Pot,
        deal: Deal,
        losers: tuple[str, ...],
        resolution_events: list,
        outcome_events: list,
        discard_before: int,
        reshuffled: bool,
        pot_chips: int,
    ) -> None:
        discard_now = pot.deck.discard_pile
        new_discards = discard_now if reshuffled else discard_now[discard_before:]
        payload = {
            "losers": sorted(deal.disqualified | set(losers)),
            "chips_paid": {e.player_id: e.amount for e in resolution_events if isinstance(e, ChipsPaid)},
            "left_pot": sorted(e.player_id for e in resolution_events if isinstance(e, PlayerLeftPot)),
            "chips_now": dict(pot.chips),
            "pot_chips": pot_chips,
            "next_dealer": next((e.player_id for e in outcome_events if isinstance(e, DealerChanged)), None),
            "discarded_cards": [
                {"card": entry.card.value, "original_holder": entry.original_holder, "discarded_via": entry.discarded_via}
                for entry in new_discards
            ],
        }
        self._broadcast("deal_result", lambda pid: payload)

    def _send_pot_result(self, chips: dict[str, int], conclusion: PotWon | PotWipedOut) -> None:
        if isinstance(conclusion, PotWon):
            payload = {"result": "won", "winner": conclusion.winner, "amount": conclusion.amount, "chips_now": dict(chips)}
        else:
            payload = {"result": "wiped_out", "amount": conclusion.amount, "chips_now": dict(chips)}
        self._broadcast("pot_result", lambda pid: payload)

    # -- ディール -------------------------------------------------------------

    def _run_deal(self, pot: Pot, game: Game) -> Deal:
        deal = pot.start_next_deal()
        for pid in deal.order:
            self.by_id[pid].pending_cucco = False

        self._send_events(deal.take_pending_events())
        self._broadcast(
            "deal_started",
            lambda pid: {
                "your_hand": deal.hands[pid].value if pid in deal.hands else None,
                "deck_remaining_count": pot.deck.remaining_count,
            },
        )

        # 「どうぞ」。その前に効くのは親自身のクク宣言だけ(配られた瞬間に宣言
        # する方策の親は、プロンプトを待たずにここで止める)。
        dealer_only = {deal.dealer_id}
        declarer = self._take_pending_cucco(deal, dealer_only)
        if declarer is None:
            self.by_id[deal.dealer_id].ask("dealer_ready", ("dealer_ready",))
            declarer = self._take_pending_cucco(deal, dealer_only)
        if declarer is not None:
            self._send_events(deal.submit_cucco_declare(declarer))

        while deal.legal_actor() is not None and deal.cucco_declared_by is None:
            # 切れ目ごとに、立っているクク宣言を先に適用する。頭脳はイベントを
            # 受けたその場で宣言するので、前の手番の交換でクク を受け取った席の
            # 宣言は、次の手番の席が動く前にここで拾われる。
            declarer = self._take_pending_cucco(deal)
            if declarer is not None:
                self._send_events(deal.submit_cucco_declare(declarer))
                break
            actor_id = deal.legal_actor()
            assert actor_id is not None
            self._send_events(self._run_turn(deal, actor_id))

        game.note_deal_played()
        return deal

    def _run_turn(self, deal: Deal, actor_id: str) -> list:
        answer = self.by_id[actor_id].ask("turn_prompt", ("cambio_declare", "no_change_declare"))
        declarer = self._take_pending_cucco(deal)
        if declarer is not None:
            return deal.submit_cucco_declare(declarer)
        if answer is None:
            return deal.submit_no_change(actor_id, via_timeout=True)
        if answer[0] == "cambio_declare":
            if self.game.config.effect_declaration == "declared":
                return self._run_declared_cambio(deal, actor_id)
            return deal.submit_cambio(actor_id)
        return deal.submit_no_change(actor_id)

    def _run_declared_cambio(self, deal: Deal, requester: str) -> list:
        events, target = deal.begin_cambio(requester)
        while target is not None:
            answer = self.by_id[target].ask("effect_window", ("effect_declare", "effect_pass"), {"requester": requester})
            declared = answer is not None and answer[0] == "effect_declare" and deal.hands.get(target) in DECLARABLE_RANKS
            if declared:
                step_events, target = deal.resolve_effect_declared(requester, target)
                events += step_events
            else:
                events += deal.resolve_exchange_accept(requester, target)
                target = None
        return events

    def _take_pending_cucco(self, deal: Deal, eligible: set[str] | None = None) -> str | None:
        order = [deal.dealer_id] + [pid for pid in deal.order if pid != deal.dealer_id]
        for pid in order:
            seat = self.by_id[pid]
            if not seat.pending_cucco:
                continue
            if pid in deal.disqualified or deal.hands.get(pid) is not Rank.CUCCO:
                seat.pending_cucco = False
                continue
            if eligible is not None and pid not in eligible:
                continue
            seat.pending_cucco = False
            return pid
        return None


def play_game(
    participants: list[str],
    policy_of: dict[str, str],
    config: GameConfig,
    seed: int,
    *,
    observer: Observer | None = None,
) -> GameResult:
    """1ゲームを最後まで回す。

    `participants` は卓に入った順(着席順ではない)。着席順は
    `EvaluationRunner` と同じく、このシードの乱数でシャッフルして決める。
    `observer(game, type, payload)` には全員向けのイベントが公開部分だけ届く
    (局面を記録して価値関数を学習するための口)。
    """
    rng = random.Random(seed)
    seating = list(participants)
    rng.shuffle(seating)
    game = Game(seating, config, rng)
    seats = [_Seat(pid, policy_of[pid]) for pid in participants]
    _FastTable(seats, game, observer).run()
    assert game.final_ranking is not None
    return GameResult(
        seed=seed,
        seats=tuple(seating),
        ranking=tuple(game.final_ranking),
        deals=game.round_count,
        pots=game.pot_number,
    )


def lineup(policies: list[str]) -> tuple[list[str], dict[str, str]]:
    """方策名の並びから (participants, policy_of) を作る。player_id は
    `P0`, `P1`, … で、**並びの位置で決まる** — 対比較では同じ位置の席が
    同じ id になるので、席を1つ差し替えても残りの席の乱数の当たり方は変わらない。"""
    participants = [f"P{i}" for i in range(len(policies))]
    return participants, dict(zip(participants, policies))


def play_games(
    policies: list[str], config: GameConfig, seeds: Iterable[int]
) -> list[GameResult]:
    participants, policy_of = lineup(policies)
    return [play_game(participants, policy_of, config, seed) for seed in seeds]
