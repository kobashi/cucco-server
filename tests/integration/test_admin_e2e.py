"""End-to-end admin surface: real sockets, a genuinely stuck table.

A bot-only normal table rematches forever (the documented stage-1 caveat);
the operator's abort over the real admin listener is what puts it down.
"""

import asyncio
import json

import pytest
import websockets

from cucco.server.admin import serve_admin
from cucco.server.app import handle_connection
from cucco.server.registry import TableRegistry

TOKEN = "e2e-admin-token"


@pytest.mark.asyncio
async def test_admin_abort_over_a_real_socket():
    registry = TableRegistry()

    async def game_handler(websocket):
        await handle_connection(websocket, registry)

    game_server = await websockets.serve(game_handler, "localhost", 0)
    admin_server = await serve_admin(registry, host="127.0.0.1", port=0, token=TOKEN)
    try:
        game_port = game_server.sockets[0].getsockname()[1]
        admin_port = admin_server.sockets[0].getsockname()[1]

        # A spectator creates a bot-only table; the bots start and rematch
        # endlessly on their own.
        async with websockets.connect(f"ws://localhost:{game_port}") as ws:
            async def send(t, p):
                await ws.send(json.dumps({"type": t, "table_id": None, "protocol_version": "1.0", "payload": p, "ts": ""}))

            await send("identify", {"name": "Watcher", "player_type": "spectator"})
            await ws.recv()
            await send("create_table", {"starting_chips": 3, "ai_players": [{"policy": "always_change", "count": 2}]})
            room_id = json.loads(await ws.recv())["payload"]["room_id"]
            await send("join_table", {"room_id": room_id})

            table = registry.get(room_id)

            async def wait_running():
                while table.game is None:
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_running(), timeout=10)

            async with websockets.connect(f"ws://127.0.0.1:{admin_port}") as admin:
                # Wrong token first: rejected.
                await admin.send(json.dumps({"token": "nope", "action": "list_tables"}))
                assert json.loads(await admin.recv())["ok"] is False

                await admin.send(json.dumps({"token": TOKEN, "action": "list_tables"}))
                listing = json.loads(await admin.recv())
                assert listing["ok"] and listing["tables"][0]["room_id"] == room_id

                await admin.send(json.dumps({"token": TOKEN, "action": "abort_table", "room_id": room_id}))
                aborted = json.loads(await admin.recv())
                assert aborted["ok"] and aborted["aborted"] == room_id

            # The spectator's socket saw a regular game_ended; the room is gone.
            async def spectator_saw_game_ended():
                while True:
                    ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if ev["type"] == "game_ended":
                        return
            await asyncio.wait_for(spectator_saw_game_ended(), timeout=5)
            assert registry.get(room_id) is None
    finally:
        game_server.close()
        admin_server.close()
        await game_server.wait_closed()
        await admin_server.wait_closed()
