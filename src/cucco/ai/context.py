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

    # -- 他席の札の推論 -------------------------------------------------------

    def seat_roles(self) -> dict[str, str]:
        """失格していない他席を、**このディールでの動きから**分類する。

        札の見積もりは動きで変わる(docs/ai-policy-reference.md §3):

        - `"known"`   札が分かっている(公開された、または自分との交換で渡した)
        - `"kept"`    ノーチェンジした。**弱い札なら交換しているはず**なので強め
        - `"stuck"`   交換を仕掛けたが拒否され、手放したかった札を持ったまま。弱め
        - `"moved"`   交換が成立した。持っているのは相手が持っていた札で、手がかりなし
        - `"unacted"` まだ動いていない。手がかりなし
        """
        last: dict[str, ExchangeRecord] = {}
        for e in self.exchanges_this_deal:
            last[e.requester] = e
        roles: dict[str, str] = {}
        for pid in self.turn_order:
            if pid == self.my_id or pid in self.disqualified:
                continue
            if pid in self.known_held:
                roles[pid] = "known"
            elif pid in last:
                roles[pid] = "moved" if last[pid].result in ("accepted", "deck_exchange_accepted") else "stuck"
            elif pid in self.acted_this_deal:
                roles[pid] = "kept"
            else:
                roles[pid] = "unacted"
        return roles

    def p_someone_weaker(self, keep_cutoff: float, leak: float = 0.0) -> float:
        """失格していない他席の**誰かが自分より弱い札を持っている**確率の見積もり。

        数え上げ(`unseen_counts`)に、各席の動きからの推論を重ねる。
        `unseen_weaker_than_own() / unseen_total` は「見えていない札は全席に均等」
        と置くが、実際には**ノーチェンジした席は弱い札を持っていない**。8人卓で
        先の7席が全員ノーチェンジのとき、均等の仮定は 0.95 と言うが実際は 0.66
        だった。ここではノーチェンジ席の札を「交換されなかったはずの札」に絞る。

        `keep_cutoff` は相手の交換のしきい値(それより弱い札は交換される、という
        想定。「自分より弱い札の割合」で測る)。`leak` は、想定どおりに打たない
        相手のために、しきい値より弱い札も残す割合(0 = 完全に絞る)。

        席どうしを独立とみなす近似で、同点は「弱い」に数えない。2〜10人卓で
        実際の手札と突き合わせて、ずれは ±0.03 以内(leak=0)。
        """
        return 1.0 - self.loss_model(keep_cutoff, leak).none_weaker(strength_of(self.own_rank))

    def loss_model(self, keep_cutoff: float, leak: float = 0.0) -> "LossModel":
        """このディールの「誰が最弱か」の見積もりを、問い合わせられる形で返す。"""
        return LossModel(self, keep_cutoff, leak)


