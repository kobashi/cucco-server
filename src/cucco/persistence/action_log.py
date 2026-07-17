"""Per-game JSON Lines action log (docs/protocol/design.md 「永続化・成績記録」).

Records the deck's shuffle seed plus every domain event and raw client
action in chronological order, so a game can be replayed deterministically
later, for replay and AI strategy analysis.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path

from cucco.domain.timeutil import now_iso

logger = logging.getLogger("cucco.persistence.action_log")


def _serialize(value):
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _serialize(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted(_serialize(v) for v in value)
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value


class ActionLogWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation, not "w": the caller is expected to give every
        # game a unique filename. If two games ever collided on one path,
        # silently truncating (`"w"`) would destroy the earlier game's
        # already-recorded log instead of failing loudly.
        self._file = path.open("x", encoding="utf-8")

    def write_seed(self, seed: int) -> None:
        self._write({"kind": "seed", "seed": seed})

    def write_action(self, player_id: str, action_type: str, payload: dict | None = None) -> None:
        self._write({"kind": "action", "player_id": player_id, "action_type": action_type, "payload": payload or {}})

    def write_event(self, event: object) -> None:
        self._write({"kind": "event", "event_type": type(event).__name__, "payload": _serialize(event)})

    def _write(self, record: dict) -> None:
        record["ts"] = now_iso()
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def open_for_game(action_log_dir: Path, room_id: str) -> ActionLogWriter | None:
    """Best-effort: open a uniquely-named per-game log under
    `action_log_dir`. Returns None (after logging why) instead of raising
    -- persistence is server-internal (docs/protocol/design.md), so a
    directory permission problem or full disk must never prevent a game
    from starting. The uuid suffix (not just room_id) is required: a
    room_id is only unique for one process's lifetime, and both a
    restarted process and evaluation mode's multi-game-per-table loop
    would otherwise collide on the same filename."""
    try:
        return ActionLogWriter(action_log_dir / f"{room_id}-{uuid.uuid4().hex}.jsonl")
    except OSError:
        logger.exception("failed to open action log under %s for table %s -- continuing without it", action_log_dir, room_id)
        return None
