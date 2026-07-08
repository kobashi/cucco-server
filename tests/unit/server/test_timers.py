import pytest

from cucco.domain.config import GameConfig
from cucco.server.timers import timeout_for


def test_turn_timeout_uses_player_type():
    config = GameConfig(turn_timeout_human_sec=30.0, turn_timeout_ai_sec=10.0)
    assert timeout_for(config, "turn", "human") == 30.0
    assert timeout_for(config, "turn", "ai") == 10.0


def test_cucco_window_timeout_uses_player_type():
    config = GameConfig(cucco_window_timeout_human_sec=10.0, cucco_window_timeout_ai_sec=2.0)
    assert timeout_for(config, "cucco_window", "human") == 10.0
    assert timeout_for(config, "cucco_window", "ai") == 2.0


@pytest.mark.parametrize("prompt_type", ["ready", "dealer_ready", "continue"])
def test_non_turn_prompts_reuse_the_turn_timeout(prompt_type):
    config = GameConfig(turn_timeout_human_sec=30.0, turn_timeout_ai_sec=10.0)
    assert timeout_for(config, prompt_type, "human") == 30.0
    assert timeout_for(config, prompt_type, "ai") == 10.0


def test_unknown_prompt_type_raises():
    with pytest.raises(ValueError):
        timeout_for(GameConfig(), "not_a_prompt", "human")
