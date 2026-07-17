import json

from cucco.domain.cards import Rank
from cucco.domain.events import DealOpened, PotStarted
from cucco.persistence.action_log import ActionLogWriter


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_write_seed_records_the_shuffle_seed_first(tmp_path):
    path = tmp_path / "game.jsonl"
    log = ActionLogWriter(path)
    log.write_seed(123456)
    log.close()

    lines = read_lines(path)
    assert lines[0]["kind"] == "seed"
    assert lines[0]["seed"] == 123456
    assert "ts" in lines[0]


def test_write_action_records_a_raw_client_action(tmp_path):
    # write_action logs a raw client action (one with no corresponding domain
    # event) for deterministic replay -- exercised here with a generic action.
    path = tmp_path / "game.jsonl"
    log = ActionLogWriter(path)
    log.write_action("B", "some_raw_action", {"via_timeout": False})
    log.close()

    lines = read_lines(path)
    assert lines[0] == {
        "kind": "action",
        "player_id": "B",
        "action_type": "some_raw_action",
        "payload": {"via_timeout": False},
        "ts": lines[0]["ts"],
    }


def test_write_event_serializes_a_dataclass_with_enum_and_tuple_fields(tmp_path):
    path = tmp_path / "game.jsonl"
    log = ActionLogWriter(path)
    log.write_event(
        DealOpened(
            hands={"A": Rank.JOKER, "B": Rank.N5},
            elevated_joker_holders=frozenset({"A"}),
            losers=("B",),
        )
    )
    log.close()

    lines = read_lines(path)
    assert lines[0]["kind"] == "event"
    assert lines[0]["event_type"] == "DealOpened"
    payload = lines[0]["payload"]
    assert payload["hands"] == {"A": Rank.JOKER.value, "B": Rank.N5.value}
    assert payload["elevated_joker_holders"] == ["A"]
    assert payload["losers"] == ["B"]


def test_write_event_serializes_nested_dict_and_bool_fields(tmp_path):
    path = tmp_path / "game.jsonl"
    log = ActionLogWriter(path)
    log.write_event(
        PotStarted(
            pot_number=2,
            dealer_id="C",
            participants=("A", "B", "C"),
            chips_now={"A": 10, "B": 0, "C": 20},
            entry_fee_waived=True,
        )
    )
    log.close()

    lines = read_lines(path)
    payload = lines[0]["payload"]
    assert payload == {
        "pot_number": 2,
        "dealer_id": "C",
        "participants": ["A", "B", "C"],
        "chips_now": {"A": 10, "B": 0, "C": 20},
        "entry_fee_waived": True,
        "pot_chips": 0,
    }


def test_entries_are_appended_in_order_across_multiple_writes(tmp_path):
    path = tmp_path / "game.jsonl"
    log = ActionLogWriter(path)
    log.write_seed(1)
    log.write_action("A", "no_change_declare", {"via_timeout": True})
    log.write_event(PotStarted(pot_number=1, dealer_id="A", participants=("A", "B"), chips_now={"A": 24, "B": 24}))
    log.close()

    lines = read_lines(path)
    assert [line["kind"] for line in lines] == ["seed", "action", "event"]
