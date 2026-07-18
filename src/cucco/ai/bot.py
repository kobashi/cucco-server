"""The event-driven AI brain (docs/ai-client-guide.md).

A passive loop: waits for server notifications and answers the decision
points (turn / continue / dealer_ready / effect window), delegating the
actual choices to a policy from `cucco.ai.policies`. クク is fire-and-forget
(`cucco_declare` may be sent at any moment); this bot only declares it at
its own prompts (turn / dealer_ready), which is a legal simplification --
declaring is always optional.

The brain is transport-agnostic: `conn` is anything with
`send(type, payload)`, `events()` (async iterator of objects with
`.type`/`.payload`), and `player_id` -- satisfied both by the external
WebSocket client (`clients.common.ws_client.CuccoConnection`) and by the
server's in-process loopback (`cucco.server.bots`). Used as the reference
implementation for seminar students' own AI clients and as the engine of
the server-embedded `ai_players` bots.
"""

from __future__ import annotations

from dataclasses import dataclass

from cucco.ai.policies import BasePolicy


@dataclass
class BotEvent:
    """A server event as the brain sees it. Structurally identical to
    `clients.common.ws_client.ServerEvent` -- the brain only reads
    `.type` and `.payload`, so either works."""

    type: str
    payload: dict
    table_id: str | None = None


class MockAI:
    """Plays one table to its end (game_ended, or evaluation_summary in
    evaluation mode) over an already-identified, already-joined connection."""

    def __init__(self, conn, policy: BasePolicy, *, mode: str = "normal", log=None) -> None:
        self.conn = conn
        self.policy = policy
        self.mode = mode
        self.log = log  # callable(str) or None
        # State per docs/ai-client-guide.md §3.
        self.my_hand: str | None = None
        self.my_chips: int = 0
        self.pot_active: set[str] = set()
        self.deal_alive: set[str] = set()
        self.received: list = []

    def _info(self, message: str) -> None:
        if self.log is not None:
            self.log(message)

    @property
    def alive_count(self) -> int:
        return len(self.deal_alive) if self.deal_alive else max(len(self.pot_active), 2)

    async def play(self) -> dict:
        await self.conn.send("ready", {})
        async for event in self.conn.events():
            self.received.append(event)
            result = await self._handle(event)
            if result is not None:
                return result
        raise RuntimeError("connection closed before the game ended")

    async def _handle(self, event) -> dict | None:
        p = event.payload
        me = self.conn.player_id

        if event.type == "pot_started":
            self.pot_active = set(p["participants"])
            self.my_chips = p["chips_now"].get(me, self.my_chips)
        elif event.type == "player_left_pot":
            self.pot_active.discard(p["player_id"])
            self.deal_alive.discard(p["player_id"])
        elif event.type == "deal_started":
            self.my_hand = p.get("your_hand")
            self.deal_alive = set(self.pot_active)
        elif event.type == "player_disqualified":
            self.deal_alive.discard(p["player_id"])
            if p["player_id"] == me:
                self.my_hand = None
        elif event.type == "exchange_result":
            # Private fields are merged in only for the involved parties.
            if "your_new_card" in p:
                self.my_hand = p["your_new_card"]
            elif p.get("actor") == me and "new_card" in p:
                self.my_hand = p["new_card"]
        elif event.type == "deal_result":
            self.my_chips = p["chips_now"].get(me, self.my_chips)

        elif event.type == "dealer_ready":
            # A クク-holding dealer may declare it together with "dōzo".
            if self.my_hand == "クク" and self.policy.decide_cucco_declare(self.my_hand, self.alive_count):
                await self.conn.send("cucco_declare", {})
                self._info("dealer_ready: declaring クク")
            else:
                await self.conn.send("dealer_ready", {})
        elif event.type == "turn_prompt":
            # クク is offered as a third turn choice to a holder.
            if self.my_hand == "クク" and self.policy.decide_cucco_declare(self.my_hand, self.alive_count):
                await self.conn.send("cucco_declare", {})
                self._info(f"turn: hand={self.my_hand} alive={self.alive_count} -> cucco")
            else:
                change = self.policy.decide_change(self.my_hand or "", self.alive_count)
                await self.conn.send("cambio_declare" if change else "no_change_declare", {})
                self._info(f"turn: hand={self.my_hand} alive={self.alive_count} -> {'change' if change else 'no_change'}")
        elif event.type == "effect_window":
            # Declared-effects tables send this to EVERY exchange target (a
            # uniform prompt masks the timing tell of who holds a special
            # card). Declare when our card has an effect -- matching the base
            # rules' automatic behavior -- otherwise confirm the exchange.
            # Either way answer fast, so the table never waits out the window.
            if self.my_hand in ("人間", "馬", "猫", "家"):
                await self.conn.send("effect_declare", {})
            else:
                await self.conn.send("effect_pass", {})
        elif event.type == "continue_prompt":
            stay = self.policy.decide_continue(self.my_chips, p.get("required_chips", 1))
            await self.conn.send("continue_declare", {"continue": stay})

        elif event.type == "pot_result" and self.mode == "normal":
            # Not required by the protocol -- later pots within a game
            # auto-include everyone (docs/protocol/design.md's `ready` row)
            # and the server ignores this. Kept as a harmless liveness ping
            # and as insurance against that behavior ever changing.
            await self.conn.send("ready", {})
        elif event.type == "game_ended":
            self._info(f"game_ended: {p['ranking']}")
            if self.mode == "normal":
                return p
        elif event.type == "evaluation_summary":
            return p
        elif event.type == "action_rejected":
            self._info(f"action_rejected: {p.get('reason')}")
        return None
