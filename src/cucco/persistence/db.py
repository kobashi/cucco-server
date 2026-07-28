"""SQLite schema for the results store (docs/protocol/design.md
「永続化・成績記録」). One row per completed game plus one row per
participant's final standing in it, plus one row per evaluation-mode
table's aggregate summary -- each tagged with the 集計期間 it belongs to.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cucco.domain.timeutil import now_iso

SCHEMA = """
-- 集計期間 (docs/protocol/design.md 「集計期間」). Standings are reported per
-- period, and the operator 〆s the open one from the admin console: that
-- stamps closed_at and opens a fresh period, so the live standings restart
-- from zero while the closed period stays readable. Exactly one row has
-- closed_at IS NULL at any time -- `ensure_periods` maintains that.
CREATE TABLE IF NOT EXISTS periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    action_log_path TEXT,
    -- The period open when the game ended. Added in v1.1.0; NULL only for
    -- rows an older version wrote before ensure_periods backfilled them.
    period_id INTEGER REFERENCES periods(id)
);

CREATE TABLE IF NOT EXISTS participants (
    game_id INTEGER NOT NULL REFERENCES games(id),
    player_id TEXT NOT NULL,
    name TEXT NOT NULL,
    player_type TEXT NOT NULL,
    final_rank INTEGER NOT NULL,
    final_chips INTEGER NOT NULL,
    -- Built-in policy name for server-embedded bots; NULL for humans and
    -- external clients. Added in v0.14.0 (see _migrate for older files).
    ai_policy TEXT
);

CREATE INDEX IF NOT EXISTS idx_participants_game_id ON participants(game_id);

-- One row per evaluation-mode table's game_count run. The individual
-- games themselves are already recorded normally in `games`/`participants`
-- (mode="evaluation") -- this table holds the *aggregate* the run computes
-- on top of those (per-player win rate / avg rank / avg chips /
-- disqualification rate, plus the seat-rotation breakdown), which isn't
-- reconstructible from the per-game rows alone. Stored as a JSON blob
-- rather than normalized further -- this is a research-analysis dump, not
-- something queried relationally.
CREATE TABLE IF NOT EXISTS evaluation_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id TEXT NOT NULL,
    game_count INTEGER NOT NULL,
    games_played INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    period_id INTEGER REFERENCES periods(id)
);
"""
# NOTE: idx_games_period_id is created in _migrate, not here -- on a database
# written before periods existed this script runs while games.period_id is
# still missing, and indexing a column that isn't there yet aborts the whole
# schema step.

LEGACY_PERIOD_NAME = "第0期"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    ensure_periods(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent, additive-only migrations for result files created by
    older versions (CREATE TABLE IF NOT EXISTS never alters an existing
    table, so new columns must be patched in here)."""
    participant_columns = {row[1] for row in conn.execute("PRAGMA table_info(participants)")}
    if "ai_policy" not in participant_columns:
        conn.execute("ALTER TABLE participants ADD COLUMN ai_policy TEXT")
    game_columns = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
    if "period_id" not in game_columns:
        conn.execute("ALTER TABLE games ADD COLUMN period_id INTEGER REFERENCES periods(id)")
    eval_columns = {row[1] for row in conn.execute("PRAGMA table_info(evaluation_summaries)")}
    if "period_id" not in eval_columns:
        conn.execute("ALTER TABLE evaluation_summaries ADD COLUMN period_id INTEGER REFERENCES periods(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_games_period_id ON games(period_id)")


def ensure_periods(conn: sqlite3.Connection) -> None:
    """Guarantee the invariant the rest of the code relies on: every recorded
    result belongs to a period, and exactly one period is open.

    On a database that predates periods this adopts the existing results into
    a single already-closed 第0期 spanning their real timestamps, then opens a
    fresh period for what happens from now on -- so the operator's live
    standings start clean without any history being lost.
    """
    if conn.execute("SELECT COUNT(*) FROM periods").fetchone()[0] == 0:
        oldest, newest = _recorded_span(conn)
        if oldest is not None:
            legacy_id = _insert_period(conn, seq=0, name=LEGACY_PERIOD_NAME, started_at=oldest, closed_at=newest)
            conn.execute("UPDATE games SET period_id = ? WHERE period_id IS NULL", (legacy_id,))
            conn.execute("UPDATE evaluation_summaries SET period_id = ? WHERE period_id IS NULL", (legacy_id,))

    # Whether or not anything was adopted above, leave exactly one period open.
    if conn.execute("SELECT COUNT(*) FROM periods WHERE closed_at IS NULL").fetchone()[0] == 0:
        next_seq = (conn.execute("SELECT MAX(seq) FROM periods").fetchone()[0] or 0) + 1
        _insert_period(conn, seq=next_seq, name=f"第{next_seq}期", started_at=now_iso(), closed_at=None)


def _recorded_span(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    """Oldest and newest timestamps across everything already recorded, so an
    adopted legacy period reports the span it actually covers."""
    stamps = [
        conn.execute("SELECT MIN(ended_at), MAX(ended_at) FROM games").fetchone(),
        conn.execute("SELECT MIN(recorded_at), MAX(recorded_at) FROM evaluation_summaries").fetchone(),
    ]
    lows = [row[0] for row in stamps if row[0] is not None]
    highs = [row[1] for row in stamps if row[1] is not None]
    return (min(lows) if lows else None, max(highs) if highs else None)


def _insert_period(
    conn: sqlite3.Connection, *, seq: int, name: str, started_at: str, closed_at: str | None
) -> int:
    cur = conn.execute(
        "INSERT INTO periods (seq, name, started_at, closed_at) VALUES (?, ?, ?, ?)",
        (seq, name, started_at, closed_at),
    )
    return cur.lastrowid
