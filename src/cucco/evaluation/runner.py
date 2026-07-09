"""AI専用高速評価モード (docs/protocol/design.md 「AI専用高速評価モード」).

Runs `config.game_count` games back-to-back under one table, rotating seat
order between games so a fixed seating position can't bias the results,
then reports aggregate per-player results via `evaluation_summary`.

Stays out of the domain layer's vocabulary entirely: `Game`/`Pot`/`Deal`
know nothing about "evaluation mode" existing. This module just calls
`TableRunner` once per game, exactly as normal mode does for its one game,
and observes the same domain events every other consumer sees.
"""

from __future__ import annotations

import logging
import random

from cucco.domain.events import PlayerDisqualified, PotStarted
from cucco.domain.game import Game
from cucco.persistence.action_log import ActionLogWriter, open_for_game
from cucco.protocol.envelope import build_envelope
from cucco.server.runner import TableRunner
from cucco.server.table import Table

logger = logging.getLogger("cucco.evaluation.runner")

# Below this many still-connected participants, a game can't be dealt at
# all (TableRunner._run_pot's own liveness check would force_end it
# instantly, tying everyone at starting_chips-1 -- see run()'s pre-check).
MIN_CONNECTED_TO_CONTINUE = 2


class _StatsCollectingLog:
    """Duck-types `ActionLogWriter` (write_seed/write_action/write_event/
    close/path) so it can be handed straight to `TableRunner` as its
    `action_log` -- forwards every call to the real writer, if this table
    has one configured, while also tallying this one game's contribution
    to the evaluation summary (who got mid-deal disqualified, who dealt
    first)."""

    def __init__(self, inner: ActionLogWriter | None) -> None:
        self._inner = inner
        self.path = inner.path if inner is not None else None
        self.disqualified_players: set[str] = set()
        self.first_dealer_id: str | None = None

    def write_seed(self, seed: int) -> None:
        if self._inner is not None:
            self._inner.write_seed(seed)

    def write_action(self, player_id: str, action_type: str, payload: dict | None = None) -> None:
        if self._inner is not None:
            self._inner.write_action(player_id, action_type, payload)

    def write_event(self, event: object) -> None:
        if isinstance(event, PlayerDisqualified):
            self.disqualified_players.add(event.player_id)
        elif isinstance(event, PotStarted) and self.first_dealer_id is None:
            self.first_dealer_id = event.dealer_id
        if self._inner is not None:
            self._inner.write_event(event)

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()


class EvaluationRunner:
    def __init__(self, table: Table, participants: list[str]) -> None:
        self.table = table
        self.participants = list(participants)

    async def run(self) -> None:
        config = self.table.config
        game_count = config.game_count
        assert game_count is not None  # enforced by GameConfig.__post_init__

        totals = {pid: _PlayerTotals() for pid in self.participants}
        seat_rotations = []
        seats = list(self.participants)
        games_played = 0

        for game_number in range(1, game_count + 1):
            if game_number > 1:
                # Rotate, don't reshuffle: every player visits every seat
                # position across the run, in a predictable, reportable
                # order (docs/protocol/design.md: 座席位置がCuccoの勝率に
                # 大きく影響するため、固定座席のままだと評価結果に座席バイ
                # アスがかかる). The first dealer per game is separately
                # randomized -- Game._start_new_pot already draws it from
                # this game's own freshly-seeded rng.
                seats = seats[1:] + seats[:1]

            if self._connected_count() < MIN_CONNECTED_TO_CONTINUE:
                # Not enough live AI connections left to actually play --
                # stop here instead of burning through the remaining games
                # via TableRunner._run_pot's own force_end() liveness check,
                # which would tie every seat at starting_chips-1 and quietly
                # poison the aggregate stats with fabricated wins/ranks.
                break

            seed = random.SystemRandom().randrange(2**63)
            game = Game(seats, config, random.Random(seed))
            self.table.game = game

            real_log = open_for_game(self.table.action_log_dir, self.table.room_id) if self.table.action_log_dir else None
            if real_log is not None:
                real_log.write_seed(seed)
            stats_log = _StatsCollectingLog(real_log)

            await TableRunner(self.table, action_log=stats_log, results_store=self.table.results_store).run()
            games_played += 1

            assert game.final_ranking is not None
            for rank, (pid, chips) in enumerate(game.final_ranking, start=1):
                if pid in totals:
                    totals[pid].add_game(rank=rank, chips=chips, disqualified=pid in stats_log.disqualified_players)

            seat_rotations.append(
                {"game_number": game_number, "seats": list(seats), "dealer_id": stats_log.first_dealer_id}
            )

        payload = {
            "game_count": game_count,
            "games_played": games_played,
            "players": {
                pid: totals[pid].summarize(self.table.get(pid), games_played) for pid in self.participants
            }
            if games_played > 0
            else {},
            "seat_rotations": seat_rotations,
        }
        if self.table.results_store is not None:
            try:
                self.table.results_store.record_evaluation_summary(
                    table_id=self.table.room_id, game_count=game_count, games_played=games_played, summary=payload
                )
            except Exception:
                logger.exception("failed to record evaluation summary for table %s", self.table.room_id)

        envelope = build_envelope("evaluation_summary", payload, table_id=self.table.room_id)
        for session in list(self.table.sessions.values()):
            try:
                await session.send(envelope)
            except Exception:
                logger.exception("failed to send evaluation_summary to session %s", session.player_id)

        self.table.finished = True

    def _connected_count(self) -> int:
        return sum(1 for pid in self.participants if (s := self.table.get(pid)) is not None and s.connected)


class _PlayerTotals:
    def __init__(self) -> None:
        self.wins = 0
        self.rank_sum = 0
        self.chips_sum = 0
        self.disqualified_games = 0

    def add_game(self, *, rank: int, chips: int, disqualified: bool) -> None:
        self.rank_sum += rank
        self.chips_sum += chips
        if rank == 1:
            self.wins += 1
        if disqualified:
            self.disqualified_games += 1

    def summarize(self, session, games_played: int) -> dict:
        return {
            "name": session.name if session is not None else None,
            "win_rate": self.wins / games_played,
            "avg_rank": self.rank_sum / games_played,
            "avg_final_chips": self.chips_sum / games_played,
            # Fraction of games in which this player was disqualified via a
            # special card (道化/人間/猫) at least once -- not a per-deal
            # rate, since not every game has the same number of deals.
            "disqualification_rate": self.disqualified_games / games_played,
        }
