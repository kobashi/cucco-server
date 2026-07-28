"""集計期間: tagging writes, 〆ing, and adopting a pre-periods database."""

import sqlite3

import pytest

from cucco.persistence import stats
from cucco.persistence.db import LEGACY_PERIOD_NAME, connect
from cucco.persistence.results_store import PlayerInfo, ResultsStore


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "results.db"


def play(store: ResultsStore, table_id: str, winner: str = "Alice") -> None:
    store.record_game_ended(
        table_id=table_id,
        mode="normal",
        players=[PlayerInfo("id-a", winner, "human"), PlayerInfo("id-b", "Bob", "human")],
        ranking=(("id-a", 30), ("id-b", 10)),
        action_log_path=None,
    )


def test_a_fresh_store_opens_one_period_and_tags_games_with_it(store_path):
    store = ResultsStore(store_path)
    current = store.current_period()
    assert current["seq"] == 1 and current["name"] == "第1期" and current["closed_at"] is None

    play(store, "T1")
    store.record_evaluation_summary(table_id="E1", game_count=1, games_played=1, summary={})

    assert store._conn.execute("SELECT period_id FROM games").fetchall() == [(current["id"],)]
    assert store._conn.execute("SELECT period_id FROM evaluation_summaries").fetchall() == [(current["id"],)]
    store.close()


def test_close_period_starts_a_new_one_without_deleting_the_old_games(store_path):
    store = ResultsStore(store_path)
    first = store.current_period()
    play(store, "T1")
    play(store, "T2")

    result = store.close_period(next_name="2026年度後期")

    assert result["closed"]["id"] == first["id"]
    assert result["closed"]["games"] == 2
    assert result["opened"]["name"] == "2026年度後期"
    assert result["opened"]["seq"] == 2
    # The point of 〆る: the games survive, they just belong to a closed period.
    assert store._conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2
    assert store.current_period()["id"] == result["opened"]["id"]

    play(store, "T3")
    by_period = dict(store._conn.execute("SELECT period_id, COUNT(*) FROM games GROUP BY period_id").fetchall())
    assert by_period == {first["id"]: 2, result["opened"]["id"]: 1}
    store.close()


def test_close_period_without_a_name_numbers_the_next_one(store_path):
    store = ResultsStore(store_path)
    assert store.close_period()["opened"]["name"] == "第2期"
    assert store.close_period(next_name="   ")["opened"]["name"] == "第3期"
    # Exactly one period is open no matter how often it is closed.
    assert store._conn.execute("SELECT COUNT(*) FROM periods WHERE closed_at IS NULL").fetchone()[0] == 1
    store.close()


def test_standings_restart_at_the_new_period_but_stay_readable_for_the_closed_one(store_path):
    store = ResultsStore(store_path)
    first = store.current_period()
    play(store, "T1")
    play(store, "T2")
    opened = store.close_period()["opened"]
    play(store, "T3")
    store.close()

    conn = stats.open_readonly(store_path)
    live = stats.career_by_name(conn, period_id=opened["id"])
    assert [(r.name, r.games) for r in live] == [("Alice", 1), ("Bob", 1)]

    closed = stats.career_by_name(conn, period_id=first["id"])
    assert [(r.name, r.games) for r in closed] == [("Alice", 2), ("Bob", 2)]

    combined = stats.career_by_name(conn, period_id=None)
    assert [(r.name, r.games) for r in combined] == [("Alice", 3), ("Bob", 3)]

    assert len(stats.recent_games(conn, period_id=first["id"])) == 2
    assert len(stats.recent_games(conn, period_id=opened["id"])) == 1
    conn.close()


def test_rename_period_rejects_blank_names_and_unknown_ids(store_path):
    store = ResultsStore(store_path)
    current = store.current_period()

    assert store.rename_period(period_id=current["id"], name="  ゼミ第3回  ")["name"] == "ゼミ第3回"
    with pytest.raises(ValueError):
        store.rename_period(period_id=current["id"], name="   ")
    with pytest.raises(ValueError):
        store.rename_period(period_id=9999, name="どこにもない")
    store.close()


