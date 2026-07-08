"""WebSocket server entry point. The only module that imports `websockets`
directly -- everything else works against the `Connection` Protocol."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import websockets

from cucco.persistence.results_store import ResultsStore
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


async def serve(host: str = "0.0.0.0", port: int = 8765, data_dir: Path | None = None) -> None:
    registry = TableRegistry()
    data_dir = data_dir or Path("data")
    results_store = ResultsStore(data_dir / "results.db")
    action_log_dir = data_dir / "action_logs"

    async def _handler(websocket):
        await handle_connection(websocket, registry, results_store, action_log_dir)

    async with websockets.serve(_handler, host, port):
        logger.info("cucco-server listening on ws://%s:%d", host, port)
        await asyncio.Future()  # run forever


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
