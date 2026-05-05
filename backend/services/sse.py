"""Server-Sent Events helpers.

The wire format is the standard ``data: <json>\\n\\n`` shape. We add a
periodic heartbeat (``: ping\\n\\n``) so proxies / Fly's idle timeout
don't drop the connection during long-running extractions. The keep-
alive is a comment line — clients ignore it but TCP stays warm.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator


logger = logging.getLogger(__name__)


HEARTBEAT_INTERVAL_S = 15.0


def format_sse_event(payload: dict[str, Any]) -> str:
    """Serialise one event payload as a single SSE record."""
    body = json.dumps(payload, default=str, separators=(",", ":"))
    return f"data: {body}\n\n"


def format_sse_terminator() -> str:
    """The ``[DONE]`` sentinel some EventSource consumers special-case."""
    return "data: [DONE]\n\n"


def format_sse_heartbeat() -> str:
    """Comment line that keeps idle proxies from closing the channel."""
    return ": ping\n\n"


async def stream_with_heartbeat(
    queue: asyncio.Queue,
    *,
    terminator_types: tuple[str, ...] = ("done", "error", "canceled"),
) -> AsyncIterator[str]:
    """Drain ``queue`` as SSE chunks; emit heartbeats while idle.

    Yields strings ready to be wrapped in a FastAPI ``StreamingResponse``
    of ``media_type="text/event-stream"``. Stops after the first event
    whose ``type`` matches ``terminator_types`` is forwarded — at which
    point we also emit the ``[DONE]`` sentinel for client convenience.
    """
    while True:
        try:
            event = await asyncio.wait_for(
                queue.get(), timeout=HEARTBEAT_INTERVAL_S
            )
        except asyncio.TimeoutError:
            yield format_sse_heartbeat()
            continue

        yield format_sse_event(event)

        if event.get("type") in terminator_types:
            yield format_sse_terminator()
            return


__all__ = [
    "HEARTBEAT_INTERVAL_S",
    "format_sse_event",
    "format_sse_heartbeat",
    "format_sse_terminator",
    "stream_with_heartbeat",
]
