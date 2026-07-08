"""One Pot: sequential deals until a single winner takes the pooled chips,
or every remaining participant is simultaneously eliminated (wipeout).

docs/rules/final_rules.md §"ゲーム全体の流れ", §"1ディールの流れ", and
§"親の交代". Entry-fee collection and the wipeout-carryover/all-zero-chips
special cases are Game-level policy (docs/rules/final_rules.md §"チップ0枚の
判定タイミング") and are handled by `cucco.domain.game`, not here: `Pot`
simply starts from whatever `carried_chips` it's given.
"""

from __future__ import annotations

import random

from cucco.domain.config import GameConfig
from cucco.domain.deal import Deal
from cucco.domain.deck import Deck
from cucco.domain.errors import IllegalAction
from cucco.domain.events import (
    ChipsPaid,
    ContinuePrompted,
    DealerChanged,
    PlayerLeftPot,
    PotEvent,
    PotWipedOut,
    PotWon,
)

# Deals 1-3 are "child time" (losers pay to stay in); deal 4+ is "adult time"
# (losers are eliminated instead of paying).
CHILD_TIME_DEALS = 3


def _rotate_dealer(participants: list[str], current_dealer: str, eliminated: set[str]) -> str:
    """The next dealer is the next player after `current_dealer` in seating
    order, skipping anyone already eliminated from this pot."""
    idx = participants.index(current_dealer)
    n = len(participants)
    for offset in range(1, n + 1):
        candidate = participants[(idx + offset) % n]
        if candidate not in eliminated:
            return candidate
    raise IllegalAction("no eligible dealer remains")


class Pot:
    def __init__(
        self,
        participants: list[str],
        dealer_id: str,
        chips: dict[str, int],
        config: GameConfig,
        rng: random.Random,
        *,
        carried_chips: int = 0,
        starting_deal_number: int = 1,
        deck: Deck | None = None,
    ) -> None:
        if dealer_id not in participants:
            raise ValueError("dealer_id must be one of the participants")
        if len(set(participants)) != len(participants):
            raise ValueError("participants must not contain duplicates")

        self.participants = list(participants)  # fixed seating order for this pot
        self.dealer_id = dealer_id
        self.chips = chips  # shared reference with Game; mutated in place
        self.config = config
        self.deck = deck if deck is not None else Deck(rng)
        self.pot_chips = carried_chips
        self.deal_number = starting_deal_number - 1  # incremented by start_next_deal()
        self.eliminated: set[str] = set()
        self.current_deal: Deal | None = None
        # loser -> required chip payment, awaiting a continue_declare response
        self._pending_losers: dict[str, int] = {}

    # -- seating ----------------------------------------------------------------

    def active_participants(self) -> list[str]:
        return [p for p in self.participants if p not in self.eliminated]

    # -- deal lifecycle -----------------------------------------------------------

    def start_next_deal(self) -> Deal:
        if self._pending_losers:
            raise IllegalAction("cannot start a new deal while continue_declare responses are pending")
        if self.current_deal is not None and not self.current_deal.is_opened:
            raise IllegalAction("cannot start a new deal while one is in progress")
        active = self.active_participants()
        if len(active) < 2:
            raise IllegalAction("cannot start a deal with fewer than 2 active participants")
        self.deal_number += 1
        self.current_deal = Deal(active, self.dealer_id, self.deck, self.config)
        return self.current_deal

    def resolve_losers(self, deal: Deal, losers: tuple[str, ...]) -> list[PotEvent]:
        """Process a completed deal's losers per child-time/adult-time rules.

        The full loser set for a deal is the union of everyone mid-deal
        disqualified (道化/人間/猫) AND whoever held the weakest card at
        "open" (docs/rules/final_rules.md: "1ディールで複数の敗者が出るこ
        とがある...各敗者はそれぞれ個別にチップを支払う"). If every
        participant was disqualified mid-deal (e.g. a mutual Joker
        exchange), `losers` from `open()` is empty but `deal.disqualified`
        still carries the full loser set.

        Adult-time losers are eliminated immediately. Child-time losers who
        cannot afford the required payment are eliminated too (insolvency).
        Solvent child-time losers are held pending `submit_continue_declare`,
        offered in dealer-first seating order.
        """
        events: list[PotEvent] = []
        is_adult_time = self.deal_number > CHILD_TIME_DEALS
        all_losers = deal.disqualified | set(losers)
        for pid in self._dealer_first_order(all_losers):
            if is_adult_time:
                self.eliminated.add(pid)
                events.append(PlayerLeftPot(player_id=pid, reason="adult_time"))
                continue
            required = self.deal_number
            if self.chips.get(pid, 0) < required:
                self.eliminated.add(pid)
                events.append(PlayerLeftPot(player_id=pid, reason="insolvent"))
                continue
            self._pending_losers[pid] = required
            events.append(ContinuePrompted(player_id=pid, required_chips=required))
        return events

    def _dealer_first_order(self, pids: set[str]) -> list[str]:
        idx = self.participants.index(self.dealer_id)
        n = len(self.participants)
        seated = [self.participants[(idx + i) % n] for i in range(n)]
        return [pid for pid in seated if pid in pids]

    @property
    def awaiting_continue_declare(self) -> frozenset[str]:
        return frozenset(self._pending_losers)

    def submit_continue_declare(self, player_id: str, continue_playing: bool) -> list[PotEvent]:
        if player_id not in self._pending_losers:
            raise IllegalAction(f"{player_id} has no pending continue decision")
        required = self._pending_losers.pop(player_id)
        if not continue_playing:
            self.eliminated.add(player_id)
            return [PlayerLeftPot(player_id=player_id, reason="declined")]
        self.chips[player_id] = self.chips.get(player_id, 0) - required
        self.pot_chips += required
        return [ChipsPaid(player_id=player_id, amount=required, chips_now=self.chips[player_id])]

    # -- pot conclusion -----------------------------------------------------------

    def finalize_deal(self) -> list[PotEvent]:
        """Call once a deal's losers are fully resolved (no pending
        continue_declare responses left). Reports the pot's outcome if it
        has concluded (a single winner, or a simultaneous wipeout);
        otherwise rotates the dealer for the next deal."""
        if self._pending_losers:
            raise IllegalAction("continue_declare responses are still pending")
        active = self.active_participants()
        if len(active) == 1:
            winner = active[0]
            self.chips[winner] = self.chips.get(winner, 0) + self.pot_chips
            won = PotWon(winner=winner, amount=self.pot_chips, chips_now=self.chips[winner])
            self.pot_chips = 0
            return [won]
        if len(active) == 0:
            return [PotWipedOut(amount=self.pot_chips)]
        self.dealer_id = _rotate_dealer(self.participants, self.dealer_id, self.eliminated)
        return [DealerChanged(player_id=self.dealer_id)]
