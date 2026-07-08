"""Table registry: プレイルームID issuance and lookup.

6-character alphanumeric codes excluding confusable characters (0/O, 1/I),
freshly issued per table and never reused for the life of the process
(docs/protocol/design.md §"初期パラメータ").
"""

from __future__ import annotations

import random
import string

ROOM_ID_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits if c not in "01OI")
ROOM_ID_LENGTH = 6

MAX_PLAYERS_PER_TABLE = 15
MAX_SPECTATORS_PER_TABLE = 30


class TableRegistry:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._tables: dict[str, object] = {}

    def generate_room_id(self) -> str:
        while True:
            candidate = "".join(self._rng.choice(ROOM_ID_ALPHABET) for _ in range(ROOM_ID_LENGTH))
            if candidate not in self._tables:
                return candidate

    def register(self, table: object) -> str:
        room_id = self.generate_room_id()
        self._tables[room_id] = table
        return room_id

    def get(self, room_id: str) -> object | None:
        return self._tables.get(room_id)

    def remove(self, room_id: str) -> None:
        self._tables.pop(room_id, None)
