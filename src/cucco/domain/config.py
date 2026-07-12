"""Game configuration, mirroring the `create_table` payload (docs/protocol/design.md)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DisqualifiedCardDisclosure = Literal["immediate", "deferred"]
EndCondition = Literal["chips_zero", "round_limit"]
TableMode = Literal["normal", "evaluation"]
# "auto": special-card refusals fire by themselves (base rules).
# "declared": 人間/馬/猫/家 only take effect if their holder actively
# declares when asked to exchange (like クク); silence means the exchange
# succeeds. 道化 stays automatic, and deck-drawn specials always auto-fire
# (the deck has nobody to declare for it).
EffectDeclaration = Literal["auto", "declared"]

# Upper bound on any per-prompt timeout. A table creator's config is
# attacker-controlled input (docs/security-notes.md); an unbounded timeout
# lets a griefer set a multi-hour deadline that wedges everyone else at the
# table on a single prompt. One hour is far beyond any legitimate human turn.
MAX_TIMEOUT_SEC = 3600.0


@dataclass(frozen=True)
class GameConfig:
    mode: TableMode = "normal"
    game_count: int | None = None  # evaluation mode only
    end_condition: EndCondition = "chips_zero"
    round_limit: int | None = None  # required if end_condition == "round_limit"
    starting_chips: int = 25
    # Per-cause disqualified-card disclosure timing (docs/rules/final_rules.md
    # 「設定可能なルール」). Independently selectable per table: e.g. reveal a
    # 道化-caused disqualification immediately but keep 猫-caused ones hidden
    # until the deal opens.
    joker_disclosure: DisqualifiedCardDisclosure = "deferred"
    human_disclosure: DisqualifiedCardDisclosure = "deferred"
    cat_disclosure: DisqualifiedCardDisclosure = "deferred"
    horse_house_reveal: bool = False
    turn_timeout_human_sec: float = 30.0
    turn_timeout_ai_sec: float = 10.0
    cucco_window_timeout_human_sec: float = 10.0
    cucco_window_timeout_ai_sec: float = 2.0
    # Reading pause after deal_opened (before continue prompts) and after
    # pot_result (before the next pot) so humans get a moment to review the
    # result before the game moves on. 0 = no pause (the server otherwise
    # proceeds immediately); ignored in evaluation mode.
    result_pause_sec: float = 0.0
    effect_declaration: EffectDeclaration = "auto"

    def __post_init__(self) -> None:
        if self.end_condition == "round_limit" and self.round_limit is None:
            raise ValueError("round_limit is required when end_condition is 'round_limit'")
        if self.mode == "evaluation" and self.game_count is None:
            raise ValueError("game_count is required when mode is 'evaluation'")
        if self.game_count is not None and self.game_count <= 0:
            raise ValueError("game_count must be a positive integer")
        # Numeric bounds on attacker-controlled create_table fields
        # (docs/security-notes.md): reject values that would produce a broken
        # or grief-inducing game rather than letting them reach the engine.
        if self.starting_chips < 1:
            raise ValueError("starting_chips must be a positive integer")
        if self.round_limit is not None and self.round_limit < 1:
            raise ValueError("round_limit must be a positive integer")
        for field_name in (
            "turn_timeout_human_sec",
            "turn_timeout_ai_sec",
            "cucco_window_timeout_human_sec",
            "cucco_window_timeout_ai_sec",
        ):
            value = getattr(self, field_name)
            if not (0 < value <= MAX_TIMEOUT_SEC):
                raise ValueError(f"{field_name} must be between 0 and {MAX_TIMEOUT_SEC} seconds")
        if not (0 <= self.result_pause_sec <= 60):
            raise ValueError("result_pause_sec must be between 0 and 60 seconds")
