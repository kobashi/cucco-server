import pytest

from cucco.server.session import PlayerSession


class FakeConnection:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)


@pytest.mark.asyncio
async def test_send_forwards_to_connection():
    conn = FakeConnection()
    session = PlayerSession(player_id="p1", name="Alice", player_type="human", session_token="tok", connection=conn)
    await session.send("hello")
    assert conn.sent == ["hello"]


@pytest.mark.asyncio
async def test_send_is_noop_when_disconnected():
    conn = FakeConnection()
    session = PlayerSession(
        player_id="p1", name="Alice", player_type="human", session_token="tok", connection=conn, connected=False
    )
    await session.send("hello")
    assert conn.sent == []


@pytest.mark.asyncio
async def test_send_is_noop_with_no_connection():
    session = PlayerSession(player_id="p1", name="Alice", player_type="ai", session_token="tok")
    await session.send("hello")  # should not raise


def test_is_ai_and_is_spectator():
    ai = PlayerSession(player_id="p1", name="Bot", player_type="ai", session_token="t")
    spectator = PlayerSession(player_id="p2", name="Watcher", player_type="spectator", session_token="t")
    human = PlayerSession(player_id="p3", name="Human", player_type="human", session_token="t")
    assert ai.is_ai() and not ai.is_spectator()
    assert spectator.is_spectator() and not spectator.is_ai()
    assert not human.is_ai() and not human.is_spectator()


def test_each_session_gets_its_own_inbox():
    a = PlayerSession(player_id="p1", name="A", player_type="ai", session_token="t")
    b = PlayerSession(player_id="p2", name="B", player_type="ai", session_token="t")
    assert a.inbox is not b.inbox
