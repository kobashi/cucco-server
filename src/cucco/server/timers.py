"""Per-prompt-type timeout durations (docs/protocol/design.md §"タイムアウト・
不正操作・切断"). The clock-start point for each prompt type is handled by
the caller (runner.py) sending the prompt and immediately starting the
wait -- this module only answers "how long."
"""

from __future__ import annotations

from cucco.domain.config import GameConfig

# `dealer_ready` and `continue_prompt` reuse the turn timeout -- design.md's
# create_table payload defines no separate duration for them. `ready` is not
# a prompt at all (the lobby watchdog in dispatch.py handles game start), and
# クク declarations are fire-and-forget (no prompt, no timeout of their own).
PromptType = str  # "turn" | "effect_window" | "dealer_ready" | "continue"


def timeout_for(config: GameConfig, prompt_type: PromptType, player_type: str) -> float:
    is_human = player_type == "human"
    if prompt_type == "effect_window":
        # The interrupt-style snap decision of declared-effects tables. Reuses
        # the cucco_window_timeout_* knobs; the name is historical (クク no
        # longer has a window of its own).
        return config.cucco_window_timeout_human_sec if is_human else config.cucco_window_timeout_ai_sec
    if prompt_type in ("turn", "dealer_ready", "continue"):
        return config.turn_timeout_human_sec if is_human else config.turn_timeout_ai_sec
    raise ValueError(f"unknown prompt_type: {prompt_type!r}")
