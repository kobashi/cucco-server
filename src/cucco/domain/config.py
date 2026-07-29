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
# Upper bounds on the remaining attacker-controlled create_table numbers. The
# lower bounds below already rejected 0 and negatives, but nothing capped the
# top end: a single well-formed create_table could ask for 10**12 games or
# rounds and the table would then run essentially forever, pinning a task for
# the life of the process. That is work the network layer cannot rate-limit
# away -- it is one legitimate request -- so it has to be bounded here.
# Generous next to real use (the seminar runs tens of games, tens of rounds).
MAX_GAME_COUNT = 10_000
MAX_ROUND_LIMIT = 10_000
MAX_STARTING_CHIPS = 10_000


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
    # 山札の再構成に何を含めるか (docs/rules/final_rules.md 「設定可能なルール」).
    # False (既定): 捨て札だけを再構成する。途中失格者の表向きの札は、そのディールが
    # オープンするまで場に残るので、この再構成には含まれない。
    # True: 山札が尽きて引く必要が生じた時点で、その表向きの札も捨て札に混ぜてから
    # 再構成する -- 物理的な卓で場のカードをかき集めるのに近い。
    reshuffle_includes_revealed: bool = False
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
        if self.game_count is not None and not (0 < self.game_count <= MAX_GAME_COUNT):
            raise ValueError(f"game_count must be between 1 and {MAX_GAME_COUNT}")
        # Numeric bounds on attacker-controlled create_table fields
        # (docs/security-notes.md): reject values that would produce a broken
        # or grief-inducing game rather than letting them reach the engine.
        if not (1 <= self.starting_chips <= MAX_STARTING_CHIPS):
            raise ValueError(f"starting_chips must be between 1 and {MAX_STARTING_CHIPS}")
        if self.round_limit is not None and not (1 <= self.round_limit <= MAX_ROUND_LIMIT):
            raise ValueError(f"round_limit must be between 1 and {MAX_ROUND_LIMIT}")
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
