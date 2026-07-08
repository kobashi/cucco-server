class ProtocolError(Exception):
    """A malformed or otherwise invalid message at the wire-protocol level
    (bad JSON, missing fields, wrong types, version mismatch). The server
    layer maps this to an `action_rejected` event, same as a domain-level
    `IllegalAction`."""
