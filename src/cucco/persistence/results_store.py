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
from cucco.persistence.db import connect, ensure_periods


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

    # -- 集計期間 -------------------------------------------------------------
    #
    # Every write is tagged with whichever period is open at the time, and the
    # operator closes one from the admin console to restart the standings.
    # The open period is looked up per write rather than cached: it is a
    # single-row read on a table with a handful of rows, and caching it would
    # buy nothing while making a stale tag possible right after a 〆.

    def current_period(self) -> dict:
        """The open period. `ensure_periods` guarantees one exists, so this
        never returns None for a store built through `connect`."""
        row = self._conn.execute(
            "SELECT id, seq, name, started_at, closed_at FROM periods WHERE closed_at IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:  # a caller wiped the table out from under us
            ensure_periods(self._conn)
            self._conn.commit()
            return self.current_period()
        return _period_dict(row)

    def list_periods(self) -> list[dict]:
        """Every period, newest first, each with the number of games recorded
        in it -- what the console's period picker is built from."""
        rows = self._conn.execute(
            "SELECT p.id, p.seq, p.name, p.started_at, p.closed_at, "
            "(SELECT COUNT(*) FROM games g WHERE g.period_id = p.id) AS games "
            "FROM periods p ORDER BY p.seq DESC, p.id DESC"
        ).fetchall()
        return [{**_period_dict(row), "games": row[5]} for row in rows]

    def close_period(self, *, next_name: str | None = None) -> dict:
        """〆る: stamp the open period closed and open a fresh one. The closed
        period keeps every game recorded in it -- only the *live* standings
        reset, because those are now reported against the new period."""
        closing = self.current_period()
        stamp = now_iso()
        self._conn.execute("UPDATE periods SET closed_at = ? WHERE id = ?", (stamp, closing["id"]))
        next_seq = (self._conn.execute("SELECT MAX(seq) FROM periods").fetchone()[0] or 0) + 1
        name = (next_name or "").strip() or f"第{next_seq}期"
        cur = self._conn.execute(
            "INSERT INTO periods (seq, name, started_at, closed_at) VALUES (?, ?, ?, NULL)",
            (next_seq, name, stamp),
        )
        self._conn.commit()
        closed_games = self._conn.execute(
            "SELECT COUNT(*) FROM games WHERE period_id = ?", (closing["id"],)
        ).fetchone()[0]
        return {
            "closed": {**closing, "closed_at": stamp, "games": closed_games},
            "opened": {
                "id": cur.lastrowid, "seq": next_seq, "name": name,
                "started_at": stamp, "closed_at": None, "games": 0,
            },
        }

    def rename_period(self, *, period_id: int, name: str) -> dict:
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("期間名を入力してください")
        if len(cleaned) > 60:
            raise ValueError("期間名は60文字以内にしてください")
        cur = self._conn.execute("UPDATE periods SET name = ? WHERE id = ?", (cleaned, period_id))
        if cur.rowcount == 0:
            raise ValueError(f"その集計期間はありません: {period_id}")
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id, seq, name, started_at, closed_at FROM periods WHERE id = ?", (period_id,)
        ).fetchone()
        return _period_dict(row)

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
        current = self.current_period()
        return {
            "db_path": str(self.path),
            "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "games": counts["games"],
            "participants": counts["participants"],
            "evaluation_summaries": counts["evaluation_summaries"],
            "oldest_game": oldest,
            "newest_game": newest,
            "periods": self._conn.execute("SELECT COUNT(*) FROM periods").fetchone()[0],
            "current_period": current,
            "current_period_games": self._conn.execute(
                "SELECT COUNT(*) FROM games WHERE period_id = ?", (current["id"],)
            ).fetchone()[0],
        }

    def delete_results(self, *, before_iso: str | None = None) -> dict:
        """Delete recorded results -- everything, or only what ended before
        `before_iso`. Irreversible; the caller is responsible for confirming.
        Returns the row counts actually removed.

        Note this is the *destructive* purge, not 〆る: closing a period keeps
        its games and only restarts the live standings. Wiping everything also
        drops the period history, since periods with no results left in them
        would just be empty rows in the picker; a fresh 第1期 is opened for
        whatever is recorded next."""
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
        if before_iso is None:
            self._conn.execute("DELETE FROM periods")
            ensure_periods(self._conn)
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
            "INSERT INTO games (table_id, mode, ended_at, action_log_path, period_id) VALUES (?, ?, ?, ?, ?)",
            (table_id, mode, now_iso(), action_log_path, self.current_period()["id"]),
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
            "INSERT INTO evaluation_summaries (table_id, game_count, games_played, recorded_at, summary_json, period_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                table_id, game_count, games_played, now_iso(),
                json.dumps(summary, ensure_ascii=False), self.current_period()["id"],
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _period_dict(row) -> dict:
    return {"id": row[0], "seq": row[1], "name": row[2], "started_at": row[3], "closed_at": row[4]}
