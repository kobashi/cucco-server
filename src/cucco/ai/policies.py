"""AI decision policies (shared by the server-embedded bots and clients/mock_ai).

Three baseline policies, per the seminar's plan:

- `AlwaysChange`   -- declares cambio on every turn, no matter what.
- `AlwaysNoChange` -- declares no-change on every turn, no matter what.
- `MatrixPolicy`   -- decides by (残り人数 x 手札ランク) matrix: each alive-
  player count maps to the exact set of ranks worth exchanging away. The
  default matrix encodes docs/rules/play_summary_granpere.md's チェンジ判断表
  (specials クク/人間/馬/家/猫 never change; 道化/獅子/仮面/桶 always change;
  number cards change at-or-below a per-player-count threshold).

Every policy answers the four decision points from docs/ai-client-guide.md
§2 (turn / cucco window / continue). All baselines declare クク immediately
on any cucco window (seminar decision; timing it for advantage is the
advanced AIs' job). Ranks are the wire strings (the `Rank` enum's Japanese
values).
"""

from __future__ import annotations

from collections import Counter

from cucco.ai.context import FULL_DECK_COUNTS, strength_of
from cucco.ai.exact import p_lose_deck, p_lose_keep, p_lose_swap
from cucco.domain.cards import Rank

# Number ranks in weak->strong order, for threshold-based matrix rows.
NUMBER_RANKS: tuple[Rank, ...] = (
    Rank.N0, Rank.N1, Rank.N2, Rank.N3, Rank.N4, Rank.N5, Rank.N6,
    Rank.N7, Rank.N8, Rank.N9, Rank.N10, Rank.N11, Rank.N12,
)

# Weak plain cards that play_summary_granpere.md marks "チェンジ必須" at any
# table size (道化 is "絶対チェンジ": receiving it via exchange disqualifies
# the receiver, so holding it and doing nothing just loses the deal).
ALWAYS_CHANGE_RANKS = frozenset({Rank.JOKER, Rank.LION, Rank.MASK, Rank.BUCKET})

# Cards that refuse/punish exchanges -- "ノーチェンジ確定" in the doc.
NEVER_CHANGE_RANKS = frozenset({Rank.CUCCO, Rank.HUMAN, Rank.HORSE, Rank.CAT, Rank.HOUSE})


def matrix_row(number_threshold: int) -> frozenset[str]:
    """One matrix row: the set of rank strings to CHANGE when holding them --
    the always-change weak cards plus every number rank <= `number_threshold`."""
    numbers = frozenset(r.value for r in NUMBER_RANKS if int(r.value) <= number_threshold)
    return frozenset(r.value for r in ALWAYS_CHANGE_RANKS) | numbers


# alive-player-count -> ranks to change. Thresholds follow the doc's
# 「数字カードのチェンジ基準(人数目安)」: fewer surviving players means fewer
# chances someone else holds something even weaker, so raise the bar.
DEFAULT_MATRIX: dict[int, frozenset[str]] = {
    2: matrix_row(7),
    3: matrix_row(7),
    4: matrix_row(6),
    5: matrix_row(5),
    6: matrix_row(4),
    7: matrix_row(3),
}
DEFAULT_MATRIX_FALLBACK = matrix_row(3)  # 8+ players: doc's most conservative row


class BasePolicy:
    name = "base"

    def decide_change(self, own_rank: str, alive_count: int) -> bool:
        raise NotImplementedError

    def decide_cucco_declare(self, own_rank: str, alive_count: int) -> bool:
        # Seminar decision: every baseline mock declares クク immediately.
        # Timing the declaration for advantage (e.g. 温存 when nobody can
        # snatch it before open) is an advanced-AI concern
        # (docs/ai-advanced-policies.md 必須考慮事項6).
        return True

    def decide_continue(self, chips: int, required_chips: int) -> bool:
        return True  # child-time loser: pay and stay whenever prompted

    # -- context-aware entry points ------------------------------------------
    # The brain calls these with a `PolicyContext` (cucco.ai.context) built
    # from the shared counting tracker. Defaults delegate to the legacy
    # two-argument methods, so existing student policies keep working
    # unchanged; enhanced policies override these instead.

    def decide_change_ctx(self, ctx) -> bool:
        return self.decide_change(ctx.own_rank, ctx.alive_count)

    def decide_cucco_declare_ctx(self, ctx) -> bool:
        return self.decide_cucco_declare(ctx.own_rank, ctx.alive_count)

    def decide_continue_ctx(self, ctx) -> bool:
        return self.decide_continue(ctx.my_chips, ctx.required_chips)

    def declare_cucco_eagerly(self, ctx) -> bool:
        """Should the brain fire `cucco_declare` the moment it HOLDS クク,
        without waiting for its own prompt? Baselines keep the historical
        prompt-only simplification (False) so their behavior -- and the
        evaluation baselines built on them -- stay put; enhanced policies
        override this (see CountingPolicy for why waiting is a real leak)."""
        return False


