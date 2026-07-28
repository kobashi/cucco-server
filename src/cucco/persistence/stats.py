"""Read-side aggregation over the results store (AIロードマップ第3段階).

The write side (`results_store.py`) records one row per game and per
participant; this module answers the questions people actually ask of that
data: career standings per player name, per-policy comparisons, and the
recent-game / evaluation-run listings. Pure reads -- safe to run against a
live server's database file.

Cross-game identity: `player_id` is a fresh uuid per connection, so careers
are keyed by DISPLAY NAME, folded the same way the server's seat-collision
check folds it (NFKC + casefold). Two people sharing a name therefore share
a career row -- acceptable for the seminar's scale, and the same limitation
docs/security-notes.md already documents for seat collisions.

集計期間: every read here takes `period_id`, where None means "all periods
combined". Callers that want the live standings pass `current_period(conn)`;
the standings restart from zero at each 〆 simply because the caller starts
asking about a new period, not because anything was deleted.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cucco.protocol.actions import folded_name


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the results DB read-only (URI mode) so the stats CLI can never
    write to -- or create -- a results file."""
    if not db_path.exists():
        raise FileNotFoundError(f"results database not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _policy_col(conn: sqlite3.Connection) -> str:
    """`p.ai_policy`, or a NULL literal for result files written before the
    column existed -- being read-only, this connection cannot migrate them
    (the server's own write path migrates on next open)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(participants)")}
    return "p.ai_policy" if "ai_policy" in columns else "NULL AS ai_policy"


# -- 集計期間 -------------------------------------------------------------------
#
# Same read-only caveat as _policy_col: a results file written before periods
# existed has neither the table nor the columns, and this connection cannot
# add them. Such a file reads as "one unnamed period covering everything",
# which is exactly what it is until the server next opens it for writing.


def _has_period_column(conn: sqlite3.Connection, table: str) -> bool:
    return "period_id" in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _period_col(conn: sqlite3.Connection, table: str, alias: str) -> str:
    return f"{alias}.period_id" if _has_period_column(conn, table) else "NULL AS period_id"


def has_periods(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='periods'").fetchone()[0] > 0
    )


def list_periods(conn: sqlite3.Connection) -> list[dict]:
    """Every 集計期間, newest first, with the number of games in each."""
    if not has_periods(conn):
        return []
    rows = conn.execute(
        "SELECT p.id, p.seq, p.name, p.started_at, p.closed_at, "
        "(SELECT COUNT(*) FROM games g WHERE g.period_id = p.id) AS games "
        "FROM periods p ORDER BY p.seq DESC, p.id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def current_period(conn: sqlite3.Connection) -> dict | None:
    """The open period (closed_at IS NULL), or None on a results file that
    has no period bookkeeping yet."""
    if not has_periods(conn):
        return None
    row = conn.execute(
        "SELECT id, seq, name, started_at, closed_at FROM periods WHERE closed_at IS NULL ORDER BY id LIMIT 1"
    ).fetchone()
    return dict(row) if row is not None else None


def resolve_period(conn: sqlite3.Connection, requested) -> int | None:
    """Turn a caller's period selector into an id to filter on.

    `"all"` (or an empty value) means every period combined -> None.
    `"current"` means whichever period is open right now. An integer id is
    validated against the table so a stale picker can't silently report an
    empty period as if it were real.
    """
    if requested in (None, "", "all"):
        return None
    if requested == "current":
        current = current_period(conn)
        return current["id"] if current else None
    try:
        period_id = int(requested)
    except (TypeError, ValueError):
        raise ValueError(f"集計期間の指定が不正です: {requested!r}") from None
    if has_periods(conn):
        exists = conn.execute("SELECT 1 FROM periods WHERE id = ?", (period_id,)).fetchone()
        if exists is None:
            raise ValueError(f"その集計期間はありません: {period_id}")
    return period_id


@dataclass(frozen=True)
class CareerRow:
    name: str  # most recently seen display spelling
    player_type: str
    ai_policy: str | None
    games: int
    wins: int
    total_rank: int
    total_chips: int
    last_played: str

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0

    @property
    def avg_rank(self) -> float:
        return self.total_rank / self.games if self.games else 0.0

    @property
    def avg_chips(self) -> float:
        return self.total_chips / self.games if self.games else 0.0


def _career_rows(
    conn: sqlite3.Connection, *, mode: str | None, key_fn, period_id: int | None = None
) -> list[CareerRow]:
    rows = conn.execute(
        f"SELECT p.name, p.player_type, {_policy_col(conn)}, p.final_rank, p.final_chips, g.ended_at, g.mode, "
        f"{_period_col(conn, 'games', 'g')} "
        "FROM participants p JOIN games g ON g.id = p.game_id "
        "ORDER BY g.ended_at"
    ).fetchall()
    acc: dict[object, dict] = {}
    for r in rows:
        if mode is not None and r["mode"] != mode:
            continue
        if period_id is not None and r["period_id"] != period_id:
            continue
        key = key_fn(r)
        if key is None:
            continue
        entry = acc.setdefault(
            key,
            {"name": r["name"], "player_type": r["player_type"], "ai_policy": r["ai_policy"],
             "games": 0, "wins": 0, "total_rank": 0, "total_chips": 0, "last_played": r["ended_at"]},
        )
        entry["games"] += 1
        entry["wins"] += 1 if r["final_rank"] == 1 else 0
        entry["total_rank"] += r["final_rank"]
        entry["total_chips"] += r["final_chips"]
        # Rows arrive in ended_at order: keep the latest spelling/type.
        entry["name"] = r["name"]
        entry["player_type"] = r["player_type"]
        entry["ai_policy"] = r["ai_policy"]
        entry["last_played"] = r["ended_at"]
    out = [CareerRow(**e) for e in acc.values()]
    out.sort(key=lambda c: (-c.games, c.avg_rank))
    return out


def career_by_name(
    conn: sqlite3.Connection, *, mode: str | None = None, period_id: int | None = None
) -> list[CareerRow]:
    """One career row per (folded) display name, all player types."""
    return _career_rows(conn, mode=mode, period_id=period_id, key_fn=lambda r: folded_name(r["name"]))


def career_by_policy(
    conn: sqlite3.Connection, *, mode: str | None = None, period_id: int | None = None
) -> list[CareerRow]:
    """One career row per built-in AI policy (embedded bots only -- external
    clients have no recorded policy). Aggregates across bot instances, so
    `AI-matrix-1` and `AI-matrix-2` both feed the `matrix` row."""
    rows = _career_rows(conn, mode=mode, period_id=period_id, key_fn=lambda r: r["ai_policy"])
    return [CareerRow(**{**row.__dict__, "name": row.ai_policy}) for row in rows]


def player_games(
    conn: sqlite3.Connection, name: str, *, limit: int = 20, period_id: int | None = None
) -> list[sqlite3.Row]:
    """The most recent games one (folded) name appeared in."""
    target = folded_name(name)
    rows = conn.execute(
        f"SELECT g.id, g.table_id, g.mode, g.ended_at, p.name, p.final_rank, p.final_chips, {_policy_col(conn)}, "
        f"{_period_col(conn, 'games', 'g')}, "
        "(SELECT COUNT(*) FROM participants q WHERE q.game_id = g.id) AS field_size "
        "FROM participants p JOIN games g ON g.id = p.game_id "
        "ORDER BY g.ended_at DESC, g.id DESC"
    ).fetchall()
    matched = [r for r in rows if folded_name(r["name"]) == target]
    if period_id is not None:
        matched = [r for r in matched if r["period_id"] == period_id]
    return matched[:limit]


def recent_games(conn: sqlite3.Connection, *, limit: int = 10, period_id: int | None = None) -> list[dict]:
    """The latest games, each with its final standings."""
    filtering = period_id is not None and _has_period_column(conn, "games")
    where, params = (" WHERE period_id = ?", [period_id]) if filtering else ("", [])
    games = conn.execute(
        "SELECT id, table_id, mode, ended_at FROM games" + where + " ORDER BY ended_at DESC, id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    out = []
    for g in games:
        standings = conn.execute(
            f"SELECT p.name, p.player_type, {_policy_col(conn)}, p.final_rank, p.final_chips "
            "FROM participants p WHERE p.game_id = ? ORDER BY p.final_rank",
            (g["id"],),
        ).fetchall()
        out.append({"game": g, "standings": standings})
    return out


def evaluation_runs(conn: sqlite3.Connection, *, limit: int = 10, period_id: int | None = None) -> list[dict]:
    """The latest evaluation-mode aggregate summaries, JSON decoded."""
    filtering = period_id is not None and _has_period_column(conn, "evaluation_summaries")
    where, params = (" WHERE period_id = ?", [period_id]) if filtering else ("", [])
    rows = conn.execute(
        "SELECT table_id, game_count, games_played, recorded_at, summary_json "
        "FROM evaluation_summaries" + where + " ORDER BY recorded_at DESC, id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [
        {
            "table_id": r["table_id"],
            "game_count": r["game_count"],
            "games_played": r["games_played"],
            "recorded_at": r["recorded_at"],
            "summary": json.loads(r["summary_json"]),
        }
        for r in rows
    ]
