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
import logging

from cucco.domain.cards import Rank
from cucco.domain.deal import DECLARABLE_RANKS, Deal
from cucco.domain.errors import IllegalAction
from cucco.domain.events import ChipsPaid, ContinuePrompted, DealerChanged, PlayerLeftPot, PotWipedOut, PotWon
from cucco.domain.game import Game
from cucco.domain.pot import Pot
from cucco.persistence.action_log import ActionLogWriter
from cucco.persistence.results_store import PlayerInfo, ResultsStore
from cucco.protocol.actions import (
    Action,
    CambioDeclare,
    ContinueDeclare,
    CuccoDeclare,
    CuccoPass,
    DealerReady,
    EffectDeclare,
    EffectPass,
    NoChangeDeclare,
)
from cucco.protocol.envelope import build_envelope
from cucco.protocol.wire_events import translate
from cucco.server.session import PlayerSession
from cucco.server.table import Table
from cucco.server.timers import timeout_for

logger = logging.getLogger("cucco.server.runner")

# How long a pot start waits for disconnected players to come back before
# force-ending the game for lack of connected players. A page reload through
# the tunnel can easily span a pot boundary; without this grace the game
# dies the instant a reload straddles it (the reconnect then lands on an
# already-finished game).
RECONNECT_GRACE_SEC = 60.0


