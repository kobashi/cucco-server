import json

import pytest

from cucco.protocol.envelope import PROTOCOL_VERSION, build_envelope, check_protocol_version, parse_envelope
from cucco.protocol.errors import ProtocolError


def test_parse_envelope_round_trips_basic_fields():
    raw = json.dumps(
        {
            "type": "ready",
            "table_id": "ABC123",
            "protocol_version": "1.0",
            "payload": {},
            "id": "msg-1",
        }
    )
    envelope = parse_envelope(raw)
    assert envelope.type == "ready"
    assert envelope.table_id == "ABC123"
    assert envelope.protocol_version == "1.0"
    assert envelope.payload == {}
    assert envelope.id == "msg-1"


def test_parse_envelope_fills_in_missing_optional_fields():
    envelope = parse_envelope(json.dumps({"type": "ready"}))
    assert envelope.payload == {}
    assert envelope.table_id is None
    assert envelope.id is None
    assert envelope.ts


def test_parse_envelope_rejects_invalid_json():
    with pytest.raises(ProtocolError):
        parse_envelope("not json")


def test_parse_envelope_rejects_non_object_json():
    with pytest.raises(ProtocolError):
        parse_envelope(json.dumps([1, 2, 3]))


def test_parse_envelope_rejects_missing_type():
    with pytest.raises(ProtocolError):
        parse_envelope(json.dumps({"payload": {}}))


def test_parse_envelope_rejects_non_object_payload():
    with pytest.raises(ProtocolError):
        parse_envelope(json.dumps({"type": "ready", "payload": "nope"}))


def test_check_protocol_version_accepts_matching_version():
    envelope = parse_envelope(json.dumps({"type": "ready", "protocol_version": PROTOCOL_VERSION}))
    check_protocol_version(envelope)  # should not raise


def test_check_protocol_version_rejects_mismatch():
    envelope = parse_envelope(json.dumps({"type": "ready", "protocol_version": "0.9"}))
    with pytest.raises(ProtocolError):
        check_protocol_version(envelope)


def test_build_envelope_produces_parseable_json_with_all_fields():
    raw = build_envelope("state_snapshot", {"foo": "bar"}, table_id="ABC123", id_="evt-1")
    data = json.loads(raw)
    assert data["type"] == "state_snapshot"
    assert data["table_id"] == "ABC123"
    assert data["id"] == "evt-1"
    assert data["protocol_version"] == PROTOCOL_VERSION
    assert data["payload"] == {"foo": "bar"}
    assert "ts" in data
