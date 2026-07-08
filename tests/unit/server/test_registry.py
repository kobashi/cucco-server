import random

from cucco.server.registry import ROOM_ID_ALPHABET, ROOM_ID_LENGTH, TableRegistry


def test_generate_room_id_has_correct_length_and_alphabet():
    registry = TableRegistry(random.Random(0))
    room_id = registry.generate_room_id()
    assert len(room_id) == ROOM_ID_LENGTH
    assert all(c in ROOM_ID_ALPHABET for c in room_id)


def test_alphabet_excludes_confusable_characters():
    for confusable in "01OI":
        assert confusable not in ROOM_ID_ALPHABET


def test_register_and_get_round_trip():
    registry = TableRegistry(random.Random(0))
    table = object()
    room_id = registry.register(table)
    assert registry.get(room_id) is table


def test_get_returns_none_for_unknown_room():
    registry = TableRegistry(random.Random(0))
    assert registry.get("NOPE12") is None


def test_room_ids_are_never_reused():
    # Force a tiny effective search space isn't possible without touching
    # internals, but we can at least confirm repeated registration always
    # yields distinct ids across many calls.
    registry = TableRegistry(random.Random(1))
    ids = {registry.register(object()) for _ in range(200)}
    assert len(ids) == 200


def test_remove_forgets_the_table():
    registry = TableRegistry(random.Random(0))
    room_id = registry.register(object())
    registry.remove(room_id)
    assert registry.get(room_id) is None