def test_resolve_period_maps_the_selectors_and_rejects_a_stale_id(store_path):
    store = ResultsStore(store_path)
    first = store.current_period()
    opened = store.close_period()["opened"]
    store.close()

    conn = stats.open_readonly(store_path)
    assert stats.resolve_period(conn, "all") is None
    assert stats.resolve_period(conn, None) is None
    assert stats.resolve_period(conn, "current") == opened["id"]
    assert stats.resolve_period(conn, first["id"]) == first["id"]
    with pytest.raises(ValueError):
        stats.resolve_period(conn, 4242)
    with pytest.raises(ValueError):
        stats.resolve_period(conn, "きのう")
    conn.close()


def test_purging_everything_resets_the_period_history(store_path):
    store = ResultsStore(store_path)
    play(store, "T1")
    store.close_period()
    play(store, "T2")

    store.delete_results()

    # Empty periods would only clutter the picker, so a full purge starts over.
    periods = store.list_periods()
    assert len(periods) == 1
    assert periods[0]["name"] == "第1期" and periods[0]["closed_at"] is None
    # And the store is immediately usable again.
    play(store, "T3")
    assert store._conn.execute("SELECT period_id FROM games").fetchall() == [(periods[0]["id"],)]
    store.close()


def test_a_dated_purge_leaves_the_period_history_alone(store_path):
    store = ResultsStore(store_path)
    play(store, "T1")
    store.close_period()

    store.delete_results(before_iso="2000-01-01T00:00:00+00:00")  # deletes nothing

    assert len(store.list_periods()) == 2
    store.close()


# -- adopting a database written before periods existed --------------------------


def _write_pre_periods_db(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE games (id INTEGER PRIMARY KEY AUTOINCREMENT, table_id TEXT NOT NULL,
            mode TEXT NOT NULL, ended_at TEXT NOT NULL, action_log_path TEXT);
        CREATE TABLE participants (game_id INTEGER NOT NULL, player_id TEXT NOT NULL,
            name TEXT NOT NULL, player_type TEXT NOT NULL,
            final_rank INTEGER NOT NULL, final_chips INTEGER NOT NULL);
        INSERT INTO games (table_id, mode, ended_at) VALUES ('T0', 'normal', '2026-07-09T12:00:00+00:00');
        INSERT INTO games (table_id, mode, ended_at) VALUES ('T1', 'normal', '2026-07-27T07:00:00+00:00');
        INSERT INTO participants VALUES (1, 'p1', 'Old', 'human', 1, 10);
        INSERT INTO participants VALUES (2, 'p1', 'Old', 'human', 2, 5);
        """
    )
    conn.commit()
    conn.close()


def test_existing_results_are_adopted_into_a_closed_legacy_period(tmp_path):
    old = tmp_path / "old.db"
    _write_pre_periods_db(old)

    conn = connect(old)

    periods = conn.execute("SELECT id, seq, name, started_at, closed_at FROM periods ORDER BY seq").fetchall()
    assert len(periods) == 2
    legacy, live = periods
    assert legacy[2] == LEGACY_PERIOD_NAME
    # The adopted period reports the span the games actually cover...
    assert legacy[3] == "2026-07-09T12:00:00+00:00"
    assert legacy[4] == "2026-07-27T07:00:00+00:00"
    # ...and every pre-existing game belongs to it, so nothing is orphaned.
    assert conn.execute("SELECT COUNT(*) FROM games WHERE period_id = ?", (legacy[0],)).fetchone()[0] == 2
    # The live period starts empty: the operator's current standings are clean.
    assert live[4] is None
    assert conn.execute("SELECT COUNT(*) FROM games WHERE period_id = ?", (live[0],)).fetchone()[0] == 0
    conn.close()

    # Reopening must not adopt a second time.
    again = connect(old)
    assert again.execute("SELECT COUNT(*) FROM periods").fetchone()[0] == 2
    again.close()


def test_stats_still_read_a_file_that_has_no_period_bookkeeping(tmp_path):
    # The read-only CLI cannot migrate, so a results file the server hasn't
    # reopened yet must degrade to "one unnamed period covering everything".
    old = tmp_path / "old.db"
    _write_pre_periods_db(old)

    conn = stats.open_readonly(old)
    assert stats.has_periods(conn) is False
    assert stats.list_periods(conn) == []
    assert stats.current_period(conn) is None
    assert stats.resolve_period(conn, "current") is None
    assert [(r.name, r.games) for r in stats.career_by_name(conn)] == [("Old", 2)]
    # A period filter it cannot honour must not raise or silently drop rows.
    assert len(stats.recent_games(conn, period_id=1)) == 2
    conn.close()
