"""Async orchestration: drives Deal/Pot/Game via domain calls, prompting
sessions and broadcasting the resulting wire events.

The domain layer (Deal/Pot/Game) is entirely synchronous and knows nothing
about asyncio, sessions, or timeouts -- this module is the only place that
combines them. Every `submit_*` call's returned domain events are broadcast
via `_send_event`; every prompt goes through `_prompt`, which sends the
envelope, waits on the target session's inbox with a timeout, and returns
`None` on timeout (the caller applies the documented default behavior).
"""

from __future__ import annotations

import asyncio

from cucco.domain.cards import Rank
from cucco.domain.deal import Deal
from cucco.domain.errors import IllegalAction
from cucco.domain.events import ChipsPaid, ContinuePrompted, DealerChanged, PlayerLeftPot, PotWipedOut, PotWon
from cucco.domain.game import Game
from cucco.domain.pot import Pot
from cucco.protocol.actions import (
    Action,
    CambioDeclare,
    ContinueDeclare,
    CuccoDeclare,
    CuccoPass,
    DealerReady,
    NoChangeDeclare,
)
from cucco.protocol.envelope import build_envelope
from cucco.protocol.wire_events import translate
from cucco.server.session import PlayerSession
from cucco.server.table import Table
from cucco.server.timers import timeout_for


def build_state_snapshot(table: Table, recipient_id: str | None) -> dict:
    game = table.game
    base = {
        "table_id": table.room_id,
        "mode": table.config.mode,
        "spectators": [s.player_id for s in table.spectators()],
    }
    if game is None:
        base.update(
            seats=[_seat_view(s, None, None) for s in table.players()],
            dealer_seat=None,
            current_turn_seat=None,
            pot_number=0,
            deal_number=0,
            deck_remaining_count=0,
            discard_pile=[],
            provenance_map={},
            declarations_this_deal=[],
            your_hand=None,
        )
        return base

    pot = game.current_pot
    deal = pot.current_deal if pot is not None else None
    active_ids = set(pot.active_participants()) if pot is not None else set()

    base.update(
        seats=[_seat_view(s, game, active_ids) for s in table.players()],
        dealer_seat=pot.dealer_id if pot is not None else None,
        current_turn_seat=(deal.legal_actor() if deal is not None and not deal.is_opened else None),
        pot_number=game.pot_number,
        deal_number=pot.deal_number if pot is not None else 0,
        deck_remaining_count=pot.deck.remaining_count if pot is not None else 0,
        discard_pile=(
            [
                {
                    "card": e.card.value,
                    "original_holder": e.original_holder,
                    "discarded_via": e.discarded_via,
                    "discarded_at": e.discarded_at,
                }
                for e in pot.deck.discard_pile
            ]
            if pot is not None
            else []
        ),
        provenance_map=dict(deal.provenance) if deal is not None else {},
        declarations_this_deal=(
            [
                {"player_id": d.player_id, "action": d.action, "via_timeout": d.via_timeout, "ts": d.ts}
                for d in deal.declarations
            ]
            if deal is not None
            else []
        ),
        your_hand=(
            deal.hands[recipient_id].value
            if deal is not None and recipient_id in deal.hands
            else None
        ),
    )
    return base


def _seat_view(session: PlayerSession, game: Game | None, active_ids: set[str] | None) -> dict:
    return {
        "player_id": session.player_id,
        "name": session.name,
        "player_type": session.player_type,
        "chips": game.chips.get(session.player_id, 0) if game is not None else 0,
        "in_current_pot": active_ids is not None and session.player_id in active_ids,
        "connected": session.connected,
    }


