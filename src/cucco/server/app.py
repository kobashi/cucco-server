"""WebSocket server entry point. The only module that imports `websockets`
directly -- everything else works against the `Connection` Protocol."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import uuid
from pathlib import Path

import websockets

from cucco.persistence.results_store import ResultsStore
from cucco.server.admin import GC_INTERVAL_SEC, run_gc_loop, serve_admin
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
    gc_interval_sec: float | None = GC_INTERVAL_SEC,
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
        admin_server = await serve_admin(
            registry, port=admin_port, token=token, results_store=results_store, action_log_dir=action_log_dir
        )

    # Background sweep that removes abandoned/idle tables (a bot-only table
    # rematching after its watcher left, an unjoined lobby, a crashed room).
    gc_task = None
    if gc_interval_sec:
        gc_task = asyncio.create_task(run_gc_loop(registry, interval_sec=gc_interval_sec))
        logger.info("table GC sweeping every %.0fs", gc_interval_sec)

    # Graceful shutdown: SIGTERM (`kill`) and SIGINT (Ctrl-C) both set this
    # event instead of the old `await asyncio.Future()` that ran forever. That
    # way `kill <pid>` unwinds cleanly through the finally below (and closing
    # the listener sends every client a WebSocket going-away frame, so they
    # show "reconnecting" and can rejoin with their session_token on restart).
    # add_signal_handler for SIGINT also replaces the default handler, so
    # Ctrl-C no longer dumps a KeyboardInterrupt traceback.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: (logger.info("received %s, shutting down", s.name), stop.set()))
        except NotImplementedError:
            # add_signal_handler is POSIX-only; on other platforms fall back
            # to the default disposition (SIGINT -> KeyboardInterrupt).
            pass

    try:
        async with websockets.serve(_handler, host, port):
            logger.info("cucco-server listening on ws://%s:%d", host, port)
            await stop.wait()
            logger.info("shutting down: closing listeners")
    finally:
        if gc_task is not None:
            gc_task.cancel()
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
    parser.add_argument(
        "--gc-interval",
        type=float,
        default=GC_INTERVAL_SEC,
        help="放置卓の自動掃除の間隔(秒)。0で無効化",
    )
    args = parser.parse_args()
    asyncio.run(
        serve(
            host=args.host,
            port=args.port,
            data_dir=args.data_dir,
            admin_port=args.admin_port or None,
            admin_token=args.admin_token,
            gc_interval_sec=args.gc_interval or None,
        )
    )


if __name__ == "__main__":
    main()
