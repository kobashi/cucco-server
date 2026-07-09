"""Results store: records one row per completed game at `game_ended`
(docs/protocol/design.md 「永続化・成績記録」). Game-in-progress state lives
entirely in memory -- this is the only thing that outlives the process.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cucco.domain.timeutil import now_iso
from cucco.persistence.db import connect


@dataclass(frozen=True)
class PlayerInfo:
    player_id: str
    name: str
    player_type: str


class ResultsStore:
    def __init__(self, db_path: Path) -> None:
        self._conn = connect(db_path)

    def record_game_ended(
        self,
        *,
        table_id: str,
        mode: str,
        players: list[PlayerInfo],
        ranking: tuple[tuple[str, int], ...],
        action_log_path: str | None,
    ) -> None:
        by_id = {p.player_id: p for p in players}
        cur = self._conn.execute(
            "INSERT INTO games (table_id, mode, ended_at, action_log_path) VALUES (?, ?, ?, ?)",
            (table_id, mode, now_iso(), action_log_path),
        )
        game_id = cur.lastrowid
        for rank, (player_id, chips) in enumerate(ranking, start=1):
            info = by_id.get(player_id)
            self._conn.execute(
                "INSERT INTO participants (game_id, player_id, name, player_type, final_rank, final_chips) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    game_id,
                    player_id,
                    info.name if info is not None else player_id,
                    info.player_type if info is not None else "unknown",
                    rank,
                    chips,
                ),
            )
        self._conn.commit()

    def record_evaluation_summary(
        self, *, table_id: str, game_count: int, games_played: int, summary: dict
    ) -> None:
        self._conn.execute(
            "INSERT INTO evaluation_summaries (table_id, game_count, games_played, recorded_at, summary_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (table_id, game_count, games_played, now_iso(), json.dumps(summary, ensure_ascii=False)),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
