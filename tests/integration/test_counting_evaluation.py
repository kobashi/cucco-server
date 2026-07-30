"""案A検証 (docs/ai-advanced-policies.md「共通: 検証の作法」): the counting
policies must outrank the matrix baseline over an evaluation run.

Average rank is the assertion target, NOT win rate: measured over 10 seeds of
400 games, both counting policies sit at or below the 0.250 chance win rate of
a 4-player table (aggressive 0.249, conservative 0.226) while still ranking
better. Cucco rewards not losing chips, and that shows up in rank -- the
conservative variant's much lower disqualification rate (0.42 vs matrix 0.54)
is where its rank edge comes from.

**This file measures the single hardest configuration**: 4 players against an
all-matrix field. The same policies look far stronger elsewhere -- at 8 players
the aggressive edge is +0.27 rather than +0.06, and against an always_change
field it is +1.23. So a failure here means "lost ground in the strictest case",
not "the policy is weak"; the table of configurations lives in the 検証の作法
section of the doc above. Deliberately kept strict so it catches regressions.

Runs fully in-process (embedded bots on an evaluation table, spectator
watching for the summary) -- no sockets, so a 400-game run takes seconds.
"""

import asyncio
import functools
import itertools
import json

import pytest

from cucco.evaluation.runner import EvaluationRunner
from cucco.protocol.envelope import build_envelope
from cucco.server.dispatch import ConnectionHandler
from cucco.server.registry import TableRegistry

GAME_COUNT = 400

# Fixed so the run is reproducible. Any seed works -- this one is just the date
# the test was pinned; see `deterministic_seeds` for why it is pinned at all.
BASE_SEED = 20260729


@pytest.fixture
def deterministic_seeds(monkeypatch):
    """Make the evaluation run reproducible.

    Production draws each game's seed from SystemRandom, which is what made
    this test flaky: the rank edge is real but modest (measured over 10 runs of
    400 games: mean 0.09, sd 0.08), so roughly 1 run in 5 landed below zero and
    even three attempts failed outright about once in a hundred runs.

    Only the *source* of the seeds is replaced -- the deck, the dealer draw and
    the policies all run exactly as shipped, so the number this test asserts on
    is a real measurement of the policy, just always the same one.
    """
    seeds = itertools.count(BASE_SEED)
    monkeypatch.setattr(
        "cucco.server.dispatch.EvaluationRunner",
        functools.partial(EvaluationRunner, seed_source=lambda: next(seeds)),
    )


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


def _rank_edge(summary: dict, names: dict[str, str], probe: str) -> float:
    probe_stats = [st for pid, st in summary["players"].items() if probe[:10] in names[pid]]
    matrix_stats = [st for pid, st in summary["players"].items() if "matrix" in names[pid]]
    assert len(probe_stats) == 1 and len(matrix_stats) == 3
    matrix_avg_rank = sum(st["avg_rank"] for st in matrix_stats) / len(matrix_stats)
    return matrix_avg_rank - probe_stats[0]["avg_rank"]


# How many consecutive seeds (from BASE_SEED) each probe is averaged over.
#
# Measured 2026-07-29 over 10 seeds of 400 games:
#
# | policy                | mean edge | min     | max     | negative runs |
# |-----------------------|-----------|---------|---------|---------------|
# | counting_aggressive   |   +0.026  | -0.067  | +0.153  |     4 / 10    |
# | counting_conservative |   +0.327  | +0.233  | +0.437  |     0 / 10    |
#
# So one run is plenty for the conservative variant, while the aggressive one is
# negative about 40% of the time in THIS configuration -- averaging several runs
# is what makes its assertion mean anything. Pinning one lucky seed would have
# gone green while hiding exactly that.
#
# Two candidate explanations for the thin aggressive edge were measured and
# ruled out as the cause:
#   - Disqualified-card disclosure timing: re-measuring the same 10 seeds with
#     `immediate` instead of the default `deferred` moved the mean from +0.026
#     to +0.029.
#   - The counting layer itself being weak: restoring counting_conservative's
#     parameters one at a time gives continue-decision-only +0.32, danger-weight
#     -only +0.04, cutoff-only +0.06. In this configuration the 案D continue
#     decision carries nearly the whole conservative edge.
# What the edge IS sensitive to is the configuration -- see the module docstring.
RUNS_BY_PROBE = {"counting_aggressive": 5, "counting_conservative": 1}


@pytest.mark.parametrize("probe", sorted(RUNS_BY_PROBE))
@pytest.mark.asyncio
async def test_counting_policy_outranks_the_matrix_baseline(probe, deterministic_seeds):
    # Deterministic: fixed seeds, so this either passes or fails the same way
    # every time. It used to retry unseeded runs up to 3 times, which still
    # failed outright roughly once in 16 runs.
    runs = RUNS_BY_PROBE[probe]
    edges = []
    for _ in range(runs):
        summary, names = await _evaluate(probe)
        assert summary["games_played"] == GAME_COUNT
        edges.append(_rank_edge(summary, names, probe))

    mean_edge = sum(edges) / len(edges)
    assert mean_edge > 0, (
        f"{probe} did not outrank the matrix field over {runs} run(s) of "
        f"{GAME_COUNT} games: mean edge {mean_edge:+.4f}, per-run {[f'{e:+.4f}' for e in edges]}"
    )
