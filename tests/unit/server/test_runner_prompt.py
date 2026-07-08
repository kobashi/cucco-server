import asyncio
import json

import pytest

from cucco.domain.config import GameConfig
from cucco.protocol.actions import CambioDeclare, CuccoDeclare, CuccoPass, NoChangeDeclare
from cucco.server.runner import TableRunner
from cucco.server.session import PlayerSession
from cucco.server.table import Table


class FakeConnection:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


def make_table() -> Table:
    return Table(room_id="ABC123", config=GameConfig(turn_timeout_ai_sec=0.2, cucco_window_timeout_ai_sec=0.1), creator_id="p1")


@pytest.mark.asyncio
async def test_stale_action_from_a_previous_prompt_is_drained_not_misapplied():
    table = make_table()
    session = PlayerSession(player_id="p1", name="Bot", player_type="ai", session_token="t", connection=FakeConnection())
    table.add_session(session)
    runner = TableRunner(table)

    # Simulate a late cucco_pass arriving after a previous (already-closed)
    # cucco_window -- it must NOT be consumed by this unrelated turn prompt.
    session.inbox.put_nowait(CuccoPass())

    async def respond_after_delay():
        await asyncio.sleep(0.05)
        session.inbox.put_nowait(NoChangeDeclare())

    task = asyncio.create_task(respond_after_delay())
    action = await runner._prompt(session, "turn", (CambioDeclare, NoChangeDeclare))
    await task

    assert isinstance(action, NoChangeDeclare)


@pytest.mark.asyncio
async def test_wrong_type_response_is_rejected_and_wait_continues():
    table = make_table()
    conn = FakeConnection()
    session = PlayerSession(player_id="p1", name="Bot", player_type="ai", session_token="t", connection=conn)
    table.add_session(session)
    runner = TableRunner(table)

    async def send_wrong_then_right():
        await asyncio.sleep(0.02)
        session.inbox.put_nowait(CuccoDeclare())  # wrong type for a turn prompt
        await asyncio.sleep(0.02)
        session.inbox.put_nowait(CambioDeclare())

    task = asyncio.create_task(send_wrong_then_right())
    action = await runner._prompt(session, "turn", (CambioDeclare, NoChangeDeclare))
    await task

    assert isinstance(action, CambioDeclare)
    rejected = [m for m in conn.sent if m["type"] == "action_rejected"]
    assert len(rejected) == 1


@pytest.mark.asyncio
async def test_prompt_returns_none_on_timeout_with_no_response():
    table = make_table()
    session = PlayerSession(player_id="p1", name="Bot", player_type="ai", session_token="t", connection=FakeConnection())
    table.add_session(session)
    runner = TableRunner(table)

    action = await runner._prompt(session, "turn", (CambioDeclare, NoChangeDeclare))
    assert action is None
