"""SQLite schema for the results store (docs/protocol/design.md
「永続化・成績記録」). One row per completed game plus one row per
participant's final standing in it, plus one row per evaluation-mode
table's aggregate summary.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    action_log_path TEXT
);

CREATE TABLE IF NOT EXISTS participants (
    game_id INTEGER NOT NULL REFERENCES games(id),
    player_id TEXT NOT NULL,
    name TEXT NOT NULL,
    player_type TEXT NOT NULL,
    final_rank INTEGER NOT NULL,
    final_chips INTEGER NOT NULL
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
    summary_json TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
