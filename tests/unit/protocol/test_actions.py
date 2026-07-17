import pytest

from cucco.protocol.actions import (
    CambioDeclare,
    ContinueDeclare,
    CreateTable,
    CuccoDeclare,
    DealerReady,
    Identify,
    JoinTable,
    NoChangeDeclare,
    Ready,
    create_table_to_config,
    parse_action,
)
from cucco.protocol.envelope import Envelope
from cucco.protocol.errors import ProtocolError


def env(type_: str, payload: dict | None = None) -> Envelope:
    return Envelope(type=type_, payload=payload or {})


def test_parse_identify():
    action = parse_action(env("identify", {"name": "Alice", "player_type": "human"}))
    assert action == Identify(name="Alice", player_type="human")


def test_parse_identify_rejects_invalid_player_type():
    with pytest.raises(ProtocolError):
        parse_action(env("identify", {"name": "Alice", "player_type": "robot"}))


def test_parse_identify_rejects_missing_name():
    with pytest.raises(ProtocolError):
        parse_action(env("identify", {"player_type": "human"}))


def test_parse_identify_trims_surrounding_whitespace():
    action = parse_action(env("identify", {"name": "  Alice  ", "player_type": "human"}))
    assert action == Identify(name="Alice", player_type="human")


def test_parse_identify_rejects_whitespace_only_name():
    with pytest.raises(ProtocolError):
        parse_action(env("identify", {"name": "   ", "player_type": "human"}))


def test_parse_identify_rejects_overlong_name():
    with pytest.raises(ProtocolError):
        parse_action(env("identify", {"name": "x" * 25, "player_type": "human"}))


@pytest.mark.parametrize(
    "bad_name",
    [
        "Ali\nce",  # newline (Cc)
        "Ali\tce",  # tab (Cc)
        "Alice‮",  # right-to-left override (Cf) -- display spoofing
        "Ali​ce",  # zero-width space (Cf)
    ],
)
def test_parse_identify_rejects_control_and_format_chars(bad_name):
    with pytest.raises(ProtocolError):
        parse_action(env("identify", {"name": bad_name, "player_type": "human"}))


def test_parse_identify_allows_plain_unicode_name():
    action = parse_action(env("identify", {"name": "たろう", "player_type": "human"}))
    assert action == Identify(name="たろう", player_type="human")


def test_parse_create_table_defaults():
    action = parse_action(env("create_table", {}))
    assert action == CreateTable()
    assert action.mode == "normal"
    assert action.starting_chips == 25
    assert action.turn_timeout_ai_sec == 10.0


def test_parse_create_table_evaluation_requires_game_count():
    with pytest.raises(ProtocolError):
        parse_action(env("create_table", {"mode": "evaluation"}))
    action = parse_action(env("create_table", {"mode": "evaluation", "game_count": 100}))
    assert action.game_count == 100


def test_parse_create_table_round_limit_requires_round_limit():
    with pytest.raises(ProtocolError):
        parse_action(env("create_table", {"end_condition": "round_limit"}))
    action = parse_action(env("create_table", {"end_condition": "round_limit", "round_limit": 50}))
    assert action.round_limit == 50


def test_parse_create_table_full_payload():
    payload = {
        "mode": "normal",
        "end_condition": "chips_zero",
        "starting_chips": 15,
        "joker_disclosure": "immediate",
        "human_disclosure": "immediate",
        "cat_disclosure": "deferred",
        "horse_house_reveal": True,
        "turn_timeout_human_sec": 20,
        "turn_timeout_ai_sec": 5,
        "cucco_window_timeout_human_sec": 8,
        "cucco_window_timeout_ai_sec": 1,
    }
    action = parse_action(env("create_table", payload))
    assert action.starting_chips == 15
    assert action.joker_disclosure == "immediate"
    assert action.human_disclosure == "immediate"
    assert action.cat_disclosure == "deferred"
    assert action.horse_house_reveal is True
    assert action.turn_timeout_human_sec == 20.0


