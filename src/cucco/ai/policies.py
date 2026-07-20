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

from cucco.ai.context import FULL_DECK_COUNTS, strength_of
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


POLICIES: dict[str, type[BasePolicy] | type[MatrixPolicy]] = {
    AlwaysChange.name: AlwaysChange,
    AlwaysNoChange.name: AlwaysNoChange,
    MatrixPolicy.name: MatrixPolicy,
    CountingAggressive.name: CountingAggressive,
    CountingConservative.name: CountingConservative,
}


def make_policy(name: str) -> BasePolicy:
    try:
        return POLICIES[name]()
    except KeyError:
        raise ValueError(f"unknown policy {name!r}; choose from {sorted(POLICIES)}") from None
