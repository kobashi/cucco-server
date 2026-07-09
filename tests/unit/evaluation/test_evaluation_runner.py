import json

import pytest

from cucco.domain.config import GameConfig
from cucco.evaluation.runner import EvaluationRunner
from cucco.protocol.actions import ContinueDeclare, CuccoPass, DealerReady, NoChangeDeclare
from cucco.server.session import PlayerSession
from cucco.server.table import Table


class AutoRespondConnection:
    """Answers every prompt immediately (no_change / cucco_pass / always
    continue) by pushing straight into its own session's inbox -- enough to
    drive whole games to completion without a real client loop."""

    def __init__(self):
        self.sent: list[dict] = []
        self.session: PlayerSession | None = None

    async def send(self, message: str) -> None:
        data = json.loads(message)
        self.sent.append(data)
        if data["type"] == "turn_prompt":
            self.session.inbox.put_nowait(NoChangeDeclare())
        elif data["type"] == "cucco_window":
            self.session.inbox.put_nowait(CuccoPass())
        elif data["type"] == "dealer_ready":
            self.session.inbox.put_nowait(DealerReady())
        elif data["type"] == "continue_prompt":
            self.session.inbox.put_nowait(ContinueDeclare(continue_playing=True))


def make_table(game_count: int, *, starting_chips: int = 5) -> tuple[Table, dict[str, AutoRespondConnection]]:
    config = GameConfig(
        mode="evaluation",
        game_count=game_count,
        end_condition="chips_zero",
        starting_chips=starting_chips,
        # Fast/deterministic: no real client is present to "wait for", and
        # a slow default timeout would make an already-answered prompt take
        # real wall-clock time for no reason.
        turn_timeout_ai_sec=0.05,
        cucco_window_timeout_ai_sec=0.02,
    )
    table = Table(room_id="ABC123", config=config, creator_id="A")
    conns: dict[str, AutoRespondConnection] = {}
    for pid in ("A", "B", "C"):
        conn = AutoRespondConnection()
        session = PlayerSession(player_id=pid, name=f"Player-{pid}", player_type="ai", session_token=pid, connection=conn)
        conn.session = session
        table.add_session(session)
        conns[pid] = conn
    return table, conns


@pytest.mark.asyncio
async def test_evaluation_runner_plays_game_count_games_and_sends_a_summary():
    table, conns = make_table(game_count=3)

    await EvaluationRunner(table, ["A", "B", "C"]).run()

    assert table.finished is True

    summaries = [m for m in conns["A"].sent if m["type"] == "evaluation_summary"]
    assert len(summaries) == 1
    payload = summaries[0]["payload"]
    assert payload["game_count"] == 3

    assert set(payload["players"]) == {"A", "B", "C"}
    for pid, stats in payload["players"].items():
        assert stats["name"] == f"Player-{pid}"
        assert 0.0 <= stats["win_rate"] <= 1.0
        assert 1.0 <= stats["avg_rank"] <= 3.0
        assert stats["avg_final_chips"] >= 0
        assert 0.0 <= stats["disqualification_rate"] <= 1.0

    # Win rates across all players in a fixed-participant evaluation run
    # must add up to exactly 1 game's worth of wins per game played.
    total_win_rate = sum(stats["win_rate"] for stats in payload["players"].values())
    assert total_win_rate == pytest.approx(1.0)

    # Every player was broadcast the exact same summary.
    for pid, conn in conns.items():
        their_summary = [m for m in conn.sent if m["type"] == "evaluation_summary"]
        assert len(their_summary) == 1
        assert their_summary[0]["payload"] == payload


@pytest.mark.asyncio
async def test_evaluation_runner_rotates_seats_and_resets_chips_each_game():
    table, conns = make_table(game_count=3, starting_chips=25)

    await EvaluationRunner(table, ["A", "B", "C"]).run()

    payload = next(m for m in conns["A"].sent if m["type"] == "evaluation_summary")["payload"]
    rotations = payload["seat_rotations"]
    assert [r["game_number"] for r in rotations] == [1, 2, 3]
    assert rotations[0]["seats"] == ["A", "B", "C"]
    assert rotations[1]["seats"] == ["B", "C", "A"]
    assert rotations[2]["seats"] == ["C", "A", "B"]
    for r in rotations:
        assert r["dealer_id"] in {"A", "B", "C"}

    # Every game_result / deal_result broadcast in between shows chips
    # freshly reset to starting_chips=25 (not carried over from the
    # previous game) -- pot_started's chips_now is the simplest signal.
    pot_starts = [m for m in conns["A"].sent if m["type"] == "pot_started" and m["payload"]["pot_number"] == 1]
    assert len(pot_starts) == 3  # one per game's first pot
    for m in pot_starts:
        assert set(m["payload"]["chips_now"].values()) == {24}  # 25 - 1 entry fee


@pytest.mark.asyncio
async def test_evaluation_runner_works_with_the_minimum_two_participants():
    table, conns = make_table(game_count=2)

    await EvaluationRunner(table, ["A", "B"]).run()

    payload = next(m for m in conns["A"].sent if m["type"] == "evaluation_summary")["payload"]
    assert set(payload["players"]) == {"A", "B"}
    assert payload["game_count"] == 2
