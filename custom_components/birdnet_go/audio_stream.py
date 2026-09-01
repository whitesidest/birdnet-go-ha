"""Live per-source audio levels from ``/api/v2/streams/audio-level``.

The endpoint emits UNNAMED SSE events (no ``event:`` line) carrying::

    {"type": "audio-level",
     "levels": {"rtsp_<hash>": {"level": 0-100, "clipping": false,
                                "source": "rtsp_<hash>", "name": "Deck"}, ...}}

It fires roughly **16 times per second**. Writing that straight into Home
Assistant would be ~4 million state changes a day across three microphones and
would bloat the recorder database badly, so nothing here touches entity state
directly: messages are folded into an in-memory accumulator and a separate
timer flushes a summary on a fixed interval.

The accumulator keeps the PEAK level rather than the latest or the mean. A bird
call is a transient a second or two long; sampling the instantaneous value at
flush time would usually miss it, and averaging would flatten it into the noise
floor. Clipping latches for the window, because a single clipped buffer is the
thing worth surfacing.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from .stream import SSEStream

_LOGGER = logging.getLogger(__name__)


class BirdNetAudioLevelStream(SSEStream):
    """Feeds decoded ``levels`` maps to a callback."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        headers: dict[str, str],
        on_levels: Callable[[dict[str, Any]], None],
        on_state: Callable[[bool], None] | None = None,
        verify_ssl: bool = True,
    ) -> None:
        super().__init__(
            session,
            f"{base_url.rstrip('/')}/api/v2/streams/audio-level",
            headers,
            on_state=on_state,
            verify_ssl=verify_ssl,
        )
        self._on_levels = on_levels

    def handle(self, event_name: str | None, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("type") not in (None, "audio-level"):
            return
        levels = payload.get("levels")
        if isinstance(levels, dict) and levels:
            self._on_levels(levels)