class AlwaysChange(BasePolicy):
    name = "always_change"

    def decide_change(self, own_rank: str, alive_count: int) -> bool:
        return True


class AlwaysNoChange(BasePolicy):
    name = "always_no_change"

    def decide_change(self, own_rank: str, alive_count: int) -> bool:
        return False


class MatrixPolicy(BasePolicy):
    name = "matrix"

    def __init__(self, matrix: dict[int, frozenset[str]] | None = None) -> None:
        self.matrix = matrix if matrix is not None else DEFAULT_MATRIX

    def decide_change(self, own_rank: str, alive_count: int) -> bool:
        row = self.matrix.get(alive_count, DEFAULT_MATRIX_FALLBACK)
        return own_rank in row


class CountingPolicy(BasePolicy):
    """Card-counting probability policy (docs/ai-advanced-policies.md 案A +
    案D's chip-expectation layer): the matrix's fixed thresholds become an
    on-the-spot estimate of P(自分が最弱) over the unseen-card multiset the
    shared tracker maintains.

    Decision rules:
    - Specials keep the fixed rows (クク/人間/馬/猫/家 never change,
      道化/獅子/仮面/桶 always change) -- those aren't probability calls.
    - Certainty first: a publicly-known weaker card in an alive opponent's
      hand means we cannot be the unique weakest -> no change. Zero weaker
      cards left unseen (all in the discard pile) means we ARE the weakest
      -> change.
    - Otherwise compute p = share of unseen cards weaker than ours, and
      change when p < cutoff(n) = base - slope*n. The linear-in-n cutoff is
      the play_summary matrix's own implied rule (its per-player-count rows
      fit base 0.65 / slope 0.047 on a fresh deck almost exactly) -- but p
      here comes from the LIVE unseen multiset, so the decision sharpens as
      the discard pile and revealed cards accumulate. That is 案A's whole
      point: same judgment, real-time probabilities.
    - The cutoff drops with refusal danger (share of 人間/猫 among the
      unseen -- a change can get the requester disqualified) and during
      子供の時間 (losses are cheap then, so risky changes buy little).
    - decide_continue is where 積極/消極 diverge: aggressive pays whenever
      it can keep at least 1 chip; conservative also wants a buffer for the
      next loss and a pot actually worth the price.
    - 継続判断はこのまま据え置いている。tools/continue_oracle.py でポット
      終了時の期待チップを後退帰納法で解くと conservative の形は0.543チップ
      /判断の損で「払えるなら払う」(0.061)より悪いが、実際に差し替えて
      7,000ゲーム対戦させると勝率は -0.73pt(SE 1.04pt)で、改善は検出
      できなかった。厳密解の目的関数(期待チップ)がこのゲームの勝敗(順位と
      生存。既定の終了条件は chips_zero)と一致していないためで、目的関数を
      直すまでこの数字で方策を動かさない。強さの差は交換判断の側
      (cutoff_base / danger_weight)から来ている。
    """

    name = "counting"
    cutoff_base = 0.66
    cutoff_slope = 0.047
    danger_weight = 0.3
    child_time_extra = 0.05
    conservative_continue = False

    def decide_change_ctx(self, ctx) -> bool:
        own = ctx.own_rank
        if own in NEVER_CHANGE_RANKS or own not in FULL_DECK_COUNTS:
            return False
        if own in {r.value for r in ALWAYS_CHANGE_RANKS}:
            return True
        own_strength = strength_of(own)
        if any(strength_of(rank) < own_strength for rank in ctx.known_held.values()):
            return False  # someone else is certainly weaker
        total = ctx.unseen_total
        if total <= 0:
            return False
        weaker = ctx.unseen_weaker_than_own()
        if weaker == 0:
            return True  # every weaker card is out of play: we are the weakest
        p_weaker = weaker / total
        danger = (ctx.unseen_counts.get(Rank.HUMAN.value, 0) + ctx.unseen_counts.get(Rank.CAT.value, 0)) / total
        cutoff = self.cutoff_base - self.cutoff_slope * ctx.alive_count - self.danger_weight * danger
        if ctx.is_child_time:
            cutoff -= self.child_time_extra
        return p_weaker < cutoff

    def decide_cucco_declare_ctx(self, ctx) -> bool:
        # At a prompt an immediate declaration dominates: holding クク we
        # cannot lose the open, but waiting risks losing the card itself.
        return True

    def declare_cucco_eagerly(self, ctx) -> bool:
        # クク cannot refuse a player-to-player exchange, so the left
        # neighbor's cambio can simply TAKE it -- waiting for our own prompt
        # leaves the card stealable for a whole round (observed in live
        # play: a human lifted クク straight out of a bot's hand). The
        # protocol's fire-and-forget declaration exists precisely so a
        # holder never has to wait: declare the instant we hold it. (The
        # server defers a non-dealer's pre-どうぞ declaration on its own.)
        return True

    def decide_continue_ctx(self, ctx) -> bool:
        remaining = ctx.my_chips - ctx.required_chips
        if not self.conservative_continue:
            return remaining >= 1
        # Conservative: keep a buffer for one more loss, and only pay a
        # price the pot actually justifies.
        return remaining >= ctx.required_chips + 2 and ctx.pot_chips >= ctx.required_chips

    # Legacy-signature fallbacks (a student harness calling the old API gets
    # matrix behavior rather than an exception).
    def decide_change(self, own_rank: str, alive_count: int) -> bool:
        return MatrixPolicy().decide_change(own_rank, alive_count)