def build_state_snapshot(table: Table, recipient_id: str | None) -> dict:
    game = table.game
    base = {
        "table_id": table.room_id,
        "mode": table.config.mode,
        "spectators": [s.player_id for s in table.spectators()],
        # The EFFECTIVE organizer: falls to the earliest-joined connected
        # player while the original creator is gone (Table.effective_creator_id),
        # so clients always render a working start button somewhere.
        "creator_id": table.effective_creator_id(),
        "ready_ids": sorted(table.ready_ids),
        # A reconnect can land AFTER game_ended was broadcast (e.g. the game
        # force-ended while this player was mid-reload); without these the
        # rejoining client would show a live-looking table forever.
        "game_finished": game is not None and game.is_finished,
        "final_ranking": (
            [[pid, chips] for pid, chips in game.final_ranking]
            if game is not None and game.final_ranking is not None
            else None
        ),
    }
    if game is None:
        base.update(
            seats=[_seat_view(s, None, None) for s in table.players()],
            dealer_seat=None,
            current_turn_seat=None,
            pot_number=0,
            deal_number=0,
            pot_chips=0,
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

    # Seats are reported in GAME seating order (randomized at game start),
    # not join order -- the on-screen arrangement then matches the actual
    # turn direction. Late joiners not seated in this game sort last.
    seat_rank = {pid: i for i, pid in enumerate(game.seats)}
    ordered_players = sorted(table.players(), key=lambda s: seat_rank.get(s.player_id, len(seat_rank)))

    base.update(
        seats=[_seat_view(s, game, active_ids) for s in ordered_players],
        dealer_seat=pot.dealer_id if pot is not None else None,
        current_turn_seat=(deal.legal_actor() if deal is not None and not deal.is_opened else None),
        pot_number=game.pot_number,
        deal_number=pot.deal_number if pot is not None else 0,
        pot_chips=pot.pot_chips if pot is not None else 0,
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
    def __init__(
        self,
        table: Table,
        action_log: ActionLogWriter | None = None,
        results_store: ResultsStore | None = None,
    ) -> None:
        self.table = table
        self.action_log = action_log
        self.results_store = results_store

    # -- low-level I/O -------------------------------------------------------------

    async def _send_to(self, session: PlayerSession, type_: str, payload: dict) -> None:
        await session.send(build_envelope(type_, payload, table_id=self.table.room_id))

    async def _broadcast(self, type_: str, payload_for: "callable") -> None:
        # Snapshot the session list before awaiting each send: a
        # join_table arriving mid-broadcast (a different asyncio task)
        # would otherwise mutate table.sessions while this loop iterates
        # it and raise RuntimeError, aborting the broadcast (and, in
        # evaluation mode, the whole game_count run) for everyone.
        for session in list(self.table.sessions.values()):
            await self._send_to(session, type_, payload_for(session.player_id))

    async def _send_event(self, event) -> None:
        # Every domain event is a "result" worth recording for replay
        # (docs/protocol/design.md 「永続化・成績記録」), even ones with no
        # wire representation (e.g. a cambio/cucco_declare Declaration).
        if self.action_log is not None:
            self.action_log.write_event(event)
        wire = translate(event)
        if wire is None:
            return
        await self._broadcast(wire.type, wire.for_recipient)

    async def _send_events(self, events: list) -> None:
        for event in events:
            await self._send_event(event)

    async def _broadcast_state_snapshot(self) -> None:
        for session in list(self.table.sessions.values()):
            snapshot = build_state_snapshot(self.table, session.player_id)
            await self._send_to(session, "state_snapshot", snapshot)

    # Internal timeout-lookup keys ("turn", "continue") vs. the wire event
    # type actually sent for that prompt ("turn_prompt", "continue_prompt").
    _WIRE_EVENT_TYPE = {
        "turn": "turn_prompt",
        "continue": "continue_prompt",
        "dealer_ready": "dealer_ready",
        "cucco_window": "cucco_window",
        "effect_window": "effect_window",
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
        on the same deadline -- except a stray `CuccoDeclare`/`CuccoPass`,
        which docs/protocol/design.md explicitly says must never be
        `action_rejected` (a well-behaved AI would otherwise get spurious
        rejections just from network-delay timing against a closed window).
        """
        while not session.inbox.empty():
            session.inbox.get_nowait()

        timeout = timeout_for(self.table.config, prompt_type, session.player_type)
        payload = dict(extra_payload or {})
        payload["timeout_sec"] = timeout
        wire_type = self._WIRE_EVENT_TYPE[prompt_type]
        deadline = asyncio.get_event_loop().time() + timeout
        # Published so a reconnect (dispatch._handle_join_table) can re-send
        # this prompt with the remaining time -- the original envelope went
        # to a connection that may be dead by the time the player is back.
        session.outstanding_prompt = {"type": wire_type, "payload": payload, "deadline": deadline}
        try:
            await self._send_to(session, wire_type, payload)

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
                if isinstance(action, (CuccoDeclare, CuccoPass, EffectDeclare, EffectPass)):
                    # Late answers to an already-closed window are pure
                    # network-delay timing; never action_rejected for these
                    # (docs/protocol/design.md).
                    continue
                expected_names = ", ".join(t.__name__ for t in expected_types)
                await self._reject(session, f"expected one of [{expected_names}], got {type(action).__name__}")
        finally:
            session.outstanding_prompt = None

    async def _reject(self, session: PlayerSession, reason: str) -> None:
        if session.is_ai():
            await self._send_to(session, "action_rejected", {"reason": reason})

    def _connected_count(self, player_ids: list[str]) -> int:
        return sum(1 for pid in player_ids if (session := self.table.get(pid)) is not None and session.connected)

    # -- game lifecycle -----------------------------------------------------------

    async def run(self) -> None:
        """Drives `self.table.game` to completion (one `game_ended`). Does
        NOT mark `self.table.finished` -- that's the caller's call, since a
        table can run several games back-to-back in evaluation mode and
        only the caller knows whether this was the last one."""
        game = self.table.game
        assert game is not None
        try:
            for event in game.start_first_pot():
                await self._send_event(event)
            await self._broadcast_state_snapshot()

            while not game.is_finished:
                await self._run_pot(game)

            if self.results_store is not None:
                # The game itself already finished successfully and every
                # player has already received `game_ended` by this point --
                # a persistence failure here must not surface to players as
                # "this table has stopped" (that's what _run_table_safely's
                # except-clause would do if this propagated).
                try:
                    self._record_results(game)
                except Exception:
                    logger.exception("failed to record results for table %s", self.table.room_id)
        finally:
            if self.action_log is not None:
                # Same reasoning as the results_store guard above: a close()
                # failure (e.g. a flush error) must not masquerade as the
                # table having crashed once the game already finished.
                try:
                    self.action_log.close()
                except Exception:
                    logger.exception("failed to close action log for table %s", self.table.room_id)

    def _record_results(self, game: Game) -> None:
        assert game.final_ranking is not None
        players = [
            PlayerInfo(pid, session.name, session.player_type)
            for pid in game.seats
            if (session := self.table.get(pid)) is not None
        ]
        self.results_store.record_game_ended(
            table_id=self.table.room_id,
            mode=self.table.config.mode,
            players=players,
            ranking=game.final_ranking,
            action_log_path=str(self.action_log.path) if self.action_log is not None and self.action_log.path is not None else None,
        )

    async def _run_pot(self, game: Game) -> None:
        pot = game.current_pot
        assert pot is not None
        if self._connected_count(pot.active_participants()) < 2:
            await self._await_reconnections(pot)
        if self._connected_count(pot.active_participants()) < 2:
            # Not enough live connections left to run a deal -- end the game
            # rather than hang forever waiting on players who won't respond.
            await self._send_events(game.force_end())
            await self._broadcast_state_snapshot()
            return
        while True:
            discard_before = len(pot.deck.discard_pile)
            reshuffle_count_before = pot.deck.reshuffle_count
            deal = await self._run_deal(pot, game)
            opened = deal.open()[0] if not deal.is_opened else None
            if opened is not None:
                await self._send_event(opened)
            losers = opened.losers if opened is not None else ()

            # Reading pause: everyone gets a moment to review the opened
            # hands BEFORE any loser's continue prompt pops over them and
            # before the next deal starts (the server otherwise proceeds
            # immediately, leaving no time to take the result in).
            await self._result_pause()

            loser_events = pot.resolve_losers(deal, losers)
            await self._send_events(loser_events)
            resolution_events = list(loser_events)
            for event in loser_events:
                if isinstance(event, ContinuePrompted):
                    resolution_events += await self._handle_continue_prompt(pot, event.player_id)

            # Captured before finalize_deal(), which pays the pot out to a
            # winner (zeroing pot_chips) when the deal concludes the pot --
            # deal_result should show the pot as it stands after the losers
            # paid in, i.e. what's physically on the table before any scoop.
            pot_chips_after_payments = pot.pot_chips
            outcome_events = pot.finalize_deal()
            await self._send_events(outcome_events)
            reshuffled = pot.deck.reshuffle_count != reshuffle_count_before
            await self._send_deal_result(
                pot, deal, losers, resolution_events, outcome_events, discard_before, reshuffled, pot_chips_after_payments
            )

            conclusion = next((e for e in outcome_events if isinstance(e, (PotWon, PotWipedOut))), None)
            if conclusion is not None:
                await self._send_pot_result(pot.chips, conclusion)
                game_events = game.process_pot_outcome(conclusion)
                await self._send_events(game_events)
                # docs/protocol/design.md:159 -- after a wipeout carryover,
                # if exactly one solvent seat remains, Game resolves that
                # instantly (no deal played) and reports it as a PotWon
                # among game_events rather than via Pot.finalize_deal(). It
                # still needs its own pot_result aggregate.
                instant_win = next((e for e in game_events if isinstance(e, PotWon)), None)
                if instant_win is not None:
                    await self._send_pot_result(game.chips, instant_win)
                if not game.is_finished:
                    # Review the pot outcome before the next pot deals. No
                    # pause when the game just ended: the ranking screen is
                    # user-paced, and delaying here would also delay the
                    # room's reset for the 続ける flow.
                    await self._result_pause()
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
        reshuffled: bool,
        pot_chips: int,
    ) -> None:
        all_losers = sorted(deal.disqualified | set(losers))
        chips_paid = {e.player_id: e.amount for e in resolution_events if isinstance(e, ChipsPaid)}
        left_pot = sorted(e.player_id for e in resolution_events if isinstance(e, PlayerLeftPot))
        next_dealer = next((e.player_id for e in outcome_events if isinstance(e, DealerChanged)), None)

        # A reshuffle mid-deal clears and rebuilds discard_pile, invalidating
        # the `discard_before` index -- fall back to the whole (post-
        # reshuffle) pile in that case rather than slicing against a stale
        # offset (`Deck.reshuffle_count` is what actually tells us this
        # happened; the pile's length alone is ambiguous once it's grown
        # back past its old size).
        discard_now = pot.deck.discard_pile
        new_discards = discard_now if reshuffled else discard_now[discard_before:]

        payload = {
            "losers": all_losers,
            "chips_paid": chips_paid,
            "left_pot": left_pot,
            "chips_now": dict(pot.chips),
            "pot_chips": pot_chips,
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

    async def _await_reconnections(self, pot: Pot) -> None:
        # Evaluation mode has no humans to wait for; a dead AI is a bug in
        # that AI, not something to stall the whole run over.
        if self.table.config.mode == "evaluation":
            return
        deadline = asyncio.get_event_loop().time() + RECONNECT_GRACE_SEC
        while asyncio.get_event_loop().time() < deadline:
            if self._connected_count(pot.active_participants()) >= 2:
                return
            await asyncio.sleep(1.0)

    async def _result_pause(self) -> None:
        # Evaluation mode explicitly omits human-pacing waits
        # (docs/protocol/design.md 「AI専用高速評価モード」).
        if self.table.config.mode == "evaluation" or self.table.config.result_pause_sec <= 0:
            return
        game = self.table.game
        seated = set(game.seats) if game is not None else set()
        self.table.result_acks.clear()
        timeout = self.table.config.result_pause_sec
        await self._broadcast("result_pause", lambda pid: {"timeout_sec": timeout})
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            # Skippable: once every seated, connected HUMAN has acked the
            # result screen there is nobody left to wait for (AIs never ack
            # -- the pause is a human-pacing feature; an all-AI normal table
            # skips it outright).
            unacked = [
                s
                for s in self.table.players()
                if s.player_type == "human" and s.connected and s.player_id in seated and s.player_id not in self.table.result_acks
            ]
            if not unacked:
                return
            await asyncio.sleep(0.2)

    async def _send_pot_result(self, chips: dict[str, int], conclusion: PotWon | PotWipedOut) -> None:
        if isinstance(conclusion, PotWon):
            payload = {"result": "won", "winner": conclusion.winner, "amount": conclusion.amount, "chips_now": dict(chips)}
        else:
            payload = {"result": "wiped_out", "amount": conclusion.amount, "chips_now": dict(chips)}
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
            if self.table.config.effect_declaration == "declared":
                return await self._run_declared_cambio(deal, actor_id)
            return deal.submit_cambio(actor_id)
        return deal.submit_no_change(actor_id)

    async def _run_declared_cambio(self, deal: Deal, requester: str) -> list:
        """effect_declaration="declared": walk the exchange one target at a
        time, giving each declarable-card holder an effect_window before the
        swap happens. Silence (pass / timeout / disconnected) means the
        effect does NOT fire and the exchange succeeds -- so unlike the base
        rules, holding 猫 or 人間 no longer protects you automatically.
        The whole walk is one atomic exchange: no cucco windows in between.
        """
        events, target = deal.begin_cambio(requester)
        while target is not None:
            declared = False
            if deal.hands.get(target) in DECLARABLE_RANKS:
                session = self.table.get(target)
                if session is not None and session.connected:
                    answer = await self._prompt(
                        session, "effect_window", (EffectDeclare, EffectPass), {"requester": requester}
                    )
                    declared = isinstance(answer, EffectDeclare)
            if declared:
                step_events, target = deal.resolve_effect_declared(requester, target)
                events += step_events
            else:
                events += deal.resolve_exchange_accept(requester, target)
                target = None
        return events

    async def _cucco_window(self, deal: Deal, holder_ids: list[str]) -> None:
        for pid in holder_ids:
            if deal.cucco_declared_by is not None:
                return
            if pid in deal.disqualified or deal.hands.get(pid) is not Rank.CUCCO:
                continue
            session = self.table.get(pid)
            if session is None or not session.connected:
                self._log_cucco_pass(pid, via_timeout=True)
                continue
            action = await self._prompt(session, "cucco_window", (CuccoDeclare, CuccoPass))
            if action is None or isinstance(action, CuccoPass):
                self._log_cucco_pass(pid, via_timeout=action is None)
                continue
            try:
                events = deal.submit_cucco_declare(pid)
                await self._send_events(events)
            except IllegalAction as exc:
                await self._reject(session, str(exc))

    def _log_cucco_pass(self, player_id: str, *, via_timeout: bool) -> None:
        # cucco_pass deliberately produces no domain event (recording it
        # publicly would leak who holds クク) -- the action log is the only
        # place this is ever recorded (Deal.submit_cucco_pass's docstring).
        if self.action_log is not None:
            self.action_log.write_action(player_id, "cucco_pass", {"via_timeout": via_timeout})
