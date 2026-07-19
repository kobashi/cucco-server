"""Stats aggregation layer + the ai_policy column migration."""

import sqlite3

import pytest

from cucco.persistence import stats
from cucco.persistence.db import connect
from cucco.persistence.results_store import PlayerInfo, ResultsStore


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "results.db"


def seed_games(store: ResultsStore) -> None:
    alice = PlayerInfo("id-a1", "Alice", "human")
    bot_m = PlayerInfo("id-m1", "AI-matrix-1", "ai", ai_policy="matrix")
    bot_c = PlayerInfo("id-c1", "AI-counting_aggres-1", "ai", ai_policy="counting_aggressive")
    store.record_game_ended(
        table_id="T1", mode="normal",
        players=[alice, bot_m, bot_c],
        ranking=(("id-a1", 30), ("id-m1", 20), ("id-c1", 10)),
        action_log_path=None,
    )
    # Alice again under a full-width spelling: same folded career.
    alice2 = PlayerInfo("id-a2", "Ａlice", "human")
    store.record_game_ended(
        table_id="T2", mode="normal",
        players=[alice2, bot_m, bot_c],
        ranking=(("id-c1", 25), ("id-a2", 15), ("id-m1", 5)),
        action_log_path=None,
    )


def test_career_by_name_folds_spellings_and_aggregates(store_path):
    store = ResultsStore(store_path)
    seed_games(store)
    store.close()
    conn = stats.open_readonly(store_path)
    rows = {c.name: c for c in stats.career_by_name(conn)}
    # Latest spelling wins as the display name; both games count.
    alice = rows["Ａlice"]
    assert alice.games == 2 and alice.wins == 1
    assert alice.avg_rank == pytest.approx(1.5)
    assert alice.avg_chips == pytest.approx(22.5)
    matrix = rows["AI-matrix-1"]
    assert matrix.games == 2 and matrix.wins == 0 and matrix.ai_policy == "matrix"
    conn.close()


def test_career_by_policy_groups_bots_and_skips_humans(store_path):
    store = ResultsStore(store_path)
    seed_games(store)
    store.close()
    conn = stats.open_readonly(store_path)
    rows = {c.name: c for c in stats.career_by_policy(conn)}
    assert set(rows) == {"matrix", "counting_aggressive"}
    assert rows["counting_aggressive"].wins == 1
    conn.close()


def test_player_games_and_recent_games(store_path):
    store = ResultsStore(store_path)
    seed_games(store)
    store.close()
    conn = stats.open_readonly(store_path)
    games = stats.player_games(conn, "ａｌｉｃｅ")  # full-width lowercase still matches
    assert len(games) == 2
    assert games[0]["field_size"] == 3
    recent = stats.recent_games(conn, limit=1)
    assert len(recent) == 1
    assert [s["final_rank"] for s in recent[0]["standings"]] == [1, 2, 3]
    conn.close()


def test_open_readonly_refuses_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        stats.open_readonly(tmp_path / "nope.db")


def test_migration_adds_ai_policy_to_an_old_database(tmp_path):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript(
        """
        CREATE TABLE games (id INTEGER PRIMARY KEY AUTOINCREMENT, table_id TEXT NOT NULL,
            mode TEXT NOT NULL, ended_at TEXT NOT NULL, action_log_path TEXT);
        CREATE TABLE participants (game_id INTEGER NOT NULL, player_id TEXT NOT NULL,
            name TEXT NOT NULL, player_type TEXT NOT NULL,
            final_rank INTEGER NOT NULL, final_chips INTEGER NOT NULL);
        INSERT INTO games (table_id, mode, ended_at) VALUES ('T0', 'normal', '2026-01-01T00:00:00');
        INSERT INTO participants VALUES (1, 'p1', 'Old', 'human', 1, 10);
        """
    )
    conn.commit()
    conn.close()

    migrated = connect(old)  # runs the idempotent migration
    columns = {row[1] for row in migrated.execute("PRAGMA table_info(participants)")}
    assert "ai_policy" in columns
    # Old rows read back with NULL policy; a second connect stays idempotent.
    assert migrated.execute("SELECT ai_policy FROM participants").fetchone()[0] is None
    migrated.close()
    connect(old).close()


def test_stats_read_an_unmigrated_old_schema_file(tmp_path):
    # The CLI opens read-only and cannot migrate; a results file written by
    # an older server (no ai_policy column) must still be readable.
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript(
        """
        CREATE TABLE games (id INTEGER PRIMARY KEY AUTOINCREMENT, table_id TEXT NOT NULL,
            mode TEXT NOT NULL, ended_at TEXT NOT NULL, action_log_path TEXT);
        CREATE TABLE participants (game_id INTEGER NOT NULL, player_id TEXT NOT NULL,
            name TEXT NOT NULL, player_type TEXT NOT NULL,
            final_rank INTEGER NOT NULL, final_chips INTEGER NOT NULL);
        INSERT INTO games (table_id, mode, ended_at) VALUES ('T0', 'normal', '2026-01-01T00:00:00');
        INSERT INTO participants VALUES (1, 'p1', 'Old', 'human', 1, 10);
        """
    )
    conn.commit()
    conn.close()
    ro = stats.open_readonly(old)
    rows = stats.career_by_name(ro)
    assert rows[0].name == "Old" and rows[0].ai_policy is None
    assert stats.career_by_policy(ro) == []
    assert stats.player_games(ro, "old")[0]["field_size"] == 1
    assert stats.recent_games(ro, limit=1)[0]["standings"][0]["name"] == "Old"
    ro.close()


def test_migration_preserves_old_rows_in_career_stats(tmp_path, store_path):
    store = ResultsStore(store_path)
    store.record_game_ended(
        table_id="T1", mode="normal",
        players=[PlayerInfo("x", "Solo", "human")],
        ranking=(("x", 5),),
        action_log_path=None,
    )
    store.close()
    conn = stats.open_readonly(store_path)
    rows = stats.career_by_name(conn)
    assert rows[0].name == "Solo" and rows[0].ai_policy is None
    conn.close()
