"""Broadcasts room events to the browsers connected over SSE."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator

from agora.core import Event

_MAX_QUEUE = 1000


class WebChannel:
    """Implements the `Channel` port; `publish` may be called from any thread."""

    def __init__(self, heartbeat: float = 15.0) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[Event | None]]] = set()
        self._heartbeat = heartbeat
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def publish(self, event: Event) -> None:
        self._broadcast(event)

    def close(self) -> None:
        """Ends the open streams so the server can stop without cutting them off forcibly."""
        self._closed = True
        self._broadcast(None)

    def _broadcast(self, event: Event | None) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            loop.call_soon_threadsafe(self._enqueue, queue, event)

    @staticmethod
    def _enqueue(queue: asyncio.Queue[Event | None], event: Event | None) -> None:
        if queue.full():  # a browser that does not read must not block the others
            queue.get_nowait()
        queue.put_nowait(event)

    async def sse_stream(self) -> AsyncIterator[str]:
        entry = (asyncio.get_running_loop(), asyncio.Queue[Event | None](_MAX_QUEUE))
        with self._lock:
            self._subscribers.add(entry)
        try:
            yield ": connected\n\n"
            while not self._closed:
                try:
                    event = await asyncio.wait_for(entry[1].get(), self._heartbeat)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event is None:
                    return
                data = json.dumps(dict(event.data), ensure_ascii=False)
                yield f"event: {event.type}\ndata: {data}\n\n"
        finally:
            with self._lock:
                self._subscribers.discard(entry)
