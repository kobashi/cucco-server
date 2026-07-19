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
  `deal_started`.
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

    @property
    def is_child_time(self) -> bool:
        return self.deal_number <= 3

    @property
    def unseen_total(self) -> int:
        return sum(self.unseen_counts.values())

    def unseen_weaker_than_own(self) -> int:
        own = strength_of(self.own_rank)
        return sum(n for rank, n in self.unseen_counts.items() if strength_of(rank) < own)


def strength_of(rank: str) -> int:
    """Wire-string wrapper over the domain's strength order (道化 weakest --
    the elevated-joker exception only exists after a deck draw, which the
    holder knows about separately)."""
    return strength(next(r for r in RANK_ORDER if r.value == rank))


@dataclass
class CountingTracker:
    discard_counts: Counter = field(default_factory=Counter)
    revealed_this_deal: Counter = field(default_factory=Counter)
    known_held: dict[str, str] = field(default_factory=dict)
    deal_number: int = 0
    pot_chips: int = 0
    dealer_id: str | None = None
    turn_actions_this_deal: int = 0

    def observe(self, event) -> None:
        p = event.payload
        t = event.type
        if t == "pot_started":
            self.discard_counts.clear()
            self.pot_chips = p.get("pot_chips", 0)
            self.dealer_id = p.get("dealer_id")
            self.deal_number = 0
            self._reset_deal()
        elif t == "deal_started":
            self.deal_number += 1
            self._reset_deal()
        elif t == "deck_reshuffled":
            # The discard pile went back into the deck: counting resets.
            self.discard_counts.clear()
            self.revealed_this_deal.clear()
        elif t == "dealer_changed":
            self.dealer_id = p.get("player_id")
        elif t == "exchange_result":
            self._observe_exchange(p)
        elif t == "no_change_declared" or t == "turn_timeout_consumed":
            self.turn_actions_this_deal += 1
        elif t == "player_disqualified":
            if p.get("card"):  # immediate disclosure only; deferred sends null
                self.revealed_this_deal[p["card"]] += 1
            self.known_held.pop(p.get("player_id"), None)
        elif t == "deal_opened":
            for rank in (p.get("hands") or {}).values():
                if rank:
                    self.revealed_this_deal[rank] += 1
        elif t == "deal_result":
            # Authoritative: replace the provisional mid-deal layer.
            for entry in p.get("discarded_cards", ()):
                self.discard_counts[entry["card"]] += 1
            self.revealed_this_deal.clear()
            self.pot_chips = p.get("pot_chips", self.pot_chips)
            if p.get("next_dealer"):
                self.dealer_id = p["next_dealer"]

    def _reset_deal(self) -> None:
        self.revealed_this_deal.clear()
        self.known_held.clear()
        self.turn_actions_this_deal = 0

    def _observe_exchange(self, p: dict) -> None:
        result = p.get("result")
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
