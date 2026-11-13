"""Shared observation state for AI policies (docs/ai-advanced-policies.md 案A).

`CountingTracker` maintains the card-counting bookkeeping every enhanced
policy needs, fed by the public event stream the brain already receives --
policies stay pure decision functions over a `PolicyContext` snapshot.

Accounting model (avoids double counting):
- `discard_counts` mirrors the server's discard pile exactly: incremented
  only from `deal_result.discarded_cards` (the authoritative record of what
  left play), cleared on `pot_started` and `deck_reshuffled`.
- `revealed_this_deal` covers cards made public MID-deal that the discard
  mirror doesn't know yet (deck-draw refusals, immediate-disclosure
  disqualifications, the dealer's given-up card, opened hands). Cleared at
  `deal_result` (superseded by the authoritative increment) and at
  `deal_started`. A `deck_reshuffled` rebuilds it from
  `revealed_disqualified`: the pile went back into the deck, but cards
  face-up in front of a disqualified player did not.
- `known_held` maps alive players to publicly-known current cards (a
  refusal's `revealed_rank`, the dealer's publicly drawn card). Swapped on
  accepted exchanges; dropped when the holder is disqualified (the card is
  then covered by the layers above).

`unseen_counts()` = 2 per rank minus all three layers minus the caller's
own hand: the multiset a policy should reason over.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from cucco.domain.cards import RANK_ORDER, strength

FULL_DECK_COUNTS: dict[str, int] = {rank.value: 2 for rank in RANK_ORDER}


@dataclass(frozen=True)
class ExchangeRecord:
    """このディールで起きた交換1件の公開記録。

    カンビオ宣言そのものには専用のイベントがない(wire_events の
    「"cambio" / "cucco_declare" declarations carry no info of their own」)。
    交換の結果イベントに載る `requester` が、その手番に動いた席である。
    """

    requester: str
    target: str | None  # None = 山札と交換した
    result: str  # accepted / refused / deck_exchange_accepted / deck_draw_refused
    revealed_rank: str | None = None  # 拒否で公開された札(人間・馬・猫・家)


@dataclass
class PolicyContext:
    """Everything an enhanced policy may look at for one decision."""

    own_rank: str
    alive_count: int
    deal_number: int  # 1-based within the pot; 1-3 is 子供の時間
    pot_chips: int
    my_chips: int
    is_dealer: bool
    unseen_counts: dict[str, int]
    known_held: dict[str, str]  # alive player_id -> publicly known rank
    turn_actions_this_deal: int  # turn declarations observed so far this deal
    required_chips: int = 1  # continue_prompt only

    # -- 席・相手・チップ(docs/ai-policy-reference.md「手番の順と交換相手」)--
    # どれも公開情報。狙い撃ちのように席を名指しする方策のために出している。
    # 既定値があるので、これらを渡さない呼び出し側(古い方策のテストや
    # tools/oracle_check.py)はそのまま動く。
    my_id: str = ""  # 自分の player_id(turn_order の中で自分を見つけるため)
    chips: dict[str, int] = field(default_factory=dict)  # 全員の持ちチップ
    turn_order: tuple[str, ...] = ()  # このディールの手番順。**親が最後**、循環しない
    disqualified: frozenset[str] = frozenset()  # このディールで失格した席
    acted_this_deal: frozenset[str] = frozenset()  # 手番宣言を済ませた席
    exchanges_this_deal: tuple[ExchangeRecord, ...] = ()  # 起きた順

    # 今持っている札の**元の持ち主が自分**か(deal.py の provenance)。配られた
    # ままなら True、一度でも交換すると False。猫がらみの失格は「要求者の札の
    # 元の持ち主」に落ちるので、True のまま交換を仕掛けると自爆の危険がある。
    # 既定が False なのは、席情報と同じく渡さない呼び出し側を壊さないため。
    own_card_original: bool = False

    @property
    def is_child_time(self) -> bool:
        return self.deal_number <= 3

    @property
    def unseen_total(self) -> int:
        return sum(self.unseen_counts.values())

    def unseen_weaker_than_own(self) -> int:
        own = strength_of(self.own_rank)
        return sum(n for rank, n in self.unseen_counts.items() if strength_of(rank) < own)

    # -- 席とチップの補助 -----------------------------------------------------

    @property
    def chip_leader(self) -> str | None:
        """チップ単独首位の player_id。同点首位なら `None`(狙い先が決まらない)。

        自分が首位なら自分の id が返る。狙い撃ちでは自分を除外すること。
        """
        if not self.chips:
            return None
        top = max(self.chips.values())
        leaders = [pid for pid, n in self.chips.items() if n == top]
        return leaders[0] if len(leaders) == 1 else None

    @property
    def exchange_target(self) -> str | None:
        """自分がカンビオしたときの交換相手。`None` なら**山札**と交換になる。

        手番順で自分より後ろにある、最初の失格していない席。末尾から溢れたら
        山札(`turn_order` は循環しない)。`is_dealer` と違い、親が途中失格して
        山札交換役を引き継いだ場合もこちらは正しい。

        席情報がない文脈(`turn_order` が空)でも `None` を返すので、山札か
        どうかを判定したいときは `turn_order` が空でないことを確かめること。
        """
        if self.my_id not in self.turn_order:
            return None
        idx = self.turn_order.index(self.my_id)
        for pid in self.turn_order[idx + 1 :]:
            if pid not in self.disqualified:
                return pid
        return None

    def unacted_before_me(self) -> tuple[str, ...]:
        """自分より前の手番で、まだ動いていない生存席。

        **自分を狙えるのはこの席だけ。**空ならクク を温存しても誰にも奪われない
        (交換の要求が自分に届かないまま開示を迎える)。空でないあいだ温存すると、
        直前の席のカンビオでクク はそのまま渡る(クク は拒否できない)。
        """
        if self.my_id not in self.turn_order:
            return ()
        idx = self.turn_order.index(self.my_id)
        return tuple(
            pid
            for pid in self.turn_order[:idx]
            if pid not in self.disqualified and pid not in self.acted_this_deal
        )

    @property
    def last_exchange(self) -> ExchangeRecord | None:
        """直近の交換の記録。`declare_cucco_eagerly` が呼ばれた契機を見るのに使う。"""
        return self.exchanges_this_deal[-1] if self.exchanges_this_deal else None

    def was_taken_from(self, player_id: str) -> bool:
        """その席が交換の**対象**になり、交換が成立したか。

        要求した側は自分の札を弱いと判断して動いているので、対象にされた席が
        受け取った札は弱い可能性が高い(docs/ai-advanced-policies.md 観点4)。
        札の値そのものは当事者しか見られないので、これは推定の材料である。
        """
        return any(e.target == player_id and e.result == "accepted" for e in self.exchanges_this_deal)


def strength_of(rank: str) -> int:
    """Wire-string wrapper over the domain's strength order (道化 weakest --
    the elevated-joker exception only exists after a deck draw, which the
    holder knows about separately)."""
    return strength(next(r for r in RANK_ORDER if r.value == rank))


@dataclass
class CountingTracker:
    discard_counts: Counter = field(default_factory=Counter)
    revealed_this_deal: Counter = field(default_factory=Counter)
    # Face-up cards of players disqualified this deal, by seat. These sit on
    # the table rather than in the pile, so a reshuffle has to know which of
    # `revealed_this_deal` survives it (see the deck_reshuffled branch).
    revealed_disqualified: dict[str, str] = field(default_factory=dict)
    known_held: dict[str, str] = field(default_factory=dict)
    # 継続確認の支払い枚数。当人宛の `continue_prompt` には入らず、全員向けの
    # `continue_prompted` にだけ乗る(docs/protocol/design.md「イベント」)。
    # ブラウザクライアントと同じようにブロードキャストから拾っておく。
    continue_required: dict[str, int] = field(default_factory=dict)
    deal_number: int = 0
    pot_chips: int = 0
    dealer_id: str | None = None
    turn_actions_this_deal: int = 0
    # 席とチップ。どれも公開イベントから拾える情報で、席を名指しする方策
    # (狙い撃ち・クク の温存)が必要とする。
    chips: dict[str, int] = field(default_factory=dict)
    pot_participants: tuple[str, ...] = ()
    disqualified: set[str] = field(default_factory=set)
    acted_this_deal: set[str] = field(default_factory=set)
    exchanges_this_deal: list[ExchangeRecord] = field(default_factory=list)

    def observe(self, event) -> None:
        p = event.payload
        t = event.type
        if t == "pot_started":
            self.discard_counts.clear()
            self.continue_required.clear()
            self.pot_chips = p.get("pot_chips", 0)
            self.dealer_id = p.get("dealer_id")
            self.pot_participants = tuple(p.get("participants") or ())
            self._observe_chips(p)
            self.deal_number = 0
            self._reset_deal()
        elif t == "deal_started":
            self.deal_number += 1
            self._reset_deal()
        elif t == "deck_reshuffled":
            # The discard pile went back into the deck, so everything it held
            # is unseen again. What does NOT go back is a disqualified
            # player's face-up card: it sits on the table until the deal opens
            # (both disclosure settings), so it stays counted as seen --
            # unless this reshuffle swept it in (`swept_seats`, the
            # reshuffle_includes_revealed rule), in which case it is unseen
            # again like the rest.
            for pid in p.get("swept_seats", ()):
                self.revealed_disqualified.pop(pid, None)
            self.discard_counts.clear()
            self.revealed_this_deal = Counter(self.revealed_disqualified.values())
        elif t == "dealer_changed":
            self.dealer_id = p.get("player_id")
        elif t == "chips_paid" or t == "pot_won" or t == "pot_result":
            self._observe_chips(p)
        elif t == "player_left_pot":
            # 次のディールの席順から外れる(ポットから降りた/大人の時間で脱落)。
            pid = p.get("player_id")
            self.pot_participants = tuple(x for x in self.pot_participants if x != pid)
        elif t == "exchange_result":
            self._observe_exchange(p)
        elif t == "no_change_declared" or t == "turn_timeout_consumed":
            self.turn_actions_this_deal += 1
            if p.get("player_id"):
                self.acted_this_deal.add(p["player_id"])
        elif t == "continue_prompted":
            pid = p.get("player_id")
            if pid is not None:
                self.continue_required[pid] = p.get("required_chips", 1)
        elif t == "player_disqualified":
            if p.get("card"):  # immediate disclosure only; deferred sends null
                self.revealed_this_deal[p["card"]] += 1
                # Remembered per seat so a reshuffle can tell which revealed
                # cards are still on the table and which it just swept away.
                self.revealed_disqualified[p["player_id"]] = p["card"]
            self.known_held.pop(p.get("player_id"), None)
            if p.get("player_id"):
                self.disqualified.add(p["player_id"])
        elif t == "deal_opened":
            for rank in (p.get("hands") or {}).values():
                if rank:
                    self.revealed_this_deal[rank] += 1
        elif t == "deal_result":
            # Authoritative: replace the provisional mid-deal layer.
            for entry in p.get("discarded_cards", ()):
                self.discard_counts[entry["card"]] += 1
            self.revealed_this_deal.clear()
            self.revealed_disqualified.clear()
            self.pot_chips = p.get("pot_chips", self.pot_chips)
            self._observe_chips(p)
            if p.get("next_dealer"):
                self.dealer_id = p["next_dealer"]

    def _reset_deal(self) -> None:
        self.revealed_this_deal.clear()
        self.revealed_disqualified.clear()
        self.known_held.clear()
        self.turn_actions_this_deal = 0
        self.disqualified.clear()
        self.acted_this_deal.clear()
        self.exchanges_this_deal.clear()

    def _observe_exchange(self, p: dict) -> None:
        result = p.get("result")
        # 要求した席(山札交換は actor)。カンビオ宣言自体はイベントを持たない
        # ので、動いた席が分かるのはここだけ。馬・家の拒否連鎖では同じ要求に
        # 対して複数回届くが、requester は連鎖のあいだ変わらない。
        actor = p.get("requester") or p.get("actor")
        if actor:
            self.acted_this_deal.add(actor)
            self.exchanges_this_deal.append(
                ExchangeRecord(
                    requester=actor,
                    target=p.get("target"),
                    result=str(result),
                    revealed_rank=p.get("revealed_rank"),
                )
            )
        if result == "accepted":
            self.turn_actions_this_deal += 1
            # Cards swapped: whatever we publicly knew about either hand
            # travels with the card.
            req, tgt = p.get("requester"), p.get("target")
            req_known, tgt_known = self.known_held.pop(req, None), self.known_held.pop(tgt, None)
            if req_known is not None:
                self.known_held[tgt] = req_known
            if tgt_known is not None:
                self.known_held[req] = tgt_known
        elif result == "refused":
            self.turn_actions_this_deal += 1
            if p.get("revealed_rank"):
                self.known_held[p["target"]] = p["revealed_rank"]
        elif result == "deck_exchange_accepted":
            self.turn_actions_this_deal += 1
            # Both cards are public: the draw is visible, the give-up is
            # face-up on the discard pile (mirrored authoritatively later).
            if p.get("new_card"):
                self.known_held[p["actor"]] = p["new_card"]
            if p.get("given_up_card"):
                self.revealed_this_deal[p["given_up_card"]] += 1
        elif result == "deck_draw_refused":
            self.turn_actions_this_deal += 1
            if p.get("drawn_rank"):
                self.revealed_this_deal[p["drawn_rank"]] += 1

    def _observe_chips(self, p: dict) -> None:
        """`chips_now` を取り込む。pot_started / chips_paid / pot_won /
        pot_result / deal_result に載る公開情報(wire_events)。"""
        now = p.get("chips_now")
        if isinstance(now, dict):
            self.chips.update(now)

    def turn_order(self) -> tuple[str, ...]:
        """このディールの手番順。**親が最後**で循環しない。

        domain 側の `_rotate_order_dealer_last` と同じ並べ替え。失格した席も
        並びには残る(交換相手を探すときに読み飛ばす — PolicyContext
        .exchange_target)。
        """
        seats = list(self.pot_participants)
        if self.dealer_id is None or self.dealer_id not in seats:
            return tuple(seats)
        idx = seats.index(self.dealer_id)
        return tuple(seats[idx + 1 :] + seats[: idx + 1])

    def unseen_counts(self, own_rank: str | None, alive_ids: set[str] | None = None) -> dict[str, int]:
        counts = dict(FULL_DECK_COUNTS)
        for layer in (self.discard_counts, self.revealed_this_deal):
            for rank, n in layer.items():
                counts[rank] = max(0, counts.get(rank, 0) - n)
        for pid, rank in self.known_held.items():
            if alive_ids is None or pid in alive_ids:
                counts[rank] = max(0, counts.get(rank, 0) - 1)
        if own_rank is not None and counts.get(own_rank, 0) > 0:
            counts[own_rank] -= 1
        return counts

    def known_held_alive(self, alive_ids: set[str], exclude: str | None = None) -> dict[str, str]:
        return {pid: rank for pid, rank in self.known_held.items() if pid in alive_ids and pid != exclude}
