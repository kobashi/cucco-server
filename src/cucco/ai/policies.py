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


POLICIES: dict[str, type[BasePolicy] | type[MatrixPolicy]] = {
    AlwaysChange.name: AlwaysChange,
    AlwaysNoChange.name: AlwaysNoChange,
    MatrixPolicy.name: MatrixPolicy,
}


def make_policy(name: str) -> BasePolicy:
    try:
        return POLICIES[name]()
    except KeyError:
        raise ValueError(f"unknown policy {name!r}; choose from {sorted(POLICIES)}") from None
