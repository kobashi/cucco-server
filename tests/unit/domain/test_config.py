"""Range validation on attacker-controlled create_table fields
(docs/security-notes.md #5). GameConfig is where the wire payload lands, so
these bounds are the last line before the numbers reach the engine."""

import pytest

from cucco.domain.config import (
    MAX_GAME_COUNT,
    MAX_ROUND_LIMIT,
    MAX_STARTING_CHIPS,
    MAX_TIMEOUT_SEC,
    GameConfig,
)


def test_defaults_are_valid():
    GameConfig()  # must not raise


def test_rejects_non_positive_starting_chips():
    for bad in (0, -5):
        with pytest.raises(ValueError):
            GameConfig(starting_chips=bad)


def test_allows_minimum_starting_chips():
    assert GameConfig(starting_chips=1).starting_chips == 1


def test_rejects_non_positive_round_limit():
    with pytest.raises(ValueError):
        GameConfig(end_condition="round_limit", round_limit=0)


@pytest.mark.parametrize(
    "field",
    [
        "turn_timeout_human_sec",
        "turn_timeout_ai_sec",
        "cucco_window_timeout_human_sec",
        "cucco_window_timeout_ai_sec",
    ],
)
def test_rejects_out_of_range_timeouts(field):
    with pytest.raises(ValueError):
        GameConfig(**{field: 0})  # zero -> instant timeout
    with pytest.raises(ValueError):
        GameConfig(**{field: -1})
    with pytest.raises(ValueError):
        GameConfig(**{field: MAX_TIMEOUT_SEC + 1})  # grief-length deadline


def test_allows_small_positive_timeouts_used_by_evaluation():
    # Evaluation/e2e runs legitimately use sub-second timeouts.
    GameConfig(turn_timeout_ai_sec=0.02, cucco_window_timeout_ai_sec=0.02)


# The counts below are attacker-controlled too: a single well-formed
# create_table asking for 10**12 games or rounds would pin a task for the life
# of the process, which no amount of network-level rate limiting can undo.


def test_rejects_unbounded_game_count():
    GameConfig(mode="evaluation", game_count=MAX_GAME_COUNT)  # the cap itself is fine
    with pytest.raises(ValueError):
        GameConfig(mode="evaluation", game_count=MAX_GAME_COUNT + 1)
    with pytest.raises(ValueError):
        GameConfig(mode="evaluation", game_count=10**12)
    with pytest.raises(ValueError):
        GameConfig(mode="evaluation", game_count=0)


def test_rejects_unbounded_round_limit():
    GameConfig(end_condition="round_limit", round_limit=MAX_ROUND_LIMIT)
    with pytest.raises(ValueError):
        GameConfig(end_condition="round_limit", round_limit=MAX_ROUND_LIMIT + 1)
    with pytest.raises(ValueError):
        GameConfig(end_condition="round_limit", round_limit=10**12)


def test_rejects_unbounded_starting_chips():
    GameConfig(starting_chips=MAX_STARTING_CHIPS)
    with pytest.raises(ValueError):
        GameConfig(starting_chips=MAX_STARTING_CHIPS + 1)
    with pytest.raises(ValueError):
        GameConfig(starting_chips=10**100)


def test_ordinary_table_settings_still_pass():
    # The caps must not get in the way of how the seminar actually plays.
    GameConfig(end_condition="round_limit", round_limit=20, starting_chips=25)
    GameConfig(mode="evaluation", game_count=100)
