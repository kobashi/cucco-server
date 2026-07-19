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
    }


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


async def serve_admin(registry: TableRegistry, *, host: str = "127.0.0.1", port: int = 8766, token: str):
    """Start the admin listener. Returns the websockets server object."""
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

    server = await websockets.serve(_handler, host, port)
    logger.info("admin listener on ws://%s:%d (local only -- do NOT tunnel this port)", host, port)
    return server
