"""Game configuration, mirroring the `create_table` payload (docs/protocol/design.md)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DisqualifiedCardDisclosure = Literal["immediate", "deferred"]
EndCondition = Literal["chips_zero", "round_limit"]
TableMode = Literal["normal", "evaluation"]


@dataclass(frozen=True)
class GameConfig:
    mode: TableMode = "normal"
    game_count: int | None = None  # evaluation mode only
    end_condition: EndCondition = "chips_zero"
    round_limit: int | None = None  # required if end_condition == "round_limit"
    starting_chips: int = 25
    disqualified_card_disclosure: DisqualifiedCardDisclosure = "deferred"
    horse_house_reveal: bool = False
    turn_timeout_human_sec: float = 30.0
    turn_timeout_ai_sec: float = 10.0
    cucco_window_timeout_human_sec: float = 10.0
    cucco_window_timeout_ai_sec: float = 2.0

    def __post_init__(self) -> None:
        if self.end_condition == "round_limit" and self.round_limit is None:
            raise ValueError("round_limit is required when end_condition is 'round_limit'")
        if self.mode == "evaluation" and self.game_count is None:
            raise ValueError("game_count is required when mode is 'evaluation'")
        if self.game_count is not None and self.game_count <= 0:
            raise ValueError("game_count must be a positive integer")