class CountingAggressive(CountingPolicy):
    name = "counting_aggressive"
    cutoff_base = 0.70
    danger_weight = 0.2
    conservative_continue = False


class CountingConservative(CountingPolicy):
    name = "counting_conservative"
    cutoff_base = 0.62
    danger_weight = 0.5
    conservative_continue = True


class CountingExact(CountingConservative):
    """交換判断だけを**厳密解**に差し替えた counting_conservative。

    > [!WARNING]
    > **対局に使う方策ではない。弱い。**7,000ゲームの実測で
    > `counting_conservative` に **勝率 -3.90pt(SE 0.73pt)**。近視眼的な
    > 厳密解に近づけるほど弱くなることを示すために置いてある(下記)。

    何をしているか: cutoff_base / danger_weight の線形しきい値は「このディール
    で自分が敗者になる確率」を人数で割った近似にすぎない。ここではその確率を
    `cucco.ai.exact` で直接計算し、持ち続ける場合と交換する場合を比べて小さい
    ほうを選ぶ。同じ式を tools/oracle_check.py が採点に使っているので、この
    方策のリグレットは**構成上ゼロ**になる(近視眼モデルの中では、という意味)。

    規則の扱いも線形版より正確にしてある:

    - **交換相手が山札かどうかを席から見る。**親かどうか(`is_dealer`)ではなく
      `exchange_target` を見るので、親が途中失格して山札交換役が回ってきた席も
      正しく山札の式を使う。山札とプレイヤーでは当たる札が別物になる。
    - **猫の自爆を数える。**猫を引く/猫に拒否されると失格するのは「要求者の札の
      元の持ち主」なので、配られた札をまだ持っているあいだ(`own_card_original`)
      交換は自爆の危険を伴う。線形版は猫を danger_weight でまとめて割り引いて
      いただけで、自分の provenance は見ていなかった。

    それでも弱い。しかも**正確にするほど弱くなった**: 猫の自爆を外すと
    -1.79pt(SE 0.96pt)まで戻り、逆に「札が割れている席を未知の引きに数えない」
    補正まで入れると -4.03pt(SE 1.22pt)になる。原因は近似の精度ではなく
    **目的関数**にある。「このディールで自分が敗者になる確率」を最小化しても、
    勝敗(順位と生存。既定の終了条件は chips_zero)は最大化されない。弱い札を
    交換で他家に押しつければ相手の敗北確率が上がるが、この目的関数は自分の
    敗北確率しか数えないので、その価値をまったく評価していない。

    継続判断で起きたのとまったく同じ失敗である(`CountingPolicy` の注記)。
    **厳密解が勝率を上げるのは目的関数が勝敗と一致しているときだけ**で、この
    方策はそれを確かめるための反例として残してある。tools/oracle_check.py の
    リグレット 0.0000 がどれだけ当てにならないかの目盛りにもなる。
    """

    name = "counting_exact"

    def decide_change_ctx(self, ctx) -> bool:
        own = ctx.own_rank
        if own in NEVER_CHANGE_RANKS or own not in FULL_DECK_COUNTS:
            return False
        own_strength = strength_of(own)
        if any(strength_of(rank) < own_strength for rank in ctx.known_held.values()):
            return False  # someone else is certainly weaker
        unseen = Counter({r: n for r, n in ctx.unseen_counts.items() if n > 0})
        if sum(unseen.values()) <= 0:
            return False
        k = max(0, ctx.alive_count - 1)
        keep = p_lose_keep(own, unseen, k)
        if ctx.turn_order and ctx.my_id in ctx.turn_order:
            to_deck = ctx.exchange_target is None
        else:
            to_deck = ctx.is_dealer  # 席情報が無い呼び出し(古いテスト・採点ツール)
        solve = p_lose_deck if to_deck else p_lose_swap
        return solve(own, unseen, k, ctx.own_card_original) < keep


