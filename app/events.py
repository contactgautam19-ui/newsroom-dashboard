"""In-process pub/sub bridging background jobs to SSE subscribers.

Background jobs run in scheduler threads; publish() is thread-safe and hands
events to the asyncio loop that owns the subscriber queues.
"""

import asyncio
import json
from typing import Any

_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def _dispatch(event: dict[str, Any]) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # slow client; drop rather than stall the newsroom feed


def publish(event_type: str, data: Any) -> None:
    """Thread-safe publish from any context."""
    event = {"type": event_type, "data": data}
    if _loop is None:
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _loop:
        _dispatch(event)
    else:
        _loop.call_soon_threadsafe(_dispatch, event)


def sse_format(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event['data'], default=str)}\n\n"
