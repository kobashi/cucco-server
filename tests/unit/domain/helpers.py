from __future__ import annotations

from cucco.domain.cards import Rank
from cucco.domain.config import GameConfig
from cucco.domain.deal import Deal, _rotate_order_dealer_last
from cucco.domain.deck import Deck


def build_deal(
    hand_by_player: dict[str, Rank],
    dealer_id: str,
    deck_tail: list[Rank] | None = None,
    config: GameConfig | None = None,
) -> Deal:
    """Construct a Deal whose initial hands are exactly `hand_by_player`.

    `deck_tail` supplies cards for any subsequent deck draws (e.g. the
    dealer's own deck exchange, or a horse/house chain that runs off the
    end of turn order), drawn in the given order.
    """
    participants = list(hand_by_player.keys())
    order = _rotate_order_dealer_last(participants, dealer_id)
    initial_draws = [hand_by_player[pid] for pid in order]
    deck = Deck.from_fixed_order(initial_draws + (deck_tail or []))
    return Deal(participants, dealer_id, deck, config or GameConfig())
