"""In-process pub/sub used to feed the dashboard WebSocket and latest-state APIs."""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any


def _offer(queue: asyncio.Queue, event: dict) -> None:
    """Drop the oldest frame when a slow dashboard is behind; never block producers."""
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
            queue.put_nowait(event)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            pass


class EventBus:
    def __init__(self, history: int = 50) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._latest: dict[str, Any] = {}
        self._history: dict[str, deque] = {}
        self._history_len = history
        self._lock = threading.Lock()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def latest(self, topic: str) -> Any:
        with self._lock:
            return self._latest.get(topic)

    def latest_all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    def history(self, topic: str) -> list[Any]:
        with self._lock:
            return list(self._history.get(topic, ()))

    def publish(self, topic: str, payload: Any) -> None:
        with self._lock:
            self._latest[topic] = payload
            buf = self._history.setdefault(topic, deque(maxlen=self._history_len))
            buf.append(payload)
            subs = list(self._subscribers)
            loop = self._loop
        if loop is None or loop.is_closed():
            return
        event = {"topic": topic, "data": payload}
        for queue in subs:
            try:
                loop.call_soon_threadsafe(_offer, queue, event)
            except RuntimeError:
                # Loop closed between the check and the call; dropping the live
                # notification is safe because ``latest`` still holds the value.
                pass

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)
