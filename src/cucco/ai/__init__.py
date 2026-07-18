"""Server-embedded AI players.

The decision policies and the event-driven bot brain live here so the
in-process bots spawned via `create_table`'s `ai_players` and the external
reference client (`clients/mock_ai/`) share ONE implementation. The
external client keeps its historical import paths through thin re-export
shims in `clients/mock_ai/` -- seminar students' code and the guides keep
working unchanged.
"""
