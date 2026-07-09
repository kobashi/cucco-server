"""Client->server actions (docs/protocol/design.md §"アクション一覧").

Each action is a frozen dataclass. `parse_action(envelope)` looks up the
envelope's `type` in a registry and builds the corresponding dataclass from
its `payload`, raising `ProtocolError` on anything malformed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

from cucco.domain.config import GameConfig
from cucco.protocol.envelope import Envelope
from cucco.protocol.errors import ProtocolError

VALID_PLAYER_TYPES = ("human", "ai", "spectator")
VALID_MODES = ("normal", "evaluation")
VALID_END_CONDITIONS = ("chips_zero", "round_limit")
VALID_DISCLOSURES = ("immediate", "deferred")


@dataclass(frozen=True)
class Identify:
    name: str
    player_type: str  # "human" | "ai" | "spectator"


@dataclass(frozen=True)
class CreateTable:
    mode: str = "normal"
    game_count: int | None = None
    end_condition: str = "chips_zero"
    round_limit: int | None = None
    starting_chips: int = 25
    joker_disclosure: str = "deferred"
    human_disclosure: str = "deferred"
    cat_disclosure: str = "deferred"
    horse_house_reveal: bool = False
    turn_timeout_human_sec: float = 30.0
    turn_timeout_ai_sec: float = 10.0
    cucco_window_timeout_human_sec: float = 10.0
    cucco_window_timeout_ai_sec: float = 2.0


@dataclass(frozen=True)
class JoinTable:
    room_id: str
    session_token: str | None = None


@dataclass(frozen=True)
class Ready:
    pass


@dataclass(frozen=True)
class DealerReady:
    pass


@dataclass(frozen=True)
class CambioDeclare:
    pass


@dataclass(frozen=True)
class NoChangeDeclare:
    pass


@dataclass(frozen=True)
class CuccoDeclare:
    pass


@dataclass(frozen=True)
class CuccoPass:
    pass


@dataclass(frozen=True)
class ContinueDeclare:
    continue_playing: bool


Action = Union[
    Identify,
    CreateTable,
    JoinTable,
    Ready,
    DealerReady,
    CambioDeclare,
    NoChangeDeclare,
    CuccoDeclare,
    CuccoPass,
    ContinueDeclare,
]


# -- payload validation helpers -------------------------------------------------


def _require_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"'{key}' must be a non-empty string")
    return value


def _require_bool(payload: dict, key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ProtocolError(f"'{key}' must be a boolean")
    return value


def _require_choice(payload: dict, key: str, choices: tuple[str, ...], default: str | None = None) -> str:
    value = payload.get(key, default)
    if value not in choices:
        raise ProtocolError(f"'{key}' must be one of {choices}, got {value!r}")
    return value


def _optional_int(payload: dict, key: str, default: int | None = None) -> int | None:
    if key not in payload or payload[key] is None:
        return default
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProtocolError(f"'{key}' must be an integer")
    return value


def _optional_number(payload: dict, key: str, default: float) -> float:
    if key not in payload or payload[key] is None:
        return default
    value = payload[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ProtocolError(f"'{key}' must be a number")
    return float(value)


# -- per-type parsers -------------------------------------------------------------


def _parse_identify(payload: dict) -> Identify:
    name = _require_str(payload, "name")
    player_type = _require_choice(payload, "player_type", VALID_PLAYER_TYPES)
    return Identify(name=name, player_type=player_type)


def _parse_create_table(payload: dict) -> CreateTable:
    mode = _require_choice(payload, "mode", VALID_MODES, default="normal")
    game_count = _optional_int(payload, "game_count")
    if mode == "evaluation" and game_count is None:
        raise ProtocolError("'game_count' is required when mode is 'evaluation'")
    end_condition = _require_choice(payload, "end_condition", VALID_END_CONDITIONS, default="chips_zero")
    round_limit = _optional_int(payload, "round_limit")
    if end_condition == "round_limit" and round_limit is None:
        raise ProtocolError("'round_limit' is required when end_condition is 'round_limit'")
    starting_chips = _optional_int(payload, "starting_chips", default=25)
    # Per-cause disqualified-card disclosure timing (docs/rules/final_rules.md
    # 「設定可能なルール」) -- independently selectable per table, but
    # `disqualified_card_disclosure` sets all three at once as a shorthand
    # for the common case of not needing per-cause granularity. A per-cause
    # field, if present, overrides the bulk value for that cause only.
    bulk_disclosure = _require_choice(payload, "disqualified_card_disclosure", VALID_DISCLOSURES, default="deferred")
    joker_disclosure = _require_choice(payload, "joker_disclosure", VALID_DISCLOSURES, default=bulk_disclosure)
    human_disclosure = _require_choice(payload, "human_disclosure", VALID_DISCLOSURES, default=bulk_disclosure)
    cat_disclosure = _require_choice(payload, "cat_disclosure", VALID_DISCLOSURES, default=bulk_disclosure)
    horse_house_reveal = payload.get("horse_house_reveal", False)
    if not isinstance(horse_house_reveal, bool):
        raise ProtocolError("'horse_house_reveal' must be a boolean")
    return CreateTable(
        mode=mode,
        game_count=game_count,
        end_condition=end_condition,
        round_limit=round_limit,
        starting_chips=starting_chips,
        joker_disclosure=joker_disclosure,
        human_disclosure=human_disclosure,
        cat_disclosure=cat_disclosure,
        horse_house_reveal=horse_house_reveal,
        turn_timeout_human_sec=_optional_number(payload, "turn_timeout_human_sec", 30.0),
        turn_timeout_ai_sec=_optional_number(payload, "turn_timeout_ai_sec", 10.0),
        cucco_window_timeout_human_sec=_optional_number(payload, "cucco_window_timeout_human_sec", 10.0),
        cucco_window_timeout_ai_sec=_optional_number(payload, "cucco_window_timeout_ai_sec", 2.0),
    )


def _parse_join_table(payload: dict) -> JoinTable:
    room_id = _require_str(payload, "room_id")
    session_token = payload.get("session_token")
    if session_token is not None and not isinstance(session_token, str):
        raise ProtocolError("'session_token' must be a string if present")
    return JoinTable(room_id=room_id, session_token=session_token)


def _parse_continue_declare(payload: dict) -> ContinueDeclare:
    return ContinueDeclare(continue_playing=_require_bool(payload, "continue"))


_PARSERS: dict[str, Callable[[dict], Action]] = {
    "identify": _parse_identify,
    "create_table": _parse_create_table,
    "join_table": _parse_join_table,
    "ready": lambda payload: Ready(),
    "dealer_ready": lambda payload: DealerReady(),
    "cambio_declare": lambda payload: CambioDeclare(),
    "no_change_declare": lambda payload: NoChangeDeclare(),
    "cucco_declare": lambda payload: CuccoDeclare(),
    "cucco_pass": lambda payload: CuccoPass(),
    "continue_declare": _parse_continue_declare,
}


def parse_action(envelope: Envelope) -> Action:
    parser = _PARSERS.get(envelope.type)
    if parser is None:
        raise ProtocolError(f"unknown action type: {envelope.type!r}")
    return parser(envelope.payload)


def create_table_to_config(action: CreateTable) -> GameConfig:
    """Bridge from the wire `create_table` action to the domain `GameConfig`."""
    return GameConfig(
        mode=action.mode,
        game_count=action.game_count,
        end_condition=action.end_condition,
        round_limit=action.round_limit,
        starting_chips=action.starting_chips,
        joker_disclosure=action.joker_disclosure,
        human_disclosure=action.human_disclosure,
        cat_disclosure=action.cat_disclosure,
        horse_house_reveal=action.horse_house_reveal,
        turn_timeout_human_sec=action.turn_timeout_human_sec,
        turn_timeout_ai_sec=action.turn_timeout_ai_sec,
        cucco_window_timeout_human_sec=action.cucco_window_timeout_human_sec,
        cucco_window_timeout_ai_sec=action.cucco_window_timeout_ai_sec,
    )
