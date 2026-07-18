"""Client->server actions (docs/protocol/design.md §"アクション一覧").

Each action is a frozen dataclass. `parse_action(envelope)` looks up the
envelope's `type` in a registry and builds the corresponding dataclass from
its `payload`, raising `ProtocolError` on anything malformed.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Callable, Union

from cucco.domain.config import GameConfig
from cucco.protocol.envelope import Envelope
from cucco.protocol.errors import ProtocolError

VALID_PLAYER_TYPES = ("human", "ai", "spectator")
VALID_MODES = ("normal", "evaluation")
VALID_END_CONDITIONS = ("chips_zero", "round_limit")
VALID_DISCLOSURES = ("immediate", "deferred")
VALID_EFFECT_DECLARATIONS = ("auto", "declared")

# Display names are attacker-controlled and broadcast to every client via
# state_snapshot (seats[].name), so they must be bounded and sanitized at the
# protocol boundary -- the browser client's maxlength=24 doesn't apply to a
# hand-crafted WebSocket client. Rejecting control (Cc) and format (Cf) code
# points blocks display-spoofing (RTL override, zero-width) and stops raw
# clients from smuggling markup/newlines into any viewer that forgets to
# escape. See docs/security-notes.md.
MAX_NAME_LENGTH = 24


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
    result_pause_sec: float = 0.0
    effect_declaration: str = "auto"
    # Server-embedded AI opponents to seat at this table: ((policy, count), ...).
    # Policy-name validity and the total-seats cap are checked in the server
    # layer (dispatch), where the policy registry and seat limits live.
    ai_players: tuple = ()


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
    """Declare クク. Fire-and-forget: a holder may send this at ANY time
    during a deal; the server applies it at the next point outside an atomic
    exchange (dispatch routes it as a pending flag, never as a prompt
    answer). There is no window, no pass, and nothing for the table to wait
    on -- so the game's pacing reveals nothing about who holds クク."""

    pass


@dataclass(frozen=True)
class ContinueDeclare:
    continue_playing: bool


@dataclass(frozen=True)
class StartPot:
    pass


@dataclass(frozen=True)
class ResultAck:
    """The player confirmed the result screen -- once every seated,
    connected player has acked, the result pause ends early."""

    pass


@dataclass(frozen=True)
class EffectDeclare:
    """Declare the special card's effect during an effect_window
    (effect_declaration: "declared" tables only)."""

    pass


@dataclass(frozen=True)
class EffectPass:
    """Stay silent during an effect_window: the effect does not fire and
    the exchange goes through."""

    pass


Action = Union[
    Identify,
    CreateTable,
    JoinTable,
    Ready,
    DealerReady,
    CambioDeclare,
    NoChangeDeclare,
    CuccoDeclare,
    ContinueDeclare,
    StartPot,
    ResultAck,
    EffectDeclare,
    EffectPass,
]


# -- payload validation helpers -------------------------------------------------


def _require_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"'{key}' must be a non-empty string")
    return value


def _require_name(payload: dict) -> str:
    value = payload.get("name")
    if not isinstance(value, str):
        raise ProtocolError("'name' must be a string")
    name = value.strip()
    if not name:
        raise ProtocolError("'name' must be a non-empty string")
    if len(name) > MAX_NAME_LENGTH:
        raise ProtocolError(f"'name' must be at most {MAX_NAME_LENGTH} characters")
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in name):
        raise ProtocolError("'name' must not contain control or formatting characters")
    return name


def folded_name(name: str) -> str:
    """Fold a display name for collision detection (docs/security-notes.md):
    NFKC normalization + casefold so full-width/half-width, other Unicode
    compatibility variants, and letter-case differences of the same name
    collide (e.g. "Ａlice", "ALICE", "alice" all match "Alice"). This raises
    the bar on label impersonation; cross-script homoglyphs (Latin "a" vs
    Cyrillic "а") are NOT caught and remain a documented residual risk."""
    return unicodedata.normalize("NFKC", name).casefold()


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
    name = _require_name(payload)
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
    ai_players = _parse_ai_players(payload)
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
        result_pause_sec=_optional_number(payload, "result_pause_sec", 0.0),
        effect_declaration=_require_choice(payload, "effect_declaration", VALID_EFFECT_DECLARATIONS, default="auto"),
        ai_players=ai_players,
    )


def _parse_ai_players(payload: dict) -> tuple:
    """Shape check for `ai_players`: a list of {"policy": str, "count": int>=1}.
    Policy-name validity and the seats cap are server-layer concerns."""
    raw = payload.get("ai_players")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ProtocolError("'ai_players' must be a list of {policy, count} objects")
    specs = []
    for item in raw:
        if not isinstance(item, dict):
            raise ProtocolError("'ai_players' entries must be objects with 'policy' and 'count'")
        policy = item.get("policy")
        if not isinstance(policy, str) or not policy:
            raise ProtocolError("'ai_players[].policy' must be a non-empty string")
        count = item.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ProtocolError("'ai_players[].count' must be a positive integer")
        specs.append((policy, count))
    return tuple(specs)


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
    "continue_declare": _parse_continue_declare,
    "start_pot": lambda payload: StartPot(),
    "result_ack": lambda payload: ResultAck(),
    "effect_declare": lambda payload: EffectDeclare(),
    "effect_pass": lambda payload: EffectPass(),
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
        result_pause_sec=action.result_pause_sec,
        effect_declaration=action.effect_declaration,
    )