class CountingSniper(CountingConservative):
    """counting_conservative + **クク宣言のタイミング**。交換判断は同じ。

    クク宣言はディールを即終了させる。**凍結した瞬間の最弱者が敗者になる**ので、
    宣言は「誰が負けるか」を選ぶ武器になる。そこで、ただ持った瞬間に撃つのを
    やめ、「自分以上にチップを持っている席が交換の対象にされて成立した」瞬間
    ——つまりその席が弱い札を受け取った可能性が高い瞬間——まで待って凍結する。

    待てる窓は席で決まる(docs/ai-policy-reference.md §3):

    - **自分を狙えるのは直前の1席だけ。**自分より前に未行動の席があるあいだは
      危険で、待つと クク をそのまま持っていかれる(クク は交換を拒否できない)
    - **自分の手番を過ぎれば誰も自分を狙えない。**交換の要求は手番順に前へ
      しか進まないので、ここが安全に様子を見られる窓
    - 待っても**負けはしない**。クク を持っているかぎり最弱にはならないので、
      親が山札から道化を引いて最強札に化けても、敗者になるのは別の席である

    > [!WARNING]
    > **強くなることは確かめられていない。**確認走行7,000ゲームで
    > `counting_conservative` に対し **+0.39pt(SE 0.40pt)**。有意差なし。
    > 採用しているのは「弱くはない」からであって、「強い」からではない。

    測った順に書くと:

    - 候補3つ(「取られるまで待つだけ」「裕福な席が弱ったら凍結」「単独首位・
      大人の時間に限る」)を1卓に並べた**選抜走行**7,000ゲームで、この形だけ
      +1.76pt(SE 0.60pt)と出た
    - ところが**同じ形を1つに固定して取り直す**と +0.39pt(SE 0.40pt)に落ちた。
      3つ引けば一番良いものは偶然でも上振れする。選抜走行の数字は採用根拠に
      ならない(docs/ai-policy-reference.md §6)
    - 発火はする。400ゲームの実測で、クク を持った瞬間の判断2,300回のうち
      「取られる前に撃つしかない」1,264回(55%)、「待つ」960回(42%)、
      **実際に狙って凍結した 75回(3.3%、5.3ゲームに1回)**。条件を緩める前の
      厳しい版(24ゲームに1回)の4.4倍まで増えたが、勝率には出なかった

    つまり**発火頻度は足りているのに効かない**。狙い先の選び方(「自分以上」で
    よいのか)か、「対象にされた席は弱い」という推定そのものを疑うべきで、
    待ち方をこれ以上いじっても伸びないと思われる。
    """

    name = "counting_sniper"

    def _rich_seats(self, ctx) -> list[str]:
        """自分以上にチップを持っている、失格していない他席。

        `chip_leader`(単独首位)ではなく**自分以上**を見る。ゲーム開始直後は
        全員同点で単独首位が決まらず、首位1人に絞ると発火がほとんど無くなる。
        ポットを降りた席もここには残るが、そういう席は交換の対象になりえない
        ので `was_taken_from` が True にならず、誤爆はしない。
        """
        mine = ctx.chips.get(ctx.my_id, ctx.my_chips)
        return [pid for pid, n in ctx.chips.items()
                if pid != ctx.my_id and pid not in ctx.disqualified and n >= mine]

    def _declare_now(self, ctx) -> bool:
        if ctx.unacted_before_me():
            return True  # 取られる前に撃つ。待てない窓
        return any(ctx.was_taken_from(pid) for pid in self._rich_seats(ctx))

    def declare_cucco_eagerly(self, ctx) -> bool:
        return self._declare_now(ctx)

    def decide_cucco_declare_ctx(self, ctx) -> bool:
        # ここで False を返すと、頭脳は手番をノーチェンジで流す(クク は
        # NEVER_CHANGE_RANKS なので交換には出されない)。以降は誰にも奪われない。
        return self._declare_now(ctx)


POLICIES: dict[str, type[BasePolicy] | type[MatrixPolicy]] = {
    AlwaysChange.name: AlwaysChange,
    AlwaysNoChange.name: AlwaysNoChange,
    MatrixPolicy.name: MatrixPolicy,
    CountingAggressive.name: CountingAggressive,
    CountingConservative.name: CountingConservative,
    CountingExact.name: CountingExact,
    CountingSniper.name: CountingSniper,
}


def make_policy(name: str) -> BasePolicy:
    try:
        return POLICIES[name]()
    except KeyError:
        raise ValueError(f"unknown policy {name!r}; choose from {sorted(POLICIES)}") from None
