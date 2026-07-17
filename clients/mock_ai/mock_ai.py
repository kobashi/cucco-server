"""Fully automatic Mock AI client (docs/ai-client-guide.md).

A passive event loop: waits for server notifications and answers the
decision points (turn / continue / dealer_ready / effect window), delegating
the actual choices to a policy from `clients.mock_ai.policies`. クク is
fire-and-forget (`cucco_declare` may be sent at any moment); this bot only
declares it at its own prompts (turn / dealer_ready), which is a legal
simplification -- declaring is always optional. Used to drive
evaluation-mode self-play runs and as a reference implementation for
seminar students' own AI clients.

Run standalone:
    python -m clients.mock_ai.mock_ai --url ws://localhost:8765 --name Bot1 \
        --policy matrix --create --mode evaluation --game-count 10
    python -m clients.mock_ai.mock_ai --url ws://localhost:8765 --name Bot2 \
        --policy always_change --room ABC123
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from clients.common.ws_client import CuccoConnection, ServerEvent
from clients.mock_ai.policies import BasePolicy, make_policy


class MockAI:
    """Plays one table to its end (game_ended, or evaluation_summary in
    evaluation mode) over an already-identified, already-joined connection."""

    def __init__(self, conn: CuccoConnection, policy: BasePolicy, *, mode: str = "normal", log=None) -> None:
        self.conn = conn
        self.policy = policy
        self.mode = mode
        self.log = log  # callable(str) or None
        # State per docs/ai-client-guide.md §3.
        self.my_hand: str | None = None
        self.my_chips: int = 0
        self.pot_active: set[str] = set()
        self.deal_alive: set[str] = set()
        self.received: list[ServerEvent] = []

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

    async def _handle(self, event: ServerEvent) -> dict | None:
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
            # Guide §2: re-declare ready for every pot. The current server
            # auto-includes everyone in later pots and ignores this, but the
            # protocol contract says to send it.
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


async def main() -> None:
    parser = argparse.ArgumentParser(description="Cucco mock AI client")
    parser.add_argument("--url", default="ws://localhost:8765")
    parser.add_argument("--name", required=True)
    parser.add_argument("--policy", default="matrix", help="always_change | always_no_change | matrix")
    parser.add_argument("--create", action="store_true", help="create a new table instead of joining")
    parser.add_argument("--room", help="room id to join (when not --create)")
    parser.add_argument("--mode", default="normal", choices=["normal", "evaluation"])
    parser.add_argument("--game-count", type=int, default=10, help="evaluation mode only")
    parser.add_argument("--starting-chips", type=int, default=25)
    args = parser.parse_args()

    if not args.create and not args.room:
        parser.error("either --create or --room is required")

    async with CuccoConnection(args.url) as conn:
        await conn.identify(args.name, "ai")
        if args.create:
            config: dict = {"mode": args.mode, "starting_chips": args.starting_chips}
            if args.mode == "evaluation":
                config["game_count"] = args.game_count
            room_id = await conn.create_table(config)
            print(f"table created: room_id={room_id}", flush=True)
        else:
            room_id = args.room
        await conn.join_table(room_id)

        ai = MockAI(conn, make_policy(args.policy), mode=args.mode, log=lambda m: print(f"[{args.name}] {m}", flush=True))
        result = await ai.play()
        print(f"[{args.name}] finished: {result}", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
