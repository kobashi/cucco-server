"""Results store: records one row per completed game at `game_ended`
(docs/protocol/design.md 「永続化・成績記録」). Game-in-progress state lives
entirely in memory -- this is the only thing that outlives the process.
"""

from __future__ import annotations

import contextlib
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
    # Built-in policy for server-embedded bots; None otherwise.
    ai_policy: str | None = None


class ResultsStore:
    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path)
        self._conn = connect(db_path)

    # -- maintenance ---------------------------------------------------------
    #
    # Deletions go through THIS connection (the server's single writer) rather
    # than a second one, so admin maintenance can never race the game loop's
    # own writes into a "database is locked".

    def storage_summary(self) -> dict:
        counts = {
            table: self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("games", "participants", "evaluation_summaries")
        }
        oldest, newest = self._conn.execute("SELECT MIN(ended_at), MAX(ended_at) FROM games").fetchone()
        return {
            "db_path": str(self.path),
            "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "games": counts["games"],
            "participants": counts["participants"],
            "evaluation_summaries": counts["evaluation_summaries"],
            "oldest_game": oldest,
            "newest_game": newest,
        }

    def delete_results(self, *, before_iso: str | None = None) -> dict:
        """Delete recorded results -- everything, or only what ended before
        `before_iso`. Irreversible; the caller is responsible for confirming.
        Returns the row counts actually removed."""
        if before_iso is None:
            where, params = "", ()
        else:
            where, params = " WHERE ended_at < ?", (before_iso,)
        participants = self._conn.execute(
            "DELETE FROM participants WHERE game_id IN (SELECT id FROM games" + where + ")", params
        ).rowcount
        games = self._conn.execute("DELETE FROM games" + where, params).rowcount
        eval_where, eval_params = ("", ()) if before_iso is None else (" WHERE recorded_at < ?", (before_iso,))
        evaluations = self._conn.execute("DELETE FROM evaluation_summaries" + eval_where, eval_params).rowcount
        self._conn.commit()
        # Reclaim the freed pages so the file size reflects the deletion --
        # the point of this tool is usually disk pressure.
        with contextlib.suppress(Exception):
            self._conn.execute("VACUUM")
        return {"games": games, "participants": participants, "evaluation_summaries": evaluations}

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
                "INSERT INTO participants (game_id, player_id, name, player_type, final_rank, final_chips, ai_policy) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    game_id,
                    player_id,
                    info.name if info is not None else player_id,
                    info.player_type if info is not None else "unknown",
                    rank,
                    chips,
                    info.ai_policy if info is not None else None,
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
