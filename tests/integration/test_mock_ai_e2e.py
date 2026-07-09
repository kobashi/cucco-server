"""End-to-end: real websockets server + real MockAI clients over real sockets.

The final verification step of the build plan -- the same client code a
seminar student would run (clients/mock_ai) driving full games against the
actual server stack, in both normal and evaluation modes.
"""

import asyncio

import pytest
import websockets

from clients.common.ws_client import CuccoConnection
from clients.mock_ai.mock_ai import MockAI
from clients.mock_ai.policies import make_policy
from cucco.server.app import handle_connection
from cucco.server.registry import TableRegistry

FAST_TIMEOUTS = {"turn_timeout_ai_sec": 1.0, "cucco_window_timeout_ai_sec": 0.5}


async def _serve():
    registry = TableRegistry()

    async def handler(websocket):
        await handle_connection(websocket, registry)

    return await websockets.serve(handler, "localhost", 0)


async def _run_ai(url: str, name: str, policy_name: str, room_id: str | None, config: dict, mode: str):
    async with CuccoConnection(url) as conn:
        await conn.identify(name, "ai")
        if room_id is None:
            room_id = await conn.create_table(config)
        await conn.join_table(room_id)
        result = await MockAI(conn, make_policy(policy_name), mode=mode).play()
        return room_id, result


@pytest.mark.asyncio
async def test_three_mock_ais_play_a_normal_game_to_completion():
    server = await _serve()
    try:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://localhost:{port}"
        config = {"mode": "normal", "end_condition": "chips_zero", "starting_chips": 5, **FAST_TIMEOUTS}

        # The creator connects first to get a room_id the others can join.
        creator_conn = CuccoConnection(url)
        async with creator_conn:
            await creator_conn.identify("Matrix", "ai")
            room_id = await creator_conn.create_table(config)
            await creator_conn.join_table(room_id)
            creator = MockAI(creator_conn, make_policy("matrix"), mode="normal")

            results = await asyncio.wait_for(
                asyncio.gather(
                    creator.play(),
                    _run_ai(url, "Changer", "always_change", room_id, config, "normal"),
                    _run_ai(url, "Keeper", "always_no_change", room_id, config, "normal"),
                ),
                timeout=30.0,
            )

        game_ended = results[0]
        assert len(game_ended["ranking"]) == 3
        total_chips = sum(chips for _, chips in game_ended["ranking"])
        assert total_chips <= 15  # 3 players x 5 chips, minus any unclaimed carryover
        # Every client saw the same final ranking.
        for _, other in results[1:]:
            assert other["ranking"] == game_ended["ranking"]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_three_mock_ais_complete_an_evaluation_run():
    server = await _serve()
    try:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://localhost:{port}"
        config = {
            "mode": "evaluation",
            "game_count": 3,
            "end_condition": "chips_zero",
            "starting_chips": 5,
            **FAST_TIMEOUTS,
        }

        creator_conn = CuccoConnection(url)
        async with creator_conn:
            await creator_conn.identify("Matrix", "ai")
            room_id = await creator_conn.create_table(config)
            await creator_conn.join_table(room_id)
            creator = MockAI(creator_conn, make_policy("matrix"), mode="evaluation")

            results = await asyncio.wait_for(
                asyncio.gather(
                    creator.play(),
                    _run_ai(url, "Changer", "always_change", room_id, config, "evaluation"),
                    _run_ai(url, "Keeper", "always_no_change", room_id, config, "evaluation"),
                ),
                timeout=60.0,
            )

        summary = results[0]
        assert summary["game_count"] == 3
        assert summary["games_played"] == 3
        assert len(summary["players"]) == 3
        assert len(summary["seat_rotations"]) == 3
        win_total = sum(s["win_rate"] for s in summary["players"].values())
        assert win_total == pytest.approx(1.0)
        for _, other in results[1:]:
            assert other == summary
    finally:
        server.close()
        await server.wait_closed()
