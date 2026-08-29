"""Fully automatic Mock AI client (docs/ai-client-guide.md).

CLI + WebSocket transport only: the decision brain (`MockAI`) and the
policies live in `cucco.ai` (shared with the server-embedded `ai_players`
bots) and are re-exported here so the historical import paths keep working.

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
from pathlib import Path

from clients.common.ws_client import CuccoConnection
from clients.mock_ai.policies import make_policy
from cucco.ai.plugins import load_policy_dir, log_report
from cucco.ai.bot import MockAI  # noqa: F401  (re-export for existing importers)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Cucco mock AI client")
    parser.add_argument("--url", default="ws://localhost:8765")
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--policy",
        default="matrix",
        help="always_change | always_no_change | matrix | counting_aggressive | counting_conservative",
    )
    parser.add_argument("--create", action="store_true", help="create a new table instead of joining")
    parser.add_argument("--room", help="room id to join (when not --create)")
    parser.add_argument("--mode", default="normal", choices=["normal", "evaluation"])
    parser.add_argument("--game-count", type=int, default=10, help="evaluation mode only")
    parser.add_argument("--starting-chips", type=int, default=25)
    parser.add_argument(
        "--policy-dir",
        type=Path,
        default=Path("policies"),
        help="追加AI方策の置き場所(既定 policies)。自分で書いた方策をここから読む",
    )
    args = parser.parse_args()
    # 自分で書いた方策をこのプロセスにも読み込む。サーバー内蔵AIは
    # サーバー側で読み込まれるが、この外部クライアントは別プロセスなので
    # ここでも読む必要がある(docs/ai-policy-reference.md「手元で測る」)。
    log_report(load_policy_dir(args.policy_dir), args.policy_dir)

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
