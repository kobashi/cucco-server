"""案A検証 (docs/ai-advanced-policies.md「共通: 検証の作法」): the counting
policies must measurably outperform the matrix baseline over an evaluation
run. Average rank is the assertion target -- it is far more stable at a few
hundred games than win rate (the aggressive variant also wins outright more
often than 1/n in practice; the conservative variant converts its chip
protection into rank, not wins, by design).

Runs fully in-process (embedded bots on an evaluation table, spectator
watching for the summary) -- no sockets, so ~300 games take seconds.
"""

import asyncio
import json

import pytest

from cucco.protocol.envelope import build_envelope
from cucco.server.dispatch import ConnectionHandler
from cucco.server.registry import TableRegistry

GAME_COUNT = 300


class SummarySink:
    def __init__(self):
        self.summary: dict | None = None

    async def send(self, message: str) -> None:
        data = json.loads(message)
        if data["type"] == "evaluation_summary":
            self.summary = data["payload"]


async def _evaluate(probe_policy: str) -> tuple[dict, dict[str, str]]:
    registry = TableRegistry()
    sink = SummarySink()
    handler = ConnectionHandler(sink, registry)
    await handler.handle_message(build_envelope("identify", {"name": "Watcher", "player_type": "spectator"}))
    await handler.handle_message(
        build_envelope(
            "create_table",
            {
                "mode": "evaluation",
                "game_count": GAME_COUNT,
                "starting_chips": 5,
                "ai_players": [{"policy": probe_policy, "count": 1}, {"policy": "matrix", "count": 3}],
            },
        )
    )
    room_id = next(iter(registry._tables))
    await handler.handle_message(build_envelope("join_table", {"room_id": room_id}))

    async def wait_summary():
        while sink.summary is None:
            await asyncio.sleep(0.05)

    await asyncio.wait_for(wait_summary(), timeout=120)
    names = {pid: s.name for pid, s in registry._tables[room_id].sessions.items()}
    return sink.summary, names


@pytest.mark.parametrize("probe", ["counting_aggressive", "counting_conservative"])
@pytest.mark.asyncio
async def test_counting_policy_outranks_the_matrix_baseline(probe):
    summary, names = await _evaluate(probe)
    assert summary["games_played"] == GAME_COUNT

    probe_stats = [st for pid, st in summary["players"].items() if probe[:10] in names[pid]]
    matrix_stats = [st for pid, st in summary["players"].items() if "matrix" in names[pid]]
    assert len(probe_stats) == 1 and len(matrix_stats) == 3

    matrix_avg_rank = sum(st["avg_rank"] for st in matrix_stats) / len(matrix_stats)
    # Seat rotation removes positional bias; over 300 games a real edge shows
    # up as a lower average rank than the baseline field.
    assert probe_stats[0]["avg_rank"] < matrix_avg_rank
