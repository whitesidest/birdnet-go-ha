"""Server-Sent Events client for BirdNET-Go's live detection stream.

BirdNET-Go exposes ``GET /api/v2/detections/stream``. Its own web UI (see the
``Je()``/``onmessage`` handler in the shipped frontend bundle) dispatches on a
JSON ``eventType`` field rather than the SSE event name, and falls back to
"treat it as a detection if it has an id and a commonName". We mirror that
logic so we stay compatible with both framings:

* ``connected``  — handshake, carries a clientId
* ``detection``  — a CONFIRMED detection. NOTE: the SSE event NAME is
  ``detection`` but the in-band ``eventType`` field reads ``new_detection``
  (verified against a live 20260716 server). The frontend's own switch only
  handles ``detection`` and falls through to its duck-type branch, so we accept
  both spellings rather than trusting either alone.
* ``heartbeat``  — keepalive, carries a client count
* ``pending``    — in-progress candidates (status active/rejected). Deliberately
  IGNORED: these churn as the model accumulates hits and would make entities
  flap between species before anything is confirmed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from .const import STREAM_BACKOFF_MAX, STREAM_BACKOFF_MIN

_LOGGER = logging.getLogger(__name__)

# The server labels confirmed detections inconsistently: SSE event name
# "detection", in-band eventType "new_detection". Accept either.
DETECTION_KINDS = frozenset({"detection", "new_detection"})


def _looks_like_detection(payload: Any) -> bool:
    """Match the frontend's own fallback test."""
    return (
        isinstance(payload, dict)
        and payload.get("id") is not None
        and bool(payload.get("commonName"))
    )


class SSEStream:
    """Reconnecting Server-Sent Events reader.

    Subclasses implement :meth:`handle` to consume decoded events. The
    connection loop, SSE framing and exponential backoff live here so every
    BirdNET-Go stream shares one implementation.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        headers: dict[str, str],
        on_state: Callable[[bool], None] | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self._url = url
        self._headers = {**headers, "Accept": "text/event-stream"}
        self._on_state = on_state
        self._verify_ssl = verify_ssl
        self._task: asyncio.Task | None = None
        self._closing = False
        self.connected = False

    def handle(self, event_name: str | None, payload: Any) -> None:
        """Consume one decoded event. Override in a subclass."""
        raise NotImplementedError

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._closing = False
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._closing = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._set_connected(False)

    def _set_connected(self, value: bool) -> None:
        if value != self.connected:
            self.connected = value
            if self._on_state:
                self._on_state(value)

    async def _run(self) -> None:
        backoff = STREAM_BACKOFF_MIN
        while not self._closing:
            try:
                await self._consume()
                backoff = STREAM_BACKOFF_MIN
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - never let the loop die
                _LOGGER.debug("stream %s dropped: %s", self._url, err)
            finally:
                self._set_connected(False)

            if self._closing:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, STREAM_BACKOFF_MAX)

    async def _consume(self) -> None:
        async with self._session.get(
            self._url,
            headers=self._headers,
            ssl=None if self._verify_ssl else False,
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=None),
        ) as resp:
            resp.raise_for_status()
            self._set_connected(True)
            _LOGGER.debug("stream connected: %s", self._url)

            event_name: str | None = None
            data_lines: list[str] = []

            async for raw in resp.content:
                if self._closing:
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")

                if not line:
                    if data_lines:
                        self._decode(event_name, "\n".join(data_lines))
                    event_name, data_lines = None, []
                    continue
                if line.startswith(":"):
                    continue  # comment / keepalive
                field, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if field == "event":
                    event_name = value
                elif field == "data":
                    data_lines.append(value)

    def _decode(self, event_name: str | None, data: str) -> None:
        try:
            payload = json.loads(data)
        except ValueError:
            _LOGGER.debug("Unparseable SSE data: %s", data[:200])
            return
        self.handle(event_name, payload)


class BirdNetStream(SSEStream):
    """Live confirmed detections from ``/api/v2/detections/stream``."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        headers: dict[str, str],
        on_detection: Callable[[dict[str, Any]], None],
        on_state: Callable[[bool], None],
        verify_ssl: bool = True,
    ) -> None:
        super().__init__(
            session,
            f"{base_url.rstrip('/')}/api/v2/detections/stream",
            headers,
            on_state=on_state,
            verify_ssl=verify_ssl,
        )
        self._on_detection = on_detection

    def handle(self, event_name: str | None, payload: Any) -> None:
        kind = payload.get("eventType") if isinstance(payload, dict) else None
        kind = kind or event_name

        if kind in ("heartbeat", "connected", "pending"):
            return

        if kind in DETECTION_KINDS:
            body = payload.get("data") if isinstance(payload, dict) else None
            candidate = body if _looks_like_detection(body) else payload
            if _looks_like_detection(candidate):
                self._on_detection(candidate)
            return

        # Unlabelled — apply the frontend's fallback heuristic.
        if _looks_like_detection(payload):
            self._on_detection(payload)
        elif isinstance(payload, list):
            for item in payload:
                if _looks_like_detection(item):
                    self._on_detection(item)

    # Kept for the test-suite's direct-dispatch harness.
    def _dispatch(self, event_name: str | None, data: str) -> None:
        self._decode(event_name, data)
