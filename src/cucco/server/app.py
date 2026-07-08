"""WebSocket server entry point. The only module that imports `websockets`
directly -- everything else works against the `Connection` Protocol."""

from __future__ import annotations

import asyncio
import logging

import websockets

from cucco.server.dispatch import ConnectionHandler
from cucco.server.registry import TableRegistry

logger = logging.getLogger("cucco.server")


async def handle_connection(websocket, registry: TableRegistry) -> None:
    handler = ConnectionHandler(websocket, registry)
    try:
        async for raw in websocket:
            await handler.handle_message(raw)
    finally:
        await handler.on_disconnect()


async def serve(host: str = "0.0.0.0", port: int = 8765) -> None:
    registry = TableRegistry()

    async def _handler(websocket):
        await handle_connection(websocket, registry)

    async with websockets.serve(_handler, host, port):
        logger.info("cucco-server listening on ws://%s:%d", host, port)
        await asyncio.Future()  # run forever


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
