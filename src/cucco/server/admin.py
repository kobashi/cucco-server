"""Operator admin surface (AIロードマップ第4段階).

A SECOND WebSocket listener, separate from the public game endpoint, that
lets the operator inspect running tables and put stuck ones out of their
misery. Security model (docs/security-notes.md):

- Binds to 127.0.0.1 only by default -- reachable from the server machine,
  never through the Cloudflare tunnel that exposes the game port.
- Every request must carry the admin token (generated at startup and
  printed to the log unless supplied via --admin-token). The token is a
  second line of defense, not a reason to ever expose this port.

Wire format is deliberately NOT the game envelope -- plain request/response
JSON, one reply per request:

    {"token": "...", "action": "list_tables"}
    {"token": "...", "action": "table_status", "room_id": "AB12CD"}
    {"token": "...", "action": "abort_table",  "room_id": "AB12CD"}
    {"token": "...", "action": "remove_table", "room_id": "AB12CD"}

Replies: {"ok": true, ...} or {"ok": false, "error": "..."}.

`abort_table` force-ends a running game (players receive a regular
`game_ended` with the standings by current chips), cancels the runner and
any embedded-bot tasks, and unregisters the room -- this is the cleanup for
the documented "a bot-only table never stops on its own" case.
`remove_table` sweeps a table with no running game (lobby leftovers,
finished rooms nobody plays in).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path

from cucco.protocol.envelope import build_envelope
from cucco.protocol.wire_events import translate
from cucco.server.registry import TableRegistry
from cucco.server.runner import build_state_snapshot
from cucco.server.table import Table

logger = logging.getLogger("cucco.server.admin")


async def handle_admin_request(registry: TableRegistry, token: str, request: dict) -> dict:
    """One admin request -> one reply dict. Transport-independent so tests
    can drive it without sockets."""
    if not isinstance(request, dict) or request.get("token") != token:
        return {"ok": False, "error": "invalid admin token"}
    action = request.get("action")
    if action == "list_tables":
        return {"ok": True, "tables": [_table_summary(t) for t in _tables(registry)]}

    room_id = request.get("room_id")
    table = registry.get(room_id) if isinstance(room_id, str) else None
    if not isinstance(table, Table):
        return {"ok": False, "error": f"no such table: {room_id!r}"}

    if action == "table_status":
        return {
            "ok": True,
            "table": _table_summary(table),
            "snapshot": build_state_snapshot(table, None),  # spectator view: no hands
            "sessions": [
                {
                    "player_id": s.player_id,
                    "name": s.name,
                    "player_type": s.player_type,
                    "ai_policy": s.ai_policy,
                    "connected": s.connected,
                }
                for s in table.sessions.values()
            ],
        }
    if action == "abort_table":
        return await abort_table(registry, table)
    if action == "remove_table":
        if table.game is not None and not table.game.is_finished:
            return {"ok": False, "error": "a game is still running; use abort_table"}
        await _shutdown_table(registry, table, notify_reason="この卓は管理者によって閉じられました")
        return {"ok": True, "removed": table.room_id}
    return {"ok": False, "error": f"unknown admin action: {action!r}"}


def _tables(registry: TableRegistry) -> list[Table]:
    return [t for t in registry._tables.values() if isinstance(t, Table)]


def _table_summary(table: Table) -> dict:
    game = table.game
    bots = [s for s in table.players() if s.ai_policy is not None]
    humans = [s for s in table.players() if s.ai_policy is None]
    return {
        "room_id": table.room_id,
        "mode": table.config.mode,
        "players": len(table.players()),
        "humans_connected": sum(1 for s in humans if s.connected),
        "bots": len(bots),
        "spectators": len(table.spectators()),
        "game_active": game is not None and not game.is_finished,
        "evaluation_started": table.evaluation_started,
        "finished": table.finished,
        "pot_number": game.pot_number if game is not None else 0,
        "created_at": table.created_at,
        "idle_sec": round(time.time() - table.last_activity_at, 1),
        # Live check -- real_absent_sec alone is ambiguous, because the GC only
        # stamps real_absent_since on its (60s) sweep: a table everyone just
        # left reads as None until the next sweep, which must not look like
        # "someone is here".
        "real_connected": real_participant_connected(table),
        "real_absent_sec": round(time.time() - table.real_absent_since, 1) if table.real_absent_since is not None else None,
    }


# -- automatic garbage collection ------------------------------------------------
#
# Generous defaults, all well above runner.py's RECONNECT_GRACE_SEC, so a
# brief network blip or a page reload never loses a live table.
GC_INTERVAL_SEC = 60.0
GC_EMPTY_GRACE_SEC = 600.0  # no connected real client -> remove after 10 min
GC_FINISHED_GRACE_SEC = 300.0  # crashed/finished + idle -> remove after 5 min


def real_participant_connected(table: Table) -> bool:
    """Is anyone real (human / spectator / external-AI client) still on this
    table? Embedded bots (ai_policy set) don't count -- a table where only
    bots are connected has nobody left to serve. External AI clients carry no
    policy tag, so a legitimate AI-vs-AI run over real sockets still reads as
    occupied and is never swept."""
    return any(s.connected and s.ai_policy is None for s in table.sessions.values())


def sweep_gc(
    registry: TableRegistry,
    *,
    now: float,
    empty_grace_sec: float = GC_EMPTY_GRACE_SEC,
    finished_grace_sec: float = GC_FINISHED_GRACE_SEC,
) -> list[Table]:
    """Return the tables due for garbage collection, updating each table's
    real_absent_since bookkeeping as it goes. Free of I/O so the decision is
    unit-testable with injected timestamps; the loop below does the removing.

    Two triggers:
    - a crashed/finished room left idle past finished_grace_sec, and
    - a room with no connected real participant past empty_grace_sec
      (abandoned lobby, a bot-only table that kept rematching after its
      watcher left, or a table created but never joined).
    """
    doomed: list[Table] = []
    for table in _tables(registry):
        if table.finished and now - table.last_activity_at >= finished_grace_sec:
            doomed.append(table)
            continue
        if real_participant_connected(table):
            table.real_absent_since = None
            continue
        if table.real_absent_since is None:
            table.real_absent_since = now  # start the empty timer
        elif now - table.real_absent_since >= empty_grace_sec:
            doomed.append(table)
    return doomed


async def run_gc_loop(
    registry: TableRegistry,
    *,
    interval_sec: float = GC_INTERVAL_SEC,
    empty_grace_sec: float = GC_EMPTY_GRACE_SEC,
    finished_grace_sec: float = GC_FINISHED_GRACE_SEC,
) -> None:
    """Periodically sweep abandoned/idle tables for the life of the server."""
    while True:
        await asyncio.sleep(interval_sec)
        try:
            for table in sweep_gc(
                registry, now=time.time(), empty_grace_sec=empty_grace_sec, finished_grace_sec=finished_grace_sec
            ):
                await _shutdown_table(registry, table, notify_reason=None)
                logger.info("GC removed abandoned table %s", table.room_id)
        except Exception:
            logger.exception("table GC sweep failed")


async def abort_table(registry: TableRegistry, table: Table) -> dict:
    """Force-end whatever is running, tell the players, and unregister."""
    game = table.game
    ranking = None
    if game is not None and not game.is_finished:
        # Domain force_end computes the proper standings (current chips) and
        # produces the same GameEnded event a natural ending would.
        await _cancel(table.runner_task)
        events = game.force_end()
        for event in events:
            wire = translate(event)
            if wire is None:
                continue
            for session in list(table.sessions.values()):
                with contextlib.suppress(Exception):
                    await session.send(build_envelope(wire.type, wire.for_recipient(session.player_id), table_id=table.room_id))
        ranking = [list(pair) for pair in (game.final_ranking or ())]
    await _shutdown_table(registry, table, notify_reason=None if ranking is not None else "この卓は管理者によって閉じられました")
    logger.info("admin aborted table %s", table.room_id)
    return {"ok": True, "aborted": table.room_id, "ranking": ranking}


async def _shutdown_table(registry: TableRegistry, table: Table, *, notify_reason: str | None) -> None:
    await _cancel(table.runner_task)
    for task in table.bot_tasks:
        await _cancel(task)
    if table.ready_deadline_task is not None:
        table.ready_deadline_task.cancel()
        table.ready_deadline_task = None
    if notify_reason is not None:
        for session in list(table.sessions.values()):
            with contextlib.suppress(Exception):
                await session.send(build_envelope("action_rejected", {"reason": notify_reason}, table_id=table.room_id))
    table.finished = True
    registry.remove(table.room_id)


async def _cancel(task: asyncio.Task | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


# -- browser console -------------------------------------------------------------
#
# The same loopback listener also serves a small HTML console over plain HTTP,
# so the operator can manage tables from a browser instead of the CLI. It lives
# here (NOT under clients/) on purpose: clients/ is published to GitHub Pages by
# CI, and an admin console has no business being on the public web. The token is
# deliberately NOT embedded in the page -- it stays a real gate even if this
# port is ever reachable from somewhere it shouldn't be.
CONSOLE_PATHS = ("/", "/index.html", "/console")
_CONSOLE_FILE = Path(__file__).with_name("admin_console.html")


def console_html() -> bytes:
    """Read the console page. Read per request (not cached) so editing the
    file during a session takes effect on reload."""
    return _CONSOLE_FILE.read_bytes()


def handle_console_request(connection, request):
    """`process_request` hook: answer plain HTTP with the console page and let
    WebSocket handshakes fall through to the admin protocol (return None)."""
    from websockets.datastructures import Headers
    from websockets.http11 import Response

    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None  # not for us: continue the WebSocket handshake

    def _response(status: int, reason: str, body: bytes, content_type: str) -> "Response":
        return Response(
            status,
            reason,
            Headers(
                {
                    "Content-Type": content_type,
                    "Content-Length": str(len(body)),
                    # Never let a proxy/browser hold on to an admin surface.
                    "Cache-Control": "no-store",
                }
            ),
            body,
        )

    path = request.path.split("?", 1)[0]
    if path in CONSOLE_PATHS:
        try:
            return _response(200, "OK", console_html(), "text/html; charset=utf-8")
        except OSError:
            logger.exception("admin console page is missing or unreadable")
            return _response(500, "Internal Server Error", b"console unavailable\n", "text/plain; charset=utf-8")
    return _response(404, "Not Found", b"not found\n", "text/plain; charset=utf-8")


async def serve_admin(registry: TableRegistry, *, host: str = "127.0.0.1", port: int = 8766, token: str):
    """Start the admin listener (WebSocket protocol + HTML console over HTTP).
    Returns the websockets server object."""
    import websockets  # local import: keep the module usable without sockets in tests

    async def _handler(websocket):
        async for raw in websocket:
            try:
                request = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"ok": False, "error": "invalid JSON"}))
                continue
            try:
                reply = await handle_admin_request(registry, token, request)
            except Exception:
                logger.exception("admin request failed")
                reply = {"ok": False, "error": "internal error (see server log)"}
            await websocket.send(json.dumps(reply, ensure_ascii=False))

    server = await websockets.serve(_handler, host, port, process_request=handle_console_request)
    logger.info(
        "admin console on http://%s:%d (local only -- do NOT tunnel this port)", host, port
    )
    return server
