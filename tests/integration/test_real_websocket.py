"""Smoke test over an actual WebSocket connection (not FakeConnection),
to verify cucco.server.app's real networking glue works end to end."""

import asyncio
import json

import pytest
import websockets

from cucco.protocol.envelope import build_envelope
from cucco.server.app import handle_connection
from cucco.server.registry import TableRegistry


@pytest.mark.asyncio
async def test_identify_and_create_table_over_a_real_websocket():
    registry = TableRegistry()

    async def handler(websocket):
        await handle_connection(websocket, registry)

    async with websockets.serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://localhost:{port}") as client:
            await client.send(build_envelope("identify", {"name": "Alice", "player_type": "human"}))
            reply = json.loads(await asyncio.wait_for(client.recv(), timeout=5))
            assert reply["type"] == "identified"
            assert "player_id" in reply["payload"]

            await client.send(build_envelope("create_table", {"starting_chips": 25}))
            reply2 = json.loads(await asyncio.wait_for(client.recv(), timeout=5))
            assert reply2["type"] == "table_created"
            assert len(reply2["payload"]["room_id"]) == 6
