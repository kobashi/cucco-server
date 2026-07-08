"""Deck lifecycle: draw pile, discard pile, and mid-pot reshuffle.

A single `Deck` instance lives for exactly one Pot (docs/rules/final_rules.md
§7-9): shuffled once at pot start, rebuilt from the discard pile whenever the
draw pile is exhausted mid-pot, and fully replaced (new `Deck`) only when the
pot concludes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal

from cucco.domain.cards import Rank, full_deck

DiscardedVia = Literal["open", "disqualification", "deck_draw", "dealer_swap"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DiscardEntry:
    card: Rank
    original_holder: str | None
    discarded_via: DiscardedVia
    discarded_at: str = field(default_factory=now_iso)


class Deck:
    """The draw pile and discard pile for one pot."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._draw: list[Rank] = full_deck()
        self._rng.shuffle(self._draw)
        self.discard_pile: list[DiscardEntry] = []
        self.on_reshuffle: Callable[[], None] | None = None

    @property
    def remaining_count(self) -> int:
        return len(self._draw)

    def draw(self) -> Rank:
        """Draw the top card, rebuilding the draw pile from discards first if empty."""
        if not self._draw:
            self._reshuffle_from_discard()
        return self._draw.pop()

    def discard(self, card: Rank, *, original_holder: str | None, via: DiscardedVia) -> None:
        self.discard_pile.append(
            DiscardEntry(card=card, original_holder=original_holder, discarded_via=via)
        )

    def _reshuffle_from_discard(self) -> None:
        if not self.discard_pile:
            raise RuntimeError("cannot draw: both draw pile and discard pile are empty")
        self._draw = [entry.card for entry in self.discard_pile]
        self._rng.shuffle(self._draw)
        self.discard_pile = []
        if self.on_reshuffle is not None:
            self.on_reshuffle()