class TableRunner:
    def __init__(self, table: Table) -> None:
        self.table = table

    # -- low-level I/O -------------------------------------------------------------

    async def _send_to(self, session: PlayerSession, type_: str, payload: dict) -> None:
        await session.send(build_envelope(type_, payload, table_id=self.table.room_id))

    async def _broadcast(self, type_: str, payload_for: "callable") -> None:
        for session in self.table.sessions.values():
            await self._send_to(session, type_, payload_for(session.player_id))

    async def _send_event(self, event) -> None:
        wire = translate(event)
        if wire is None:
            return
        await self._broadcast(wire.type, wire.for_recipient)

    async def _send_events(self, events: list) -> None:
        for event in events:
            await self._send_event(event)

    async def _broadcast_state_snapshot(self) -> None:
        for session in self.table.sessions.values():
            snapshot = build_state_snapshot(self.table, session.player_id)
            await self._send_to(session, "state_snapshot", snapshot)

    # Internal timeout-lookup keys ("turn", "continue") vs. the wire event
    # type actually sent for that prompt ("turn_prompt", "continue_prompt").
    _WIRE_EVENT_TYPE = {
        "turn": "turn_prompt",
        "continue": "continue_prompt",
        "dealer_ready": "dealer_ready",
        "cucco_window": "cucco_window",
    }

    async def _prompt(
        self,
        session: PlayerSession,
        prompt_type: str,
        expected_types: tuple[type, ...],
        extra_payload: dict | None = None,
    ) -> Action | None:
        """Send `prompt_type` to `session` and wait for a response matching
        `expected_types`. Returns `None` on timeout.

        Any action already sitting in the inbox before this prompt is sent
        is drained first -- it's a leftover response to a PREVIOUS, already-
        closed prompt (e.g. a `cucco_declare` that arrived just after that
        window's timeout) and per docs/protocol/design.md must be silently
        ignored rather than misapplied to this new prompt. A response that
        arrives during THIS window but doesn't match `expected_types` is
        rejected (so a buggy AI gets a diagnostic) and the wait continues
        on the same deadline.
        """
        while not session.inbox.empty():
            session.inbox.get_nowait()

        timeout = timeout_for(self.table.config, prompt_type, session.player_type)
        payload = dict(extra_payload or {})
        payload["timeout_sec"] = timeout
        await self._send_to(session, self._WIRE_EVENT_TYPE[prompt_type], payload)

        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return None
            try:
                action = await asyncio.wait_for(session.inbox.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            if isinstance(action, expected_types):
                return action
            expected_names = ", ".join(t.__name__ for t in expected_types)
            await self._reject(session, f"expected one of [{expected_names}], got {type(action).__name__}")

    async def _reject(self, session: PlayerSession, reason: str) -> None:
        if session.is_ai():
            await self._send_to(session, "action_rejected", {"reason": reason})

    def _connected_count(self, player_ids: list[str]) -> int:
        return sum(1 for pid in player_ids if (session := self.table.get(pid)) is not None and session.connected)

    # -- game lifecycle -----------------------------------------------------------

    async def run(self) -> None:
        game = self.table.game
        assert game is not None
        for event in game.start_first_pot():
            await self._send_event(event)
        await self._broadcast_state_snapshot()

        while not game.is_finished:
            await self._run_pot(game)
        self.table.finished = True

    async def _run_pot(self, game: Game) -> None:
        pot = game.current_pot
        assert pot is not None
        if self._connected_count(pot.active_participants()) < 2:
            # Not enough live connections left to run a deal -- end the game
            # rather than hang forever waiting on players who won't respond.
            await self._send_events(game.force_end())
            await self._broadcast_state_snapshot()
            return
        while True:
            discard_before = len(pot.deck.discard_pile)
            deal = await self._run_deal(pot, game)
            opened = deal.open()[0] if not deal.is_opened else None
            if opened is not None:
                await self._send_event(opened)
            losers = opened.losers if opened is not None else ()

            loser_events = pot.resolve_losers(deal, losers)
            await self._send_events(loser_events)
            resolution_events = list(loser_events)
            for event in loser_events:
                if isinstance(event, ContinuePrompted):
                    resolution_events += await self._handle_continue_prompt(pot, event.player_id)

            outcome_events = pot.finalize_deal()
            await self._send_events(outcome_events)
            await self._send_deal_result(pot, deal, losers, resolution_events, outcome_events, discard_before)

            conclusion = next((e for e in outcome_events if isinstance(e, (PotWon, PotWipedOut))), None)
            if conclusion is not None:
                await self._send_pot_result(pot, conclusion)
                game_events = game.process_pot_outcome(conclusion)
                await self._send_events(game_events)
                await self._broadcast_state_snapshot()
                return  # this pot is done; run() will start the next one (or stop)

    async def _handle_continue_prompt(self, pot: Pot, player_id: str) -> list:
        session = self.table.get(player_id)
        if session is None or not session.connected:
            events = pot.submit_continue_declare(player_id, False)
            await self._send_events(events)
            return events
        action = await self._prompt(session, "continue", (ContinueDeclare,))
        continue_playing = action.continue_playing if action is not None else False
        events = pot.submit_continue_declare(player_id, continue_playing)
        await self._send_events(events)
        return events

    async def _send_deal_result(
        self,
        pot: Pot,
        deal: Deal,
        losers: tuple[str, ...],
        resolution_events: list,
        outcome_events: list,
        discard_before: int,
    ) -> None:
        all_losers = sorted(deal.disqualified | set(losers))
        chips_paid = {e.player_id: e.amount for e in resolution_events if isinstance(e, ChipsPaid)}
        left_pot = sorted(e.player_id for e in resolution_events if isinstance(e, PlayerLeftPot))
        next_dealer = next((e.player_id for e in outcome_events if isinstance(e, DealerChanged)), None)

        discard_now = pot.deck.discard_pile
        # A reshuffle mid-deal clears and rebuilds discard_pile, invalidating
        # the `discard_before` index -- fall back to the whole (post-
        # reshuffle) pile in that case rather than slicing garbage.
        new_discards = discard_now[discard_before:] if len(discard_now) >= discard_before else discard_now

        payload = {
            "losers": all_losers,
            "chips_paid": chips_paid,
            "left_pot": left_pot,
            "chips_now": dict(pot.chips),
            "next_dealer": next_dealer,
            "discarded_cards": [
                {
                    "card": entry.card.value,
                    "original_holder": entry.original_holder,
                    "discarded_via": entry.discarded_via,
                }
                for entry in new_discards
            ],
        }
        await self._broadcast("deal_result", lambda pid: payload)

    async def _send_pot_result(self, pot: Pot, conclusion: PotWon | PotWipedOut) -> None:
        if isinstance(conclusion, PotWon):
            payload = {"result": "won", "winner": conclusion.winner, "amount": conclusion.amount, "chips_now": dict(pot.chips)}
        else:
            payload = {"result": "wiped_out", "amount": conclusion.amount, "chips_now": dict(pot.chips)}
        await self._broadcast("pot_result", lambda pid: payload)

    async def _run_deal(self, pot: Pot, game: Game) -> Deal:
        deal = pot.start_next_deal()
        # A reshuffle can happen during the initial deal-out itself if the
        # shared deck was nearly exhausted; report it before deal_started.
        await self._send_events(deal.take_pending_events())
        await self._broadcast(
            "deal_started",
            lambda pid: {
                "your_hand": deal.hands[pid].value if pid in deal.hands else None,
                "deck_remaining_count": pot.deck.remaining_count,
            },
        )

        # Cucco priority window right after dealing: the dealer is checked
        # first, then everyone else, before "dōzo"/dealer_ready and before
        # the first turn_prompt.
        ordered_seats = [deal.dealer_id] + [pid for pid in deal.order if pid != deal.dealer_id]
        await self._cucco_window(deal, ordered_seats)

        if deal.cucco_declared_by is None:
            dealer_session = self.table.get(deal.dealer_id)
            if dealer_session is not None and dealer_session.connected:
                await self._prompt(dealer_session, "dealer_ready", (DealerReady,))

        while deal.legal_actor() is not None and deal.cucco_declared_by is None:
            actor_id = deal.legal_actor()
            assert actor_id is not None
            events = await self._run_turn(deal, actor_id)
            await self._send_events(events)
            if deal.cucco_declared_by is not None:
                break
            # After this atomic step, re-check every current holder (the
            # exchange that just resolved may have changed who holds クク),
            # in deterministic seat order (docs/protocol/design.md: "親か
            # ら手番順に行う"), not set-iteration order.
            current_holders = deal.current_cucco_holders()
            await self._cucco_window(deal, [pid for pid in deal.order if pid in current_holders])

        game.note_deal_played()
        return deal

    async def _run_turn(self, deal: Deal, actor_id: str) -> list:
        session = self.table.get(actor_id)
        if session is None or not session.connected:
            return deal.submit_no_change(actor_id, via_timeout=True)

        action = await self._prompt(session, "turn", (CambioDeclare, NoChangeDeclare))
        if action is None:
            return deal.submit_no_change(actor_id, via_timeout=True)
        if isinstance(action, CambioDeclare):
            return deal.submit_cambio(actor_id)
        return deal.submit_no_change(actor_id)

    async def _cucco_window(self, deal: Deal, holder_ids: list[str]) -> None:
        for pid in holder_ids:
            if deal.cucco_declared_by is not None:
                return
            if pid in deal.disqualified or deal.hands.get(pid) is not Rank.CUCCO:
                continue
            session = self.table.get(pid)
            if session is None or not session.connected:
                continue
            action = await self._prompt(session, "cucco_window", (CuccoDeclare, CuccoPass))
            if action is None or isinstance(action, CuccoPass):
                continue
            try:
                events = deal.submit_cucco_declare(pid)
                await self._send_events(events)
            except IllegalAction as exc:
                await self._reject(session, str(exc))
