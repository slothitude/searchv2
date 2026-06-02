import asyncio
import json
import time
from collections import defaultdict
from typing import AsyncGenerator
from fastapi import Request


class EventBus:
    """In-process SSE event bus with automatic stale subscriber cleanup."""

    SUBSCRIBER_TTL = 1800  # 30 minutes

    def __init__(self):
        self._subscribers: dict[str, list[tuple[asyncio.Queue, float]]] = defaultdict(list)

    def subscribe(self, channel: str = "default") -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=100)
        self._subscribers[channel].append((queue, time.monotonic()))
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue):
        if channel in self._subscribers:
            self._subscribers[channel] = [
                (q, t) for q, t in self._subscribers[channel] if q is not queue
            ]

    def _cleanup_stale(self, channel: str):
        now = time.monotonic()
        self._subscribers[channel] = [
            (q, t) for q, t in self._subscribers[channel]
            if now - t < self.SUBSCRIBER_TTL
        ]

    async def publish(self, event: str, data: dict, channel: str = "default"):
        message = json.dumps({"event": event, "data": data})
        self._cleanup_stale(channel)
        for queue, _ in self._subscribers.get(channel, []):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass  # drop if subscriber is slow

    async def stream(self, channel: str = "default") -> AsyncGenerator[str, None]:
        queue = self.subscribe(channel)
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {message}\n\n"
                except asyncio.TimeoutError:
                    yield f": ping\n\n"  # keepalive
        except asyncio.CancelledError:
            pass
        finally:
            self.unsubscribe(channel, queue)


bus = EventBus()
