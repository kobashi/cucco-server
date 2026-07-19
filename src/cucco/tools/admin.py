"""サーバー管理CLI (AIロードマップ第4段階).

Talks to the local admin listener (`cucco.server.app --admin-port`, default
ws://127.0.0.1:8766). Run on the server machine; the admin port must never
be tunneled (docs/security-notes.md).

    python -m cucco.tools.admin --token XXXX list
    python -m cucco.tools.admin --token XXXX status AB12CD
    python -m cucco.tools.admin --token XXXX abort AB12CD
    python -m cucco.tools.admin --token XXXX remove AB12CD

The token is printed in the server log at startup (or set via
--admin-token when launching the server).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import websockets


def _fmt_table_row(t: dict) -> list:
    state = "対局中" if t["game_active"] else ("評価中" if t["evaluation_started"] and not t["finished"] else "待機")
    return [
        t["room_id"],
        t["mode"],
        state,
        f"{t['players']}({t['bots']}AI)",
        t["humans_connected"],
        t["spectators"],
        t["pot_number"],
        f"{t['idle_sec']:.0f}s",
        time.strftime("%m-%d %H:%M", time.localtime(t["created_at"])),
    ]


def _print_table(headers: list[str], rows: list[list]) -> None:
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h)) for i, h in enumerate(headers)]
    def fmt(row):  # noqa: E306
        return "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)).rstrip()
    print(fmt(headers))
    print(fmt(["-" * w for w in widths]))
    for r in rows:
        print(fmt(r))


async def run(args) -> int:
    request: dict = {"token": args.token}
    if args.command == "list":
        request["action"] = "list_tables"
    else:
        request["action"] = {"status": "table_status", "abort": "abort_table", "remove": "remove_table"}[args.command]
        request["room_id"] = args.room_id

    async with websockets.connect(args.url) as ws:
        await ws.send(json.dumps(request))
        reply = json.loads(await ws.recv())

    if not reply.get("ok"):
        print(f"エラー: {reply.get('error')}", file=sys.stderr)
        return 1

    if args.command == "list":
        tables = reply["tables"]
        if not tables:
            print("(卓はありません)")
            return 0
        _print_table(
            ["卓", "モード", "状態", "人数", "接続中人間", "観戦", "ポット", "無操作", "作成"],
            [_fmt_table_row(t) for t in sorted(tables, key=lambda t: -t["idle_sec"])],
        )
    elif args.command == "status":
        print(json.dumps(reply, ensure_ascii=False, indent=2))
    elif args.command == "abort":
        print(f"卓 {reply['aborted']} を中止しました")
        if reply.get("ranking"):
            for i, (pid, chips) in enumerate(reply["ranking"], start=1):
                print(f"  {i}位: {pid} ({chips}チップ)")
    elif args.command == "remove":
        print(f"卓 {reply['removed']} を削除しました")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cucco-server 管理ツール(ローカル専用)")
    parser.add_argument("--url", default="ws://127.0.0.1:8766")
    parser.add_argument("--token", required=True, help="サーバー起動ログに出力された管理トークン")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="全卓の一覧と進行状況")
    for name, help_ in (("status", "卓の詳細状態"), ("abort", "進行中のゲームを強制終了して卓を閉じる"), ("remove", "ゲームの走っていない卓を削除")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("room_id")
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
