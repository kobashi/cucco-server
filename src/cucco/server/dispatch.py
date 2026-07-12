"""Per-connection receive loop: parse an incoming message, validate it, and
either handle it directly (identify/create_table/join_table/ready) or hand
it to the running TableRunner via the session's inbox queue.

Note: `ready` currently gates only the table's FIRST pot (normal mode) or
the whole game_count run (evaluation mode -- docs/protocol/design.md
「AI専用高速評価モード」: "1回のreadyで複数ゲームを自動連続実行"). Once a
game is running, later pots use the domain layer's built-in auto-revival
(every seat rejoins automatically -- docs/rules/final_rules.md "次のポッ
トへの参加"); per-pot re-ready-gating would require extending the domain
Game API and is a known simplification of this implementation.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from pathlib import Path

from cucco.domain.errors import IllegalAction
from cucco.domain.game import Game
from cucco.evaluation.runner import EvaluationRunner
from cucco.persistence.action_log import ActionLogWriter, open_for_game
from cucco.persistence.results_store import ResultsStore
from cucco.protocol.actions import (
    CambioDeclare,
    ContinueDeclare,
    CreateTable,
    CuccoDeclare,
    CuccoPass,
    DealerReady,
    EffectDeclare,
    EffectPass,
    Identify,
    JoinTable,
    NoChangeDeclare,
    Ready,
    ResultAck,
    StartPot,
    create_table_to_config,
    folded_name,
    parse_action,
)
from cucco.protocol.envelope import build_envelope, check_protocol_version, parse_envelope
from cucco.protocol.errors import ProtocolError
from cucco.server.registry import MAX_PLAYERS_PER_TABLE, MAX_SPECTATORS_PER_TABLE, TableRegistry
from cucco.server.runner import TableRunner, build_state_snapshot
from cucco.server.session import Connection, PlayerSession
from cucco.server.table import Table

QUEUE_ROUTED = (DealerReady, CambioDeclare, NoChangeDeclare, CuccoDeclare, CuccoPass, ContinueDeclare, EffectDeclare, EffectPass)

# A generous safety-net window for players to join and declare `ready`
# before the first pot starts anyway (docs/protocol/design.md: "ready"の
# タイムアウトはそのポットに参加しない扱いになる). Not tied to turn_timeout_*
# since this is a lobby-wide wait, not a single player's per-action budget.
# The table creator can also force an earlier start via `start_pot` once
# enough players are ready, so this is a fallback rather than the primary
# mechanism -- hence the generous 10-minute window.
READY_TIMEOUT_SEC = 600.0

logger = logging.getLogger("cucco.server.dispatch")


def _eligible_participant_ids(table: Table) -> list[str]:
    """Who can actually become a game participant. In evaluation mode only
    AI players count -- humans/spectators may join to watch, but per
    docs/protocol/design.md 「AI専用高速評価モード」 they never play."""
    ids = table.player_ids()
    if table.config.mode == "evaluation":
        ids = [pid for pid in ids if (session := table.get(pid)) is not None and session.player_type == "ai"]
    return ids


async def _start_game(table: Table) -> None:
    if table.game is not None or table.evaluation_started:
        return
    participants = [pid for pid in _eligible_participant_ids(table) if pid in table.ready_ids]
    if len(participants) < table.min_players:
        # Not enough players readied up in time -- reset so a fresh round
        # of `ready` declarations can retry rather than wedging forever.
        # The watchdog task that got us here has already fired (or is being
        # cancelled by the caller); clear it too so the next `ready` spawns
        # a fresh one instead of finding a stale non-None task and never
        # re-arming.
        table.ready_ids.clear()
        table.ready_deadline_task = None
        return

    if table.config.mode == "evaluation":
        # EvaluationRunner assigns table.game itself, once per game_count
        # iteration -- this flag is the re-entry guard in the meantime
        # (see Table.evaluation_started's docstring).
        table.evaluation_started = True
        asyncio.create_task(_run_evaluation_safely(table, participants))
        return

    # Recorded up front (docs/protocol/design.md 「永続化・成績記録」) so the
    # game is deterministically replayable from the action log alone --
    # random.Random() with no seed draws from OS entropy and can't be
    # recovered after the fact.
    seed = random.SystemRandom().randrange(2**63)
    action_log = open_for_game(table.action_log_dir, table.room_id) if table.action_log_dir is not None else None
    if action_log is not None:
        action_log.write_seed(seed)

    rng = random.Random(seed)
    # Seating order is randomized per game (participants arrive in join
    # order, which would otherwise bake a fixed seat bias into every game at
    # the table). Shuffled with the SAME seeded rng the Game uses, so the
    # recorded seed still deterministically reproduces seats + deals alike.
    rng.shuffle(participants)
    table.game = Game(participants, table.config, rng)
    asyncio.create_task(_run_table_safely(table, action_log))


async def _ready_timeout_watchdog(table: Table) -> None:
    await asyncio.sleep(READY_TIMEOUT_SEC)
    if table.game is None and not table.evaluation_started:
        await _start_game(table)


async def _notify_table_crashed(table: Table) -> None:
    table.finished = True
    for session in list(table.sessions.values()):
        try:
            await session.send(
                build_envelope(
                    "action_rejected",
                    {"reason": "internal server error -- this table has stopped"},
                    table_id=table.room_id,
                )
            )
        except Exception:
            logger.exception("failed to notify session %s of table crash", session.player_id)


async def _run_table_safely(table: Table, action_log: ActionLogWriter | None = None) -> None:
    """`TableRunner.run()` is launched as a fire-and-forget task; without
    this wrapper, an uncaught exception would silently kill the task and
    leave the table permanently hung with no explanation to its players."""
    try:
        await TableRunner(table, action_log=action_log, results_store=table.results_store).run()
        # A normal-mode room outlives its game: reset to the waiting state so
        # the same room (same room_id, same and/or new players) can ready up
        # and start another game with fresh chips, instead of becoming a
        # zombie that silently swallows every `ready`.
        table.game = None
        table.ready_ids.clear()
        if table.ready_deadline_task is not None:
            table.ready_deadline_task.cancel()
            table.ready_deadline_task = None
    except Exception:
        logger.exception("TableRunner crashed for table %s", table.room_id)
        await _notify_table_crashed(table)


async def _run_evaluation_safely(table: Table, participants: list[str]) -> None:
    """Same fire-and-forget crash-resilience as `_run_table_safely`, for
    the evaluation-mode game_count loop."""
    try:
        await EvaluationRunner(table, participants).run()
    except Exception:
        logger.exception("EvaluationRunner crashed for table %s", table.room_id)
        await _notify_table_crashed(table)


class ConnectionHandler:
    def __init__(
        self,
        connection: Connection,
        registry: TableRegistry,
        results_store: ResultsStore | None = None,
        action_log_dir: Path | None = None,
    ) -> None:
        self.connection = connection
        self.registry = registry
        self.results_store = results_store
        self.action_log_dir = action_log_dir
        self.session: PlayerSession | None = None
        self.table: Table | None = None

    async def handle_message(self, raw: str) -> None:
        try:
            envelope = parse_envelope(raw)
            check_protocol_version(envelope)
            action = parse_action(envelope)
        except ProtocolError as exc:
            await self._send_raw("action_rejected", {"reason": str(exc)})
            return

        try:
            if isinstance(action, Identify):
                await self._handle_identify(action)
            elif isinstance(action, CreateTable):
                await self._handle_create_table(action)
            elif isinstance(action, JoinTable):
                await self._handle_join_table(action)
            elif isinstance(action, Ready):
                await self._handle_ready()
            elif isinstance(action, StartPot):
                await self._handle_start_pot()
            elif isinstance(action, ResultAck):
                await self._handle_result_ack()
            elif isinstance(action, QUEUE_ROUTED):
                await self._route_to_inbox(action)
            else:
                raise ProtocolError(f"unhandled action type: {type(action).__name__}")
        except (ProtocolError, IllegalAction) as exc:
            await self._send_raw("action_rejected", {"reason": str(exc)})

    async def _send_raw(self, type_: str, payload: dict) -> None:
        table_id = self.table.room_id if self.table else None
        await self.connection.send(build_envelope(type_, payload, table_id=table_id))

    async def _handle_identify(self, action: Identify) -> None:
        player_id = uuid.uuid4().hex
        session_token = uuid.uuid4().hex
        self.session = PlayerSession(
            player_id=player_id,
            name=action.name,
            player_type=action.player_type,
            session_token=session_token,
            connection=self.connection,
        )
        await self._send_raw("identified", {"player_id": player_id, "session_token": session_token})

    async def _handle_create_table(self, action: CreateTable) -> None:
        if self.session is None:
            raise ProtocolError("must identify before create_table")
        try:
            config = create_table_to_config(action)
        except ValueError as exc:
            # GameConfig.__post_init__ validates cross-field invariants
            # (e.g. round_limit required for that end_condition, game_count
            # must be a positive int for evaluation mode) by raising
            # ValueError -- outside the (ProtocolError, IllegalAction) net
            # that handle_message() catches, so left alone this would crash
            # the whole connection instead of just rejecting the request.
            raise ProtocolError(str(exc)) from exc
        table = Table(
            room_id="",
            config=config,
            creator_id=self.session.player_id,
            results_store=self.results_store,
            action_log_dir=self.action_log_dir,
        )
        room_id = self.registry.register(table)
        table.room_id = room_id
        await self._send_raw("table_created", {"room_id": room_id})

    async def _handle_join_table(self, action: JoinTable) -> None:
        table = self.registry.get(action.room_id)
        if table is None:
            raise ProtocolError(f"no such table: {action.room_id!r}")
        self.table = table

        # Reconnection: a session_token alone re-binds the existing session
        # to this connection, no prior `identify` on this connection needed.
        if action.session_token:
            existing = next((s for s in table.sessions.values() if s.session_token == action.session_token), None)
            if existing is None:
                raise ProtocolError("invalid session_token")
            existing.connection = self.connection
            existing.connected = True
            self.session = existing
            await self._send_raw("state_snapshot", build_state_snapshot(table, existing.player_id))
            await self._resend_outstanding_prompt(existing)
            return

        if self.session is None:
            raise ProtocolError("must identify before join_table (unless reconnecting with session_token)")
        if self.session.player_type != "spectator" and len(table.players()) >= MAX_PLAYERS_PER_TABLE:
            raise ProtocolError("table is full")
        if self.session.player_type == "spectator" and len(table.spectators()) >= MAX_SPECTATORS_PER_TABLE:
            raise ProtocolError("too many spectators")
        # Best-effort deterrent against one person quietly occupying several
        # seats to see multiple hands, and against impersonating another
        # player's on-screen label: reject a display name already held by a
        # different player at this table. This is NOT a real defense -- a
        # determined cheater just picks distinct names -- so true
        # multi-seat/collusion prevention still requires authentication and
        # organizer oversight (docs/security-notes.md). Spectators are exempt
        # (no seat, no hand, so a name clash there is harmless).
        if self.session.player_type != "spectator":
            folded = folded_name(self.session.name)
            if any(
                s.player_id != self.session.player_id and folded_name(s.name) == folded
                for s in table.players()
            ):
                raise ProtocolError("that name is already taken at this table")

        self.session.room_id = table.room_id
        table.add_session(self.session)
        await self._send_raw("state_snapshot", build_state_snapshot(table, self.session.player_id))

    async def _resend_outstanding_prompt(self, session: PlayerSession) -> None:
        # The runner's prompt envelope went to the pre-reconnect connection;
        # without a re-send the returning player has no buttons and just
        # waits out the server-side timeout (docs/human-client-guide.md
        # expects reconnection to be practical, not merely possible). The
        # runner keeps awaiting the ORIGINAL deadline -- only the remaining
        # seconds are advertised here.
        prompt = session.outstanding_prompt
        if prompt is None:
            return
        remaining = prompt["deadline"] - asyncio.get_event_loop().time()
        if remaining <= 0:
            return
        payload = dict(prompt["payload"])
        payload["timeout_sec"] = round(remaining, 1)
        await session.send(build_envelope(prompt["type"], payload, table_id=self.table.room_id if self.table else None))

    async def _handle_ready(self) -> None:
        if self.session is None or self.table is None:
            raise ProtocolError("must join_table before ready")
        if self.session.player_type == "spectator":
            raise ProtocolError("spectators cannot declare ready")
        if self.table.config.mode == "evaluation" and self.session.player_type != "ai":
            # docs/protocol/design.md 「AI専用高速評価モード」: humans never
            # play in evaluation mode. Rejecting outright (not just
            # silently ignoring) prevents two real bugs a silent ignore
            # would still allow: a human's `ready` counting toward the
            # readiness threshold while never becoming a participant can
            # either wedge the table (participants < min_players resets
            # ready_ids, discarding AIs who already readied) or let the
            # game start with fewer AIs than intended, silently.
            raise ProtocolError("only AI players can declare ready on an evaluation table")
        table = self.table
        if table.game is not None or table.evaluation_started:
            return  # already started; later pots/games auto-include everyone eligible
        table.ready_ids.add(self.session.player_id)
        if table.ready_deadline_task is None:
            table.ready_deadline_task = asyncio.create_task(_ready_timeout_watchdog(table))
        if len(table.ready_ids) >= max(table.min_players, len(_eligible_participant_ids(table))):
            table.ready_deadline_task.cancel()
            await _start_game(table)

    async def _handle_start_pot(self) -> None:
        if self.session is None or self.table is None:
            raise ProtocolError("must join_table before start_pot")
        table = self.table
        if table.effective_creator_id() != self.session.player_id:
            raise ProtocolError("only the table creator can start the pot")
        if table.config.mode != "normal":
            raise ProtocolError("start_pot is only available on normal-mode tables")
        if table.game is not None or table.evaluation_started:
            return  # already started; no-op
        # Pressing start IS the creator's participation declaration -- the
        # organizer's flow is "wait for everyone else to ready up, then
        # start", not "ready yourself like a guest and then also start".
        if self.session.player_type != "spectator":
            table.ready_ids.add(self.session.player_id)
        participants = [pid for pid in _eligible_participant_ids(table) if pid in table.ready_ids]
        if len(participants) < table.min_players:
            raise ProtocolError(f"not enough players are ready yet (need at least {table.min_players})")
        if table.ready_deadline_task is not None:
            table.ready_deadline_task.cancel()
            table.ready_deadline_task = None
        await _start_game(table)

    async def _handle_result_ack(self) -> None:
        if self.session is None or self.table is None:
            raise ProtocolError("must join_table before result_ack")
        # A late ack (after the pause already ended) is harmless: the set is
        # cleared at the start of the next pause.
        self.table.result_acks.add(self.session.player_id)

    async def _route_to_inbox(self, action) -> None:
        if self.session is None or self.table is None:
            raise ProtocolError("must join_table first")
        self.session.inbox.put_nowait(action)

    async def on_disconnect(self) -> None:
        # Only mark the session disconnected if it is still bound to THIS
        # handler's connection. On a page reload the browser's new connection
        # can complete its session_token rebind (join_table) before the old
        # connection's close is even detected -- especially through a tunnel,
        # where the close can lag by seconds. Without this identity check the
        # stale close then flips `connected` back to False on the freshly
        # rebound session, silently muting all sends to that player and
        # making the runner treat them as gone (auto no-change, auto-decline,
        # force_end once fewer than 2 players look connected).
        if self.session is not None and self.session.connection is self.connection:
            self.session.connected = False
