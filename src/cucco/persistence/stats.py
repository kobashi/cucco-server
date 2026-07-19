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


def _career_rows(conn: sqlite3.Connection, *, mode: str | None, key_fn) -> list[CareerRow]:
    rows = conn.execute(
        f"SELECT p.name, p.player_type, {_policy_col(conn)}, p.final_rank, p.final_chips, g.ended_at, g.mode "
        "FROM participants p JOIN games g ON g.id = p.game_id "
        "ORDER BY g.ended_at"
    ).fetchall()
    acc: dict[object, dict] = {}
    for r in rows:
        if mode is not None and r["mode"] != mode:
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


def career_by_name(conn: sqlite3.Connection, *, mode: str | None = None) -> list[CareerRow]:
    """One career row per (folded) display name, all player types."""
    return _career_rows(conn, mode=mode, key_fn=lambda r: folded_name(r["name"]))


def career_by_policy(conn: sqlite3.Connection, *, mode: str | None = None) -> list[CareerRow]:
    """One career row per built-in AI policy (embedded bots only -- external
    clients have no recorded policy). Aggregates across bot instances, so
    `AI-matrix-1` and `AI-matrix-2` both feed the `matrix` row."""
    rows = _career_rows(conn, mode=mode, key_fn=lambda r: r["ai_policy"])
    return [CareerRow(**{**row.__dict__, "name": row.ai_policy}) for row in rows]


def player_games(conn: sqlite3.Connection, name: str, *, limit: int = 20) -> list[sqlite3.Row]:
    """The most recent games one (folded) name appeared in."""
    target = folded_name(name)
    rows = conn.execute(
        f"SELECT g.id, g.table_id, g.mode, g.ended_at, p.name, p.final_rank, p.final_chips, {_policy_col(conn)}, "
        "(SELECT COUNT(*) FROM participants q WHERE q.game_id = g.id) AS field_size "
        "FROM participants p JOIN games g ON g.id = p.game_id "
        "ORDER BY g.ended_at DESC, g.id DESC"
    ).fetchall()
    return [r for r in rows if folded_name(r["name"]) == target][:limit]


def recent_games(conn: sqlite3.Connection, *, limit: int = 10) -> list[dict]:
    """The latest games, each with its final standings."""
    games = conn.execute(
        "SELECT id, table_id, mode, ended_at FROM games ORDER BY ended_at DESC, id DESC LIMIT ?",
        (limit,),
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


def evaluation_runs(conn: sqlite3.Connection, *, limit: int = 10) -> list[dict]:
    """The latest evaluation-mode aggregate summaries, JSON decoded."""
    rows = conn.execute(
        "SELECT table_id, game_count, games_played, recorded_at, summary_json "
        "FROM evaluation_summaries ORDER BY recorded_at DESC, id DESC LIMIT ?",
        (limit,),
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
