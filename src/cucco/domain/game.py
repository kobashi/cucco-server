"""One Game: a sequence of Pots until an end condition is met.

docs/rules/final_rules.md §"ゲーム全体の流れ" and §"チップ0枚の判定タイミング".
`Game` owns the authoritative `chips` balances across the whole game and
decides, after each Pot concludes, whether to end the game or start the
next Pot -- including the wipeout-carryover special cases (exactly one,
or zero, non-zero-chip players remaining).
"""

from __future__ import annotations

import random

from cucco.domain.config import GameConfig
from cucco.domain.errors import IllegalAction
from cucco.domain.events import GameEnded, GameEvent, PotStarted, PotWipedOut, PotWon
from cucco.domain.pot import CHILD_TIME_DEALS, Pot


class Game:
    def __init__(self, seats: list[str], config: GameConfig, rng: random.Random) -> None:
        if len(set(seats)) != len(seats):
            raise ValueError("seats must not contain duplicates")
        if len(seats) < 2:
            raise ValueError("a game requires at least 2 seats")
        self.seats = list(seats)
        self.config = config
        self.rng = rng
        self.chips: dict[str, int] = {p: config.starting_chips for p in seats}
        self.pot_number = 0
        self.round_count = 0  # total deals played across the whole game
        self.current_pot: Pot | None = None
        self._finished = False
        self.final_ranking: tuple[tuple[str, int], ...] | None = None

    @property
    def is_finished(self) -> bool:
        return self._finished

    def note_deal_played(self) -> None:
        """Call once per completed deal (any pot) to track the round_limit
        end condition."""
        self.round_count += 1

    # -- pot lifecycle -----------------------------------------------------------

    def start_first_pot(self) -> list[GameEvent]:
        if self.pot_number != 0:
            raise IllegalAction("the first pot has already started")
        return self._start_new_pot(self.seats, waive_entry_fee=False)

    def process_pot_outcome(self, outcome: PotWon | PotWipedOut) -> list[GameEvent]:
        """Call with whatever `Pot.finalize_deal()` returned when a pot
        concludes, to determine what happens next: end the game, or start
        the next pot (handling the wipeout-carryover 0/1-remaining-player
        cases)."""
        if self._finished:
            raise IllegalAction("the game has already ended")
        if isinstance(outcome, PotWon):
            if self._chip_zero_end_reached():
                return [self._end_game()]
            return self._start_new_pot(self.seats, waive_entry_fee=False)
        return self._handle_wipeout(outcome.amount)

    def force_end(self) -> list[GameEvent]:
        """Called by the server layer when there aren't enough connected
        players to start the next pot."""
        if self._finished:
            raise IllegalAction("the game has already ended")
        return [self._end_game()]

    # -- internals -----------------------------------------------------------------

    def _handle_wipeout(self, carried_amount: int) -> list[GameEvent]:
        eligible = [p for p in self.seats if self.chips.get(p, 0) > 0]

        if len(eligible) == 1:
            winner = eligible[0]
            self.chips[winner] = self.chips.get(winner, 0) + carried_amount
            won = PotWon(winner=winner, amount=carried_amount, chips_now=self.chips[winner])
            if self._chip_zero_end_reached():
                return [won, self._end_game()]
            return [won, *self._start_new_pot(self.seats, waive_entry_fee=False)]

        if len(eligible) == 0:
            # Everyone is at 0 chips: revive all seats, skip child time
            # (start directly at deal 4 = adult time), and waive the entry
            # fee since nobody can pay it.
            return self._start_new_pot(
                self.seats,
                waive_entry_fee=True,
                carried_chips=carried_amount,
                starting_deal_number=CHILD_TIME_DEALS + 1,
            )

        # >= 2 non-zero-chip players remain: this pot's destination is still
        # undecided, so we simply continue it with the reduced roster --
        # not a fresh pot, so no new entry fee is charged.
        return self._start_new_pot(eligible, waive_entry_fee=True, carried_chips=carried_amount)

    def _start_new_pot(
        self,
        participants: list[str],
        *,
        waive_entry_fee: bool,
        carried_chips: int = 0,
        starting_deal_number: int = 1,
    ) -> list[GameEvent]:
        self.pot_number += 1
        pot_chips = carried_chips
        if not waive_entry_fee:
            for pid in participants:
                self.chips[pid] = self.chips.get(pid, 0) - 1
            pot_chips += len(participants)
        dealer_id = self.rng.choice(participants)
        self.current_pot = Pot(
            participants,
            dealer_id,
            self.chips,
            self.config,
            self.rng,
            carried_chips=pot_chips,
            starting_deal_number=starting_deal_number,
        )
        return [
            PotStarted(
                pot_number=self.pot_number,
                dealer_id=dealer_id,
                participants=tuple(participants),
                chips_now=dict(self.chips),
                entry_fee_waived=waive_entry_fee,
            )
        ]

    def _chip_zero_end_reached(self) -> bool:
        if self.config.end_condition == "chips_zero":
            return any(c <= 0 for c in self.chips.values())
        if self.config.end_condition == "round_limit":
            assert self.config.round_limit is not None
            return self.round_count >= self.config.round_limit
        return False

    def _end_game(self) -> GameEnded:
        self._finished = True
        ranking = tuple(sorted(self.chips.items(), key=lambda kv: kv[1], reverse=True))
        self.final_ranking = ranking
        return GameEnded(ranking=ranking)
