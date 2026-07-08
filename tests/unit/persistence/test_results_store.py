from pathlib import Path

from cucco.persistence.results_store import PlayerInfo, ResultsStore


def make_store(tmp_path: Path) -> ResultsStore:
    return ResultsStore(tmp_path / "results.db")


def test_record_game_ended_writes_a_game_row_and_ranked_participant_rows(tmp_path):
    store = make_store(tmp_path)
    players = [
        PlayerInfo(player_id="A", name="Alice", player_type="human"),
        PlayerInfo(player_id="B", name="Bob", player_type="ai"),
    ]

    store.record_game_ended(
        table_id="ABC123",
        mode="normal",
        players=players,
        ranking=(("A", 40), ("B", 10)),
        action_log_path="data/action_logs/ABC123.jsonl",
    )

    games = store._conn.execute("SELECT table_id, mode, action_log_path FROM games").fetchall()
    assert games == [("ABC123", "normal", "data/action_logs/ABC123.jsonl")]

    rows = store._conn.execute(
        "SELECT player_id, name, player_type, final_rank, final_chips FROM participants ORDER BY final_rank"
    ).fetchall()
    assert rows == [
        ("A", "Alice", "human", 1, 40),
        ("B", "Bob", "ai", 2, 10),
    ]
    store.close()


def test_record_game_ended_falls_back_to_player_id_for_an_unknown_participant(tmp_path):
    # Defensive: a ranking entry with no matching PlayerInfo (shouldn't
    # normally happen, but the ranking and the roster come from different
    # sources) must not crash the write.
    store = make_store(tmp_path)

    store.record_game_ended(
        table_id="XYZ789",
        mode="normal",
        players=[],
        ranking=(("C", 25),),
        action_log_path=None,
    )

    row = store._conn.execute("SELECT player_id, name, player_type FROM participants").fetchone()
    assert row == ("C", "C", "unknown")
    store.close()


def test_reopening_the_same_db_path_reuses_the_existing_schema(tmp_path):
    db_path = tmp_path / "results.db"
    ResultsStore(db_path).close()
    store = ResultsStore(db_path)  # must not error on an already-initialized schema
    store.record_game_ended(table_id="T1", mode="normal", players=[], ranking=(("A", 1),), action_log_path=None)
    count = store._conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
    assert count == 1
    store.close()
