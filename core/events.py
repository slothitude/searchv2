import asyncio
import json
from collections import defaultdict
from typing import AsyncGenerator
from fastapi import Request


class EventBus:
    """In-process SSE event bus."""

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, channel: str = "default") -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=100)
        self._subscribers[channel].append(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue):
        if channel in self._subscribers:
            self._subscribers[channel] = [
                q for q in self._subscribers[channel] if q is not queue
            ]

    async def publish(self, event: str, data: dict, channel: str = "default"):
        message = json.dumps({"event": event, "data": data})
        for queue in self._subscribers.get(channel, []):
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
