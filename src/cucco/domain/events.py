"""Domain events produced by Deal/Pot/Game.

These are transport-agnostic: the protocol layer (`cucco.protocol.wire_events`)
translates each of these into per-recipient wire payloads, and the
persistence layer fans the same events out into the action log / results
store. The domain layer itself never imports either of those.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from cucco.domain.cards import Rank
from cucco.domain.timeutil import now_iso


@dataclass(frozen=True)
class Declaration:
    """A player declared cambio / no-change / cucco on their own turn (or cucco
    out of turn). `cucco_pass` is deliberately NOT represented here — it is
    never part of the public declaration history (docs/protocol/design.md)."""

    player_id: str
    action: str  # "cambio" | "no_change" | "cucco_declare"
    via_timeout: bool = False
    ts: str = field(default_factory=now_iso, compare=False)


@dataclass(frozen=True)
class ExchangeAccepted:
    """A live exchange between two players completed (no refusal)."""

    requester: str
    target: str
    requester_new_card: Rank
    target_new_card: Rank


@dataclass(frozen=True)
class DeckExchangeAccepted:
    """The deck-exchange actor successfully kept a newly drawn card."""

    actor: str
    new_card: Rank
    given_up_card: Rank


@dataclass(frozen=True)
class ExchangeRefused:
    """A live exchange target refused via a special card's effect."""

    requester: str
    target: str
    reason: str  # "house_horse_skip" | "cat_meow" | "human_refusal"
    revealed_rank: Rank | None  # None only possible for house_horse_skip with reveal off


@dataclass(frozen=True)
class DeckDrawRefused:
    """A card drawn from the deck was not kept by the deck-exchange actor."""

    actor: str
    drawn_rank: Rank
    reason: str  # "cucco_refusal" | "human_deck_draw" | "cat_deck_draw" | "horse_house_chain"


@dataclass(frozen=True)
class PlayerDisqualified:
    player_id: str
    cause: str  # "received_joker" | "human_refusal" | "human_deck_draw" | "cat_refusal" | "cat_deck_draw"
    card: Rank | None  # None when disqualified_card_disclosure is "deferred"


@dataclass(frozen=True)
class CuccoDeclared:
    player_id: str


@dataclass(frozen=True)
class DealOpened:
    hands: dict[str, Rank]  # non-disqualified players' final hands
    elevated_joker_holders: frozenset[str]
    losers: tuple[str, ...]


@dataclass(frozen=True)
class DeckReshuffled:
    remaining_count: int


DealEvent = Union[
    Declaration,
    ExchangeAccepted,
    DeckExchangeAccepted,
    ExchangeRefused,
    DeckDrawRefused,
    PlayerDisqualified,
    CuccoDeclared,
    DealOpened,
    DeckReshuffled,
]