def test_parse_create_table_bulk_disclosure_sets_all_three_causes():
    action = parse_action(env("create_table", {"disqualified_card_disclosure": "immediate"}))
    assert action.joker_disclosure == "immediate"
    assert action.human_disclosure == "immediate"
    assert action.cat_disclosure == "immediate"


def test_parse_create_table_per_cause_field_overrides_the_bulk_setting():
    action = parse_action(
        env(
            "create_table",
            {"disqualified_card_disclosure": "immediate", "cat_disclosure": "deferred"},
        )
    )
    assert action.joker_disclosure == "immediate"  # from the bulk setting
    assert action.human_disclosure == "immediate"  # from the bulk setting
    assert action.cat_disclosure == "deferred"  # explicit per-cause override wins


def test_parse_create_table_with_neither_bulk_nor_per_cause_defaults_to_deferred():
    action = parse_action(env("create_table", {}))
    assert action.joker_disclosure == "deferred"
    assert action.human_disclosure == "deferred"
    assert action.cat_disclosure == "deferred"


def test_parse_create_table_rejects_an_invalid_disclosure_value():
    with pytest.raises(ProtocolError):
        parse_action(env("create_table", {"disqualified_card_disclosure": "Immediate"}))  # wrong case
    with pytest.raises(ProtocolError):
        parse_action(env("create_table", {"joker_disclosure": "imediate"}))  # typo


def test_parse_join_table():
    action = parse_action(env("join_table", {"room_id": "AB12CD"}))
    assert action == JoinTable(room_id="AB12CD", session_token=None)

    action2 = parse_action(env("join_table", {"room_id": "AB12CD", "session_token": "tok-1"}))
    assert action2.session_token == "tok-1"


def test_parse_join_table_requires_room_id():
    with pytest.raises(ProtocolError):
        parse_action(env("join_table", {}))


def test_parse_no_payload_actions():
    assert parse_action(env("ready")) == Ready()
    assert parse_action(env("dealer_ready")) == DealerReady()
    assert parse_action(env("cambio_declare")) == CambioDeclare()
    assert parse_action(env("no_change_declare")) == NoChangeDeclare()
    assert parse_action(env("cucco_declare")) == CuccoDeclare()


def test_parse_continue_declare():
    assert parse_action(env("continue_declare", {"continue": True})) == ContinueDeclare(continue_playing=True)
    assert parse_action(env("continue_declare", {"continue": False})) == ContinueDeclare(continue_playing=False)


def test_parse_continue_declare_requires_boolean():
    with pytest.raises(ProtocolError):
        parse_action(env("continue_declare", {"continue": "yes"}))
    with pytest.raises(ProtocolError):
        parse_action(env("continue_declare", {}))


def test_parse_action_rejects_unknown_type():
    with pytest.raises(ProtocolError):
        parse_action(env("not_a_real_action"))


def test_create_table_to_config_bridges_all_fields():
    action = CreateTable(
        mode="evaluation",
        game_count=50,
        end_condition="round_limit",
        round_limit=100,
        starting_chips=15,
        joker_disclosure="immediate",
        human_disclosure="deferred",
        cat_disclosure="immediate",
        horse_house_reveal=True,
        turn_timeout_human_sec=20.0,
        turn_timeout_ai_sec=5.0,
        cucco_window_timeout_human_sec=8.0,
        cucco_window_timeout_ai_sec=1.0,
    )
    config = create_table_to_config(action)
    assert config.mode == "evaluation"
    assert config.game_count == 50
    assert config.round_limit == 100
    assert config.starting_chips == 15
    assert config.joker_disclosure == "immediate"
    assert config.human_disclosure == "deferred"
    assert config.cat_disclosure == "immediate"
    assert config.horse_house_reveal is True
    assert config.turn_timeout_ai_sec == 5.0