class LossModel:
    """他席の札の見積もり(数え上げ + 動きからの推論)から、負ける確率を出す。

    - `none_weaker(strength)`: その強さの札を持っていて、**他の誰もそれより
      弱くない**確率。持ち続けたときに負ける確率は `none_weaker(自分の札)`
    - `lose_if_keep()` / `lose_if_change()`: 持ち続けた/交換した場合に、この
      ディールで最弱になる確率の見積もり

    交換の側は粗い近似である: 相手(または山札)から来る札は見えていない札から
    均等に引くとし、拒否・失格・その後の交換の連鎖は見ない。**この2つの差を
    そのまましきい値にすると弱い**(ディールの負けは勝率そのものではない。
    docs/ai-policy-reference.md §6)。判断材料の1つとして使うこと。
    """

    def __init__(self, ctx: PolicyContext, keep_cutoff: float, leak: float = 0.0) -> None:
        self.ctx = ctx
        counts = ctx.unseen_counts
        total = ctx.unseen_total
        self.total = total
        # 弱い順に (強さ, 枚数, 持ち続けられる割合)。札が「持ち続けられる」かは、
        # 弱いほうから積んだ割合がしきい値以上か。特殊札は性質で決まる。
        self.cards: list[tuple[int, int, float]] = []
        below = 0
        for rank in _RANKS_WEAK_TO_STRONG:
            n = counts.get(rank, 0)
            if n:
                if rank in _ALWAYS_KEPT:
                    kept = 1.0
                elif rank in _ALWAYS_DUMPED:
                    kept = leak
                else:
                    kept = 1.0 if total and below / total >= keep_cutoff else leak
                self.cards.append((_STRENGTH_BY_VALUE[rank], n, kept))
            below += n
        self.keep_w = sum(n * k for _, n, k in self.cards)
        self.dump_w = sum(n * (1.0 - k) for _, n, k in self.cards)
        roles = ctx.seat_roles()
        self.roles = roles
        self.known = [strength_of(ctx.known_held[p]) for p, r in roles.items() if r == "known"]
        self.n_kept = sum(1 for r in roles.values() if r == "kept")
        self.n_stuck = sum(1 for r in roles.values() if r == "stuck")
        self.n_uniform = sum(1 for r in roles.values() if r in ("moved", "unacted"))
        self.target_role = roles.get(ctx.exchange_target) if ctx.exchange_target else None

    def none_weaker(self, strength: int, without_target: bool = False) -> float:
        """その強さの札より弱い札を、他の誰も持っていない確率。

        `without_target` は交換相手の席を勘定から外す(交換後、その席が持つのは
        自分の元の札なので、別に扱う)。
        """
        if any(k < strength for k in self.known):
            return 0.0
        if self.total <= 0:
            return 1.0
        weaker = keep_weaker = dump_weaker = 0.0
        for st, n, kept in self.cards:
            if st >= strength:
                break
            weaker += n
            keep_weaker += n * kept
            dump_weaker += n * (1.0 - kept)
        uniform = weaker / self.total
        q_kept = keep_weaker / self.keep_w if self.keep_w > 0 else uniform
        q_stuck = dump_weaker / self.dump_w if self.dump_w > 0 else uniform
        n_kept, n_stuck, n_uniform = self.n_kept, self.n_stuck, self.n_uniform
        if without_target:
            if self.target_role == "kept":
                n_kept -= 1
            elif self.target_role == "stuck":
                n_stuck -= 1
            elif self.target_role in ("moved", "unacted"):
                n_uniform -= 1
        return (1.0 - q_kept) ** n_kept * (1.0 - q_stuck) ** n_stuck * (1.0 - uniform) ** n_uniform

    def weakest_probs(self) -> dict[str, float]:
        """**いま開示したら誰が最弱か**の見積もり(他席だけ。合計1)。

        クク宣言はディールを凍結し、その瞬間の最弱者が敗者になる。宣言する側
        (クク を持っている自分)は最弱にならないので、他席のあいだで配分する。
        同点は見ない近似。
        """
        if not self.roles:
            return {}
        if self.total <= 0 and not self.known:
            return {pid: 1.0 / len(self.roles) for pid in self.roles}
        strengths = [st for st, _, _ in self.cards]
        uniform = [n / self.total for _, n, _ in self.cards] if self.total else []
        kept = [n * k / self.keep_w for _, n, k in self.cards] if self.keep_w > 0 else uniform
        stuck = [n * (1.0 - k) / self.dump_w for _, n, k in self.cards] if self.dump_w > 0 else uniform
        grid = sorted(set(strengths) | {strength_of(self.ctx.known_held[p]) for p, r in self.roles.items() if r == "known"})
        index = {st: i for i, st in enumerate(grid)}

        def on_grid(dist: list[float]) -> list[float]:
            out = [0.0] * len(grid)
            for st, w in zip(strengths, dist):
                out[index[st]] = w
            return out

        by_role = {"kept": on_grid(kept), "stuck": on_grid(stuck), "moved": on_grid(uniform), "unacted": on_grid(uniform)}
        dists: dict[str, list[float]] = {}
        for pid, role in self.roles.items():
            if role == "known":
                point = [0.0] * len(grid)
                point[index[strength_of(self.ctx.known_held[pid])]] = 1.0
                dists[pid] = point
            else:
                dists[pid] = by_role[role]
        # above[pid][i] = その席の札が grid[i] より強い確率
        above = {}
        for pid, dist in dists.items():
            tail, acc = [0.0] * len(grid), 0.0
            for i in range(len(grid) - 1, -1, -1):
                tail[i] = acc
                acc += dist[i]
            above[pid] = tail
        probs = {}
        for pid, dist in dists.items():
            total = 0.0
            for i, w in enumerate(dist):
                if w:
                    rest = 1.0
                    for other, tail in above.items():
                        if other != pid:
                            rest *= tail[i]
                    total += w * rest
            probs[pid] = total
        norm = sum(probs.values())
        if norm <= 0:
            return {pid: 1.0 / len(probs) for pid in probs}
        return {pid: v / norm for pid, v in probs.items()}

    def lose_if_keep(self) -> float:
        return self.none_weaker(strength_of(self.ctx.own_rank))

    def lose_if_change(self) -> float:
        """交換したあと最弱になる確率。来る札は見えていない札から均等に引くと置く。

        席と交換するなら、相手は自分の元の札を持つ。来た札がそれ以上なら自分は
        最弱ではないので、負けるのは「元の札より弱い札が来て、しかも他の誰も
        それより弱くない」ときだけ。山札と交換するなら元の札は捨て札になるので、
        来た札がそのまま他の全席と比べられる。
        """
        if self.total <= 0:
            return self.lose_if_keep()
        own = strength_of(self.ctx.own_rank)
        to_deck = self.ctx.exchange_target is None
        if not to_deck and self.target_role == "known":
            theirs = strength_of(self.ctx.known_held[self.ctx.exchange_target])
            return 0.0 if theirs >= own else self.none_weaker(theirs, without_target=True)
        lose = 0.0
        for st, n, _ in self.cards:
            if not to_deck and st >= own:
                break
            lose += n / self.total * self.none_weaker(st, without_target=not to_deck)
        return lose


    # -- 判定時の見積もり(札の流れ) ------------------------------------------
    #
    # 上の lose_if_keep / lose_if_change は「いま全員が開いたら」の見積もりで、
    # 自分より後ろの席がこれから何をするかを見ない。実際には判定までに札が動く:
    #
    # - 弱い札を持った席は次の席と交換し、渡された席もまた次へ流す。流れは親で
    #   終わり、その札は**山札に抜ける**
    # - 流れの途中の席は、次の席が持っていた札を受け取る。それが弱い札でも
    #   もう動けない(**奪った札は手元に残る**)
    # - 道化を受け取った席、人間に仕掛けた席、猫に仕掛けた札の元の持ち主は
    #   **失格して比較から消える**(失格者は敗者だが、最弱の席を救わない)
    #
    # 後ろの席を手番順にたどって「判定時に、ある強さより弱い札が残っていない
    # 確率」を出す。札は見えていない札から独立に引くと置き、後ろの席は
    # 「しきい値より弱ければ交換、渡された札は必ず流す」と仮定する。

    def _flow_setup(self) -> None:
        if hasattr(self, "_p_house"):
            return
        counts, total = self.ctx.unseen_counts, self.total
        share = (lambda r: counts.get(r, 0) / total) if total > 0 else (lambda r: 0.0)
        self._p_house = share("馬") + share("家")
        self._p_human = share("人間")
        self._p_cat = share("猫")
        self._p_cucco = share("クク")
        self._p_joker = share("道化")
        special = {_STRENGTH_BY_VALUE[r] for r in ("馬", "家", "人間", "猫", "クク", "道化")}
        # 普通の札(獅子〜12): (強さ, 確率, 交換に出される割合)
        self._ordinary = [(st, n / total, 1.0 - kept) for st, n, kept in self.cards if st not in special] if total > 0 else []
        self._n_unacted = sum(1 for r in self.roles.values() if r == "unacted")
        self._n_moved = sum(1 for r in self.roles.values() if r == "moved")
        self._chains: dict[int, tuple[list[float], list[list[float]]]] = {}

    def _classes(self, t: int) -> tuple[float, float, float, float]:
        """普通の札を (流す・t より弱い, 流す・t 以上, 持つ・弱い, 持つ・以上) の確率に分ける。"""
        dw = do = gw = go = 0.0
        for st, pr, dump in self._ordinary:
            if st < t:
                dw += pr * dump
                gw += pr * (1.0 - dump)
            else:
                do += pr * dump
                go += pr * (1.0 - dump)
        return dw, do, gw, go

    def _acted_safe(self, t: int) -> float:
        """もう動いた席(と札が分かっている席)に、t より弱い札が**ない**確率。"""
        if any(k < t for k in self.known):
            return 0.0
        if self.total <= 0:
            return 1.0
        weaker = keep_weaker = dump_weaker = 0.0
        for st, n, kept in self.cards:
            if st >= t:
                break
            weaker += n
            keep_weaker += n * kept
            dump_weaker += n * (1.0 - kept)
        uniform = weaker / self.total
        q_kept = keep_weaker / self.keep_w if self.keep_w > 0 else uniform
        q_stuck = dump_weaker / self.dump_w if self.dump_w > 0 else uniform
        # 交換が成立した席が持つのは、受け取れる札(クク と普通の札)のどれか
        dw, do, gw, go = self._classes(t)
        takeable = self._p_cucco + dw + do + gw + go
        q_moved = (dw + gw) / takeable if takeable > 0 else uniform
        return (1.0 - q_kept) ** self.n_kept * (1.0 - q_stuck) ** self.n_stuck * (1.0 - q_moved) ** self._n_moved

    # 交換を仕掛ける席が持っている札の状態: (t より弱い/以上/道化) × (配られた札か)
    _W_OWN, _O_OWN, _J_OWN, _W_PUSHED, _O_PUSHED = range(5)

    def _chain(self, t: int) -> tuple[list[float], list[list[float]]]:
        """後ろの未行動の席 1..k について、判定時に t より弱い札が残らない確率。

        `free[j]`  = 席 j 以降が配られた札のまま手番を迎えるとき
        `push[j][m]` = 状態 m の札を持った席が、席 j に交換を仕掛けるとき
                       (仕掛けた席自身の結末も含む。j = k+1 は山札)
        """
        hit = self._chains.get(t)
        if hit is not None:
            return hit
        k = self._n_unacted
        ph, pn, pc, pk, pj = self._p_house, self._p_human, self._p_cat, self._p_cucco, self._p_joker
        dw, do, gw, go = self._classes(t)
        stuck_ok = (0.0, 1.0, 0.0, 0.0, 1.0)  # その札を持ったまま残ったとき、t 以上か
        own = (True, True, True, False, False)
        pushed_as = (self._W_PUSHED, self._O_PUSHED, None, self._W_PUSHED, self._O_PUSHED)
        free = [1.0] * (k + 2)
        push = [[0.0] * 5 for _ in range(k + 2)]
        rest = 1.0 - ph
        for m in range(5):
            # 山札: 馬・家は引き直し、クク は拒否、人間は自分が失格、猫は札の元の
            # 持ち主が失格、道化は最強になる
            cat = 1.0 if own[m] else stuck_ok[m]
            push[k + 1][m] = (pk * stuck_ok[m] + pn + pc * cat + pj + do + go) / rest if rest > 0 else stuck_ok[m]
        for j in range(k, 0, -1):
            after = free[j + 1]
            for m in range(5):
                # 席 j が札を受け取ったあと: 道化なら失格、それ以外は次へ流す
                received = after if pushed_as[m] is None else push[j + 1][pushed_as[m]]
                cat = 1.0 if own[m] else stuck_ok[m]
                push[j][m] = (
                    ph * push[j + 1][m]  # 馬・家: 飛ばして次の席へ
                    + pn * after  # 人間: 仕掛けた席が失格
                    + pc * cat * after  # 猫: 札の元の持ち主が失格
                    + (pk + pj + do + go) * received  # 成立(道化を受け取れば自分が失格して消える)
                )
            free[j] = (
                (ph + pn + pc + pk + go) * after
                + pj * push[j + 1][self._J_OWN]
                + dw * push[j + 1][self._W_OWN]
                + do * push[j + 1][self._O_OWN]
            )
        self._chains[t] = (free, push)
        return free, push

    def final_lose_if_keep(self) -> float:
        """持ち続けたとき、**判定時に**最弱(同点を含む)になる確率の見積もり。"""
        self._flow_setup()
        own = strength_of(self.ctx.own_rank)
        if self.total <= 0:
            return self._acted_safe(own)
        return self._acted_safe(own) * self._chain(own)[0][1]

    def final_lose_if_change(self) -> float:
        """交換を仕掛けたとき、判定時に負ける(失格を含む)確率の見積もり。

        仕掛けた結果ごとに分ける: 人間に当たる・道化を受け取る・配られた札で猫に
        当たると失格。クク を受け取れば負けない。普通の札を受け取ったら、その札を
        基準に「他の誰も弱くない」確率を出す。渡した札は相手がさらに流す。
        """
        self._flow_setup()
        own_rank = self.ctx.own_rank
        own = strength_of(own_rank)
        if self.total <= 0:
            return self._acted_safe(own)
        if self.ctx.exchange_target is not None and self.target_role == "known":
            return self.lose_if_change()
        k = self._n_unacted
        ph, pn, pc, pk, pj = self._p_house, self._p_human, self._p_cat, self._p_cucco, self._p_joker
        original = self.ctx.own_card_original
        mine_joker = own_rank == "道化"
        mine_dumped = True
        for st, _, kept in self.cards:
            if st == own:
                mine_dumped = kept < 0.5
        mine_dumped = mine_dumped or own_rank in _ALWAYS_DUMPED
        stuck_free = self._chain(own)[0]  # 拒否されて元の札のまま残るとき
        stuck_acted = self._acted_safe(own)
        # 受け取る札 y ごとの (確率, もう動いた席が安全, 後ろの鎖)
        taken = []
        for st, pr, _ in self._ordinary:
            safe = self._acted_safe(st)
            if safe > 0.0:
                taken.append((st, pr, safe, self._chain(st)))
        rest = 1.0 - ph
        if rest <= 0:
            return stuck_acted
        lose = (pk * stuck_acted + pn + pc * (1.0 if original else stuck_acted) + sum(pr * safe for _, pr, safe, _ in taken)) / rest
        for j in range(k, 0, -1):
            swap = 0.0
            for st, pr, safe, (free, push) in taken:
                if mine_joker:
                    others = free[j + 1]  # 道化を渡された席は失格して消える
                elif not mine_dumped:
                    others = free[j + 1] if own >= st else 0.0  # 相手はその札を持つ
                else:
                    others = push[j + 1][self._W_PUSHED if own < st else self._O_PUSHED]
                swap += pr * safe * others
            cat = 1.0 if original else stuck_acted * stuck_free[j + 1]
            lose = ph * lose + pn + pc * cat + pj + swap
        return lose


def strength_of(rank: str) -> int:
    """Wire-string wrapper over the domain's strength order (道化 weakest --
    the elevated-joker exception only exists after a deck draw, which the
    holder knows about separately)."""
    return _STRENGTH_BY_VALUE[rank]


# 方策は1判断のたびに何十回も強さを引く。毎回 RANK_ORDER を頭から探すと
# 対局時間の約3割がここに消えていたので、表にしてある(値は同じ)。
_STRENGTH_BY_VALUE: dict[str, int] = {r.value: strength(r) for r in RANK_ORDER}
_RANKS_WEAK_TO_STRONG: tuple[str, ...] = tuple(r.value for r in RANK_ORDER)
# 性質で決まる札: 拒否・失格させる札と クク は持ち続け、道化〜桶は必ず手放す。
# (policies.py の NEVER_CHANGE_RANKS / ALWAYS_CHANGE_RANKS と同じ集合。)
_ALWAYS_KEPT = frozenset({"クク", "人間", "馬", "猫", "家"})
_ALWAYS_DUMPED = frozenset({"道化", "獅子", "仮面", "桶"})


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
