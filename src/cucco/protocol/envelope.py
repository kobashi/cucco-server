"""The wire message envelope (docs/protocol/design.md §"メッセージ形式").

`{type, id?, table_id, protocol_version, payload, ts}` -- used identically
for client->server actions and server->client events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from cucco.domain.timeutil import now_iso
from cucco.protocol.errors import ProtocolError

PROTOCOL_VERSION = "1.0"


@dataclass(frozen=True)
class Envelope:
    type: str
    payload: dict
    table_id: str | None = None
    id: str | None = None
    protocol_version: str = PROTOCOL_VERSION
    ts: str = field(default_factory=now_iso)


def parse_envelope(raw: str) -> Envelope:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ProtocolError("envelope must be a JSON object")
    type_ = data.get("type")
    if not isinstance(type_, str) or not type_:
        raise ProtocolError("envelope missing a non-empty 'type'")
    payload = data.get("payload", {})
    if not isinstance(payload, dict):
        raise ProtocolError("envelope 'payload' must be an object")
    return Envelope(
        type=type_,
        payload=payload,
        table_id=data.get("table_id"),
        id=data.get("id"),
        protocol_version=data.get("protocol_version", ""),
        ts=data.get("ts") or now_iso(),
    )


def check_protocol_version(envelope: Envelope) -> None:
    if envelope.protocol_version != PROTOCOL_VERSION:
        raise ProtocolError(
            f"protocol_version mismatch: server is {PROTOCOL_VERSION!r}, "
            f"client sent {envelope.protocol_version!r}"
        )


def build_envelope(
    type_: str,
    payload: dict,
    *,
    table_id: str | None = None,
    id_: str | None = None,
) -> str:
    return json.dumps(
        {
            "type": type_,
            "id": id_,
            "table_id": table_id,
            "protocol_version": PROTOCOL_VERSION,
            "payload": payload,
            "ts": now_iso(),
        }
    )
