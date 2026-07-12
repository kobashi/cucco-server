"""Per-prompt-type timeout durations (docs/protocol/design.md §"タイムアウト・
不正操作・切断"). The clock-start point for each prompt type is handled by
the caller (runner.py) sending the prompt and immediately starting the
wait -- this module only answers "how long."
"""

from __future__ import annotations

from cucco.domain.config import GameConfig

# `ready`, `dealer_ready`, and `continue_prompt` reuse the turn timeout --
# design.md's create_table payload defines no separate duration for them.
PromptType = str  # "turn" | "cucco_window" | "ready" | "dealer_ready" | "continue"


def timeout_for(config: GameConfig, prompt_type: PromptType, player_type: str) -> float:
    is_human = player_type == "human"
    if prompt_type in ("cucco_window", "effect_window"):
        # effect_window is the same interrupt-style snap decision as a cucco
        # window, so it shares those timeouts rather than adding two more
        # config knobs.
        return config.cucco_window_timeout_human_sec if is_human else config.cucco_window_timeout_ai_sec
    if prompt_type in ("turn", "ready", "dealer_ready", "continue"):
        return config.turn_timeout_human_sec if is_human else config.turn_timeout_ai_sec
    raise ValueError(f"unknown prompt_type: {prompt_type!r}")
