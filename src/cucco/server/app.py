"""WebSocket server entry point. The only module that imports `websockets`
directly -- everything else works against the `Connection` Protocol."""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from pathlib import Path

import websockets

from cucco.persistence.results_store import ResultsStore
from cucco.server.admin import serve_admin
from cucco.server.dispatch import ConnectionHandler
from cucco.server.registry import TableRegistry

logger = logging.getLogger("cucco.server")


async def handle_connection(
    websocket,
    registry: TableRegistry,
    results_store: ResultsStore | None = None,
    action_log_dir: Path | None = None,
) -> None:
    handler = ConnectionHandler(websocket, registry, results_store=results_store, action_log_dir=action_log_dir)
    try:
        async for raw in websocket:
            await handler.handle_message(raw)
    finally:
        await handler.on_disconnect()


async def serve(
    host: str = "0.0.0.0",
    port: int = 8765,
    data_dir: Path | None = None,
    admin_port: int | None = 8766,
    admin_token: str | None = None,
) -> None:
    registry = TableRegistry()
    data_dir = data_dir or Path("data")
    results_store = ResultsStore(data_dir / "results.db")
    action_log_dir = data_dir / "action_logs"

    async def _handler(websocket):
        await handle_connection(websocket, registry, results_store, action_log_dir)

    # Admin listener: loopback only, never exposed alongside the game port
    # (docs/security-notes.md). Token auto-generated unless supplied.
    admin_server = None
    if admin_port is not None:
        token = admin_token or uuid.uuid4().hex
        if admin_token is None:
            logger.info("admin token (this run only): %s", token)
        admin_server = await serve_admin(registry, port=admin_port, token=token)

    try:
        async with websockets.serve(_handler, host, port):
            logger.info("cucco-server listening on ws://%s:%d", host, port)
            await asyncio.Future()  # run forever
    finally:
        if admin_server is not None:
            admin_server.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="cucco-server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--admin-port", type=int, default=8766, help="管理リスナーのポート(127.0.0.1固定)。0で無効化")
    parser.add_argument("--admin-token", default=None, help="管理トークン(省略時は起動ごとに生成しログに出力)")
    args = parser.parse_args()
    asyncio.run(
        serve(
            host=args.host,
            port=args.port,
            data_dir=args.data_dir,
            admin_port=args.admin_port or None,
            admin_token=args.admin_token,
        )
    )


if __name__ == "__main__":
    main()
