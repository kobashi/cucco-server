"""Interactive terminal client for manual protocol verification.

Prints every server event in a readable form and asks the operator what to
do at each decision point (docs/ai-client-guide.md §2). Meant for eyeballing
a live deal against the message-flow example in the guide, not for play
comfort -- the human-facing UI is a separate future project. Limitation:
クク can only be declared at your own prompts here (the synchronous ask()
loop can't take out-of-turn input); the browser clients expose the full
anytime-declaration button.

    python -m clients.stub.stub_client --url ws://localhost:8765 --name Alice --create
    python -m clients.stub.stub_client --url ws://localhost:8765 --name Bob --room ABC123
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from clients.common.ws_client import CuccoConnection, ServerEvent

# Events that are pure state notifications -- shown compactly, no response.
QUIET_TYPES = {"state_snapshot"}


def show(event: ServerEvent) -> None:
    if event.type in QUIET_TYPES:
        print(f"<< {event.type}")
        return
    print(f"<< {event.type}: {json.dumps(event.payload, ensure_ascii=False)}")


async def ask(prompt: str) -> str:
    return (await asyncio.to_thread(input, prompt)).strip().lower()


async def run(conn: CuccoConnection) -> None:
    await conn.send("ready", {})
    print(">> ready")

    async for event in conn.events():
        show(event)

        if event.type == "dealer_ready":
            # A クク-holding dealer may declare it together with どうぞ.
            answer = await ask("you are the dealer -- [Enter]=どうぞ, c=クク宣言: ")
            action = "cucco_declare" if answer == "c" else "dealer_ready"
            await conn.send(action, {})
            print(f">> {action}")
        elif event.type == "turn_prompt":
            # クク is a third choice for a holder (change / no-change / クク).
            answer = await ask("your turn -- [y=change, N=no-change, c=クク宣言]: ")
            action = {"y": "cambio_declare", "c": "cucco_declare"}.get(answer, "no_change_declare")
            await conn.send(action, {})
            print(f">> {action}")
        elif event.type == "effect_window":
            # Declared-effects tables prompt EVERY exchange target (masking
            # the timing tell of who holds a special card). Declaring with a
            # plain card is treated as accepting by the server.
            answer = await ask("exchange requested -- declare your card's effect? [y=declare / N=accept]: ")
            action = "effect_declare" if answer == "y" else "effect_pass"
            await conn.send(action, {})
            print(f">> {action}")
        elif event.type == "continue_prompt":
            answer = await ask(f"you lost (pay {event.payload.get('required_chips')} chips) -- continue? [Y/n]: ")
            stay = answer != "n"
            await conn.send("continue_declare", {"continue": stay})
            print(f">> continue_declare continue={stay}")
        elif event.type == "pot_result":
            await conn.send("ready", {})
            print(">> ready (next pot)")
        elif event.type == "game_ended":
            print("game over.")
            return


async def main() -> None:
    parser = argparse.ArgumentParser(description="Cucco interactive stub client")
    parser.add_argument("--url", default="ws://localhost:8765")
    parser.add_argument("--name", required=True)
    parser.add_argument("--player-type", default="human", choices=["human", "ai", "spectator"])
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--room")
    parser.add_argument("--starting-chips", type=int, default=25)
    args = parser.parse_args()

    if not args.create and not args.room:
        parser.error("either --create or --room is required")

    async with CuccoConnection(args.url) as conn:
        await conn.identify(args.name, args.player_type)
        print(f"identified: player_id={conn.player_id} (session_token saved)")
        if args.create:
            room_id = await conn.create_table({"starting_chips": args.starting_chips})
            print(f"table created: room_id={room_id} -- share this with the other players")
        else:
            room_id = args.room
        snapshot = await conn.join_table(room_id)
        show(snapshot)
        await run(conn)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
