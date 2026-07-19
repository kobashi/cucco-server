"""Server-embedded AI players ("内蔵ボット").

A bot is a normal client that happens to live in this process: it speaks
the full wire protocol (identify -> join_table -> ready -> declarations)
through its own `ConnectionHandler`, over an in-memory loopback instead of
a WebSocket. Nothing downstream can tell the difference -- name validation,
seat limits, inboxes, timeouts, and persistence all apply exactly as they
do to an external AI client, which is the point: human-facing protocol
behavior gets exercised, not bypassed.

Spawned by dispatch when the first real session joins a table created with
`ai_players` (spawning at create_table time would let the bots' immediate
`ready` auto-start the game before the creator has even joined -- the
all-ready auto-start counts only JOINED players).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from datetime import datetime, timezone

from cucco.ai.bot import BotEvent, MockAI
from cucco.ai.policies import make_policy
from cucco.protocol.actions import MAX_NAME_LENGTH, folded_name
from cucco.protocol.envelope import PROTOCOL_VERSION

logger = logging.getLogger("cucco.server.bots")


class _LoopbackConnection:
    """Both halves of an in-process client.

    To the server (`ConnectionHandler`) it is a `Connection`: `send()`
    delivers server->client messages, which land in a queue as parsed
    `BotEvent`s. To the brain (`cucco.ai.bot.MockAI`) it mirrors the surface
    of `clients.common.ws_client.CuccoConnection`: `send(type, payload)`,
    `events()`, `player_id` -- except "sending" means handing the envelope
    straight to this bot's own ConnectionHandler.
    """

    def __init__(self) -> None:
        self.handler = None  # set right after ConnectionHandler creation
        self.player_id: str | None = None
        self.room_id: str | None = None
        self._queue: asyncio.Queue[BotEvent] = asyncio.Queue()

    # -- server side (Connection protocol) ----------------------------------
    async def send(self, message: str) -> None:
        data = json.loads(message)
        self._queue.put_nowait(BotEvent(type=data["type"], payload=data.get("payload", {}), table_id=data.get("table_id")))

    # -- client side (what the brain calls) ---------------------------------
    async def send_action(self, type_: str, payload: dict | None = None) -> None:
        raw = json.dumps(
            {
                "type": type_,
                "table_id": self.room_id,
                "protocol_version": PROTOCOL_VERSION,
                "payload": payload or {},
                "ts": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )
        await self.handler.handle_message(raw)

    async def events(self):
        while True:
            yield await self._queue.get()

    async def expect(self, type_: str) -> BotEvent:
        event = await self._queue.get()
        if event.type == "action_rejected":
            raise RuntimeError(f"server rejected the bot's request: {event.payload.get('reason')}")
        if event.type != type_:
            raise RuntimeError(f"bot expected {type_!r}, got {event.type!r}")
        return event


class _BrainConnection:
    """The exact triplet the brain needs (send/events/player_id), bound to a
    loopback. Kept separate so `MockAI` sees the same call shape as with the
    external `CuccoConnection` (`send(type, payload)`)."""

    def __init__(self, loopback: _LoopbackConnection) -> None:
        self._loopback = loopback

    @property
    def player_id(self) -> str | None:
        return self._loopback.player_id

    async def send(self, type_: str, payload: dict | None = None) -> None:
        await self._loopback.send_action(type_, payload)

    def events(self):
        return self._loopback.events()


def bot_names(table, specs: list[tuple[str, int]]) -> list[tuple[str, str]]:
    """(name, policy) pairs for the bots to spawn: "AI-<policy>-<n>", with
    the numeric suffix bumped past any folded-name collision with players
    already seated (a human may legitimately be called "AI-matrix-1")."""
    taken = {folded_name(s.name) for s in table.players()}
    out: list[tuple[str, str]] = []
    for policy, count in specs:
        numbers = itertools.count(1)
        for _ in range(count):
            while True:
                suffix = f"-{next(numbers)}"
                # Long policy names (e.g. counting_conservative) must still
                # fit the protocol's display-name cap with prefix + suffix.
                stem = f"AI-{policy}"[: MAX_NAME_LENGTH - len(suffix)]
                name = stem + suffix
                if folded_name(name) not in taken:
                    break
            taken.add(folded_name(name))
            out.append((name, policy))
    return out


async def spawn_bot(make_handler, room_id: str, name: str, policy_name: str, mode: str) -> asyncio.Task:
    """Join one embedded bot to `room_id` and start its brain task.

    `make_handler` is a callable(connection) -> ConnectionHandler, supplied
    by dispatch so bots get the same registry/persistence wiring as real
    connections. The identify/join handshake is awaited here (so the caller
    knows the seat is taken before it returns); the ready/play loop runs as
    a background task.
    """
    loopback = _LoopbackConnection()
    loopback.handler = make_handler(loopback)
    await loopback.send_action("identify", {"name": name, "player_type": "ai"})
    identified = await loopback.expect("identified")
    loopback.player_id = identified.payload["player_id"]
    await loopback.send_action("join_table", {"room_id": room_id})
    await loopback.expect("state_snapshot")
    loopback.room_id = room_id

    task = asyncio.create_task(_run_brain(loopback, name, policy_name, mode))
    return task


async def _run_brain(loopback: _LoopbackConnection, name: str, policy_name: str, mode: str) -> None:
    """ready -> play to game end; in normal mode, re-ready for the next game
    so the bot keeps the room usable for rematches (連戦), same as a polite
    human guest. A fresh MockAI per game keeps per-deal state clean."""
    conn = _BrainConnection(loopback)
    try:
        while True:
            ai = MockAI(conn, make_policy(policy_name), mode=mode, log=None)
            await ai.play()
            if mode == "evaluation":
                return  # one evaluation run is the whole lifetime
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("embedded bot %s (policy=%s) crashed", name, policy_name)
        if loopback.handler is not None:
            await loopback.handler.on_disconnect()
