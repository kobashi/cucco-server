"""Card rank model for Cucco.

The deck has 22 distinct ranks, two copies of each (44 cards total). Ranks
are compared purely by their position in :data:`RANK_ORDER`; no per-card
instance identity exists anywhere in this codebase (see docs/rules/final_rules.md
for why: card provenance is tracked per player slot, not per physical card).
"""

from __future__ import annotations

from enum import Enum


class Rank(str, Enum):
    """The 22 card ranks. Values are the Japanese rank names used on the wire."""

    JOKER = "道化"
    LION = "獅子"
    MASK = "仮面"
    BUCKET = "桶"
    N0 = "0"
    N1 = "1"
    N2 = "2"
    N3 = "3"
    N4 = "4"
    N5 = "5"
    N6 = "6"
    N7 = "7"
    N8 = "8"
    N9 = "9"
    N10 = "10"
    N11 = "11"
    N12 = "12"
    HOUSE = "家"
    CAT = "猫"
    HORSE = "馬"
    HUMAN = "人間"
    CUCCO = "クク"


RANK_ORDER: tuple[Rank, ...] = (
    Rank.JOKER,
    Rank.LION,
    Rank.MASK,
    Rank.BUCKET,
    Rank.N0,
    Rank.N1,
    Rank.N2,
    Rank.N3,
    Rank.N4,
    Rank.N5,
    Rank.N6,
    Rank.N7,
    Rank.N8,
    Rank.N9,
    Rank.N10,
    Rank.N11,
    Rank.N12,
    Rank.HOUSE,
    Rank.CAT,
    Rank.HORSE,
    Rank.HUMAN,
    Rank.CUCCO,
)

assert len(RANK_ORDER) == 22
assert set(RANK_ORDER) == set(Rank)

_STRENGTH = {rank: index for index, rank in enumerate(RANK_ORDER)}

# A Joker drawn from the deck by the current deck-exchange actor becomes
# exceptionally the single strongest card in the deal (stronger than Cucco).
ELEVATED_JOKER_STRENGTH = len(RANK_ORDER)

# Ranks with an active effect (as opposed to "plain" ranks, which must always
# accept an exchange and otherwise behave with no special effect).
SPECIAL_RANKS = frozenset({Rank.JOKER, Rank.HOUSE, Rank.CAT, Rank.HORSE, Rank.HUMAN, Rank.CUCCO})

# The two ranks whose refusal chains the request to the next player in turn
# order (and ultimately to the deck if the chain runs off the end).
CHAINING_RANKS = frozenset({Rank.HOUSE, Rank.HORSE})


def strength(rank: Rank, *, elevated: bool = False) -> int:
    """Return a comparable strength for `rank` (higher wins/survives at open).

    `elevated=True` represents a Joker drawn from the deck, which becomes
    exceptionally the single strongest card (above Cucco) for that deal.
    """
    if elevated:
        if rank is not Rank.JOKER:
            raise ValueError("only a Joker can be elevated")
        return ELEVATED_JOKER_STRENGTH
    return _STRENGTH[rank]


def full_deck() -> list[Rank]:
    """Two copies of each of the 22 ranks (44 cards total), unshuffled."""
    return [rank for rank in RANK_ORDER for _ in range(2)]
