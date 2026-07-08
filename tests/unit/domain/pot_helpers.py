from __future__ import annotations

import random

from cucco.domain.cards import Rank
from cucco.domain.config import GameConfig
from cucco.domain.deck import Deck
from cucco.domain.pot import Pot


def make_pot(
    participants: list[str],
    dealer_id: str,
    chips: dict[str, int],
    deck_cards: list[Rank],
    config: GameConfig | None = None,
    carried_chips: int = 0,
) -> Pot:
    deck = Deck.from_fixed_order(deck_cards)
    return Pot(
        participants,
        dealer_id,
        chips,
        config or GameConfig(),
        random.Random(0),
        carried_chips=carried_chips,
        deck=deck,
    )


def play_deal_all_no_change(pot: Pot) -> tuple:
    """Deal a hand and have every active participant no-change, returning
    the deal's (disqualified | weakest) loser set as reported by open()."""
    deal = pot.start_next_deal()
    for pid in deal.order:
        deal.submit_no_change(pid)
    opened = deal.open()[0]
    return deal, opened.losers
