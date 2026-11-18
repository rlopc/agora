"""Composition of the room with its adapters and server startup (`uv run agora`)."""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import socket
import tomllib
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from agora.agents import ConfigError, ResolvedAgent, ResolvedParticipant, resolve, to_participant
from agora.agents.environment import db_path
from agora.agents.tool import Tool, UnconfiguredWaker, WakeContext
from agora.agents.watch import WatchWaker
from agora.core import MAX_HOPS, MAX_MODERATOR_TURNS, Room, RoomError, Waker
from agora.store.sqlite import SqliteStore
from agora.tools import TOOLS
from agora.web import WebChannel, create_app

log = logging.getLogger("agora")


@dataclass
class Server:
    room: Room
    app: FastAPI
    host: str
    port: int
    close: Callable[[], None]
    # Called when the stop is requested, before uvicorn waits for open connections.
    on_stop: Callable[[], None] = lambda: None
    # Participants that cannot be woken yet, to log when starting.
    warnings: tuple[str, ...] = ()


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def create_waker(p: ResolvedParticipant, tools: Mapping[str, Tool], context: WakeContext) -> Waker | None:
    if not isinstance(p, ResolvedAgent) or p.waker is None:
        return None
    if p.waker == "watch":
        return WatchWaker()
    if p.waker != tools[p.tool].default_waker:
        raise ValueError(f"unknown waker for {p.id}: {p.waker}")
    return tools[p.tool].create_waker(p, context)


def _warning(p: ResolvedParticipant, waker: Waker, tools: Mapping[str, Tool]) -> str | None:
    if isinstance(waker, UnconfiguredWaker):
        return f"@{p.id} cannot be woken: {waker.reason}"
    return tools[p.tool].startup_warning(p, waker) if isinstance(p, ResolvedAgent) else None


def build(
    config: Mapping[str, Any],
    base: Path,
    env: Mapping[str, str] | None = None,
    config_path: Path | None = None,
    tools: Mapping[str, Tool] = TOOLS,
) -> Server:
    env = os.environ if env is None else env
    settings = config.get("room", {})
    resolved = resolve(config, base, tools)
    participants = [to_participant(p) for p in resolved]
    if not participants:
        raise ValueError("agora.toml defines no participants")

    channel = WebChannel()
    threads = ThreadPoolExecutor(max_workers=4, thread_name_prefix="wake")

    def executor(task: Callable[[], None]) -> None:
        def safe() -> None:
            try:
                task()
            except Exception:
                log.exception("unexpected failure while waking")

        threads.submit(safe)

    room: Room | None = None

    def context(id: str) -> WakeContext:
        return WakeContext(env, config_path, listening=lambda: room is not None and room.listening(id))

    wakers = {p.id: w for p in resolved if (w := create_waker(p, tools, context(p.id))) is not None}
    warnings = tuple(text for p in resolved if p.id in wakers and (text := _warning(p, wakers[p.id], tools)))
    # Opened last, so that a failure creating the wakers does not leave the connection open.
    store = SqliteStore(db_path(config, base))
    try:
        room = Room(
            participants,
            store,
            [channel],
            wakers,
            max_hops=int(settings.get("max_hops", MAX_HOPS)),
            max_moderator_turns=int(settings.get("max_moderator_turns", MAX_MODERATOR_TURNS)),
            executor=executor,
        )
    except RoomError:
        threads.shutdown(wait=True, cancel_futures=False)
        store.close()
        raise
    host = settings.get("host", "127.0.0.1")
    port = int(settings.get("port", 8765))
    hosts = sorted({host, "127.0.0.1", "localhost"})
    app = create_app(room, channel, hosts=hosts, origins=[f"http://{h}:{port}" for h in hosts])

    def close() -> None:
        threads.shutdown(wait=True, cancel_futures=False)
        store.close()

    return Server(room, app, host, port, close, on_stop=channel.close, warnings=warnings)


def main(argv: Sequence[str] | None = None) -> None:
    import uvicorn

    class UvicornServer(uvicorn.Server):
        """Closes the SSE streams and long waits on Ctrl+C, so that the shutdown is clean."""

        def __init__(self, config: uvicorn.Config, on_stop: Callable[[], None]) -> None:
            super().__init__(config)
            self._on_stop = on_stop

        def handle_exit(self, sig: int, frame: Any) -> None:
            self._on_stop()
            super().handle_exit(sig, frame)

    parser = argparse.ArgumentParser(description="Local server for the agent room.")
    parser.add_argument("--config", type=Path, default=Path("agora.toml"))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = load_config(args.config)
    if args.host or args.port:
        config.setdefault("room", {})
        config["room"].update({k: v for k, v in (("host", args.host), ("port", args.port)) if v})
    base = args.config.resolve().parent
    try:
        server = build(config, base, config_path=args.config.resolve())
    except (RoomError, ConfigError) as exc:
        raise SystemExit(f"invalid agora.toml: {exc}") from None
    for warning in server.warnings:
        log.warning("%s", warning)
    with socket.socket() as probe:
        try:
            probe.bind((server.host, server.port))
        except OSError:
            server.close()
            raise SystemExit(
                f"Port {server.port} is already in use: is another room open at http://{server.host}:{server.port}?"
            ) from None
    log.info("Room at http://%s:%d", server.host, server.port)
    try:
        config = uvicorn.Config(
            server.app, host=server.host, port=server.port, log_level="warning", timeout_graceful_shutdown=2
        )
        # uvicorn re-raises the signal after stopping; as with `uvicorn.run`, it is not an error.
        with contextlib.suppress(KeyboardInterrupt):
            UvicornServer(config, server.on_stop).run()
    finally:
        server.close()
