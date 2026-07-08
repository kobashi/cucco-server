"""Translate domain events (cucco.domain.events) into wire events.

Each domain event maps to a `WireEvent`: a `type` string plus a `public`
payload (safe to broadcast to everyone) and an optional `private` payload
keyed by recipient player_id (merged on top of `public` only for that
recipient). This keeps card-privacy rules (e.g. an exchange's new card is
only revealed to the two participants) in one place, independent of who
ends up calling `for_recipient`.

Some wire events described in docs/protocol/design.md (`deal_result`,
`pot_result`, `state_snapshot`) are *aggregates* built from several domain
events plus live Deal/Pot/Game state -- those are assembled by the server
layer (`cucco.server.table`), not here. This module only handles the
events that map cleanly from a single domain event.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cucco.domain.events import (
    ChipsPaid,
    ContinuePrompted,
    CuccoDeclared,
    DealerChanged,
    DealEvent,
    DealOpened,
    Declaration,
    DeckDrawRefused,
    DeckExchangeAccepted,
    DeckReshuffled,
    ExchangeAccepted,
    ExchangeRefused,
    GameEnded,
    GameEvent,
    PlayerDisqualified,
    PlayerLeftPot,
    PotEvent,
    PotStarted,
    PotWipedOut,
    PotWon,
)


@dataclass(frozen=True)
class WireEvent:
    type: str
    public: dict
    private: dict[str, dict] = field(default_factory=dict)

    def for_recipient(self, recipient: str | None) -> dict:
        payload = dict(self.public)
        if recipient is not None and recipient in self.private:
            payload.update(self.private[recipient])
        return payload


def _rank_value(rank) -> str | None:
    return rank.value if rank is not None else None


def translate(event: DealEvent | PotEvent | GameEvent) -> WireEvent | None:
    """Translate one domain event into a WireEvent, or None if it has no
    direct wire representation (e.g. a `cambio` Declaration, which is
    implied by the ExchangeAccepted/ExchangeRefused event that follows it
    in the same `submit_cambio` call)."""

    if isinstance(event, Declaration):
        if event.action == "no_change":
            wire_type = "turn_timeout_consumed" if event.via_timeout else "no_change_declared"
            return WireEvent(wire_type, {"player_id": event.player_id})
        return None  # "cambio" / "cucco_declare" declarations carry no info of their own

    if isinstance(event, ExchangeAccepted):
        return WireEvent(
            "exchange_result",
            public={"result": "accepted", "requester": event.requester, "target": event.target},
            private={
                event.requester: {"your_new_card": _rank_value(event.requester_new_card)},
                event.target: {"your_new_card": _rank_value(event.target_new_card)},
            },
        )

    if isinstance(event, DeckExchangeAccepted):
        return WireEvent(
            "exchange_result",
            public={"result": "deck_exchange_accepted", "actor": event.actor},
            private={
                event.actor: {
                    "new_card": _rank_value(event.new_card),
                    "given_up_card": _rank_value(event.given_up_card),
                }
            },
        )

    if isinstance(event, ExchangeRefused):
        return WireEvent(
            "exchange_result",
            public={
                "result": "refused",
                "requester": event.requester,
                "target": event.target,
                "reason": event.reason,
                "revealed_rank": _rank_value(event.revealed_rank),
            },
        )

    if isinstance(event, DeckDrawRefused):
        # The drawn card is immediately discarded face-up (discarded_via
        # "deck_draw"), so it's already public information by the time this
        # event fires -- safe to include directly.
        return WireEvent(
            "exchange_result",
            public={
                "result": "deck_draw_refused",
                "actor": event.actor,
                "drawn_rank": _rank_value(event.drawn_rank),
                "reason": event.reason,
            },
        )

    if isinstance(event, PlayerDisqualified):
        return WireEvent(
            "player_disqualified",
            public={
                "player_id": event.player_id,
                "cause": event.cause,
                "card": _rank_value(event.card),  # None when disclosure is "deferred"
            },
        )

    if isinstance(event, CuccoDeclared):
        return WireEvent("cucco_declared", public={"player_id": event.player_id})

    if isinstance(event, DealOpened):
        return WireEvent(
            "deal_opened",
            public={
                "hands": {pid: _rank_value(card) for pid, card in event.hands.items()},
                "elevated_joker_holders": sorted(event.elevated_joker_holders),
                "losers": list(event.losers),
            },
        )

    if isinstance(event, DeckReshuffled):
        return WireEvent("deck_reshuffled", public={"remaining_count": event.remaining_count})

    if isinstance(event, ChipsPaid):
        return WireEvent(
            "chips_paid",
            public={"player_id": event.player_id, "amount": event.amount, "chips_now": event.chips_now},
        )

    if isinstance(event, PlayerLeftPot):
        return WireEvent("player_left_pot", public={"player_id": event.player_id, "reason": event.reason})

    if isinstance(event, ContinuePrompted):
        # Broadcast-safe summary; the actual `continue_prompt` sent to the
        # affected player (with their timeout) is assembled by the server
        # layer, which knows the live timeout configuration.
        return WireEvent(
            "continue_prompted",
            public={"player_id": event.player_id, "required_chips": event.required_chips},
        )

    if isinstance(event, DealerChanged):
        return WireEvent("dealer_changed", public={"player_id": event.player_id})

    if isinstance(event, PotWon):
        return WireEvent(
            "pot_won",
            public={"winner": event.winner, "amount": event.amount, "chips_now": event.chips_now},
        )

    if isinstance(event, PotWipedOut):
        return WireEvent("pot_wiped_out", public={"amount": event.amount})

    if isinstance(event, PotStarted):
        return WireEvent(
            "pot_started",
            public={
                "pot_number": event.pot_number,
                "dealer_id": event.dealer_id,
                "participants": list(event.participants),
                "chips_now": dict(event.chips_now),
                "entry_fee_waived": event.entry_fee_waived,
            },
        )

    if isinstance(event, GameEnded):
        return WireEvent(
            "game_ended",
            public={"ranking": [[pid, chips] for pid, chips in event.ranking]},
        )

    raise TypeError(f"no wire translation for domain event type {type(event).__name__}")
