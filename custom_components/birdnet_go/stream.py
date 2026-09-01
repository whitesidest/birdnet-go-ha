"""Server-Sent Events client for BirdNET-Go's live detection stream.

BirdNET-Go exposes ``GET /api/v2/detections/stream``. Its own web UI (see the
``Je()``/``onmessage`` handler in the shipped frontend bundle) dispatches on a
JSON ``eventType`` field rather than the SSE event name, and falls back to
"treat it as a detection if it has an id and a commonName". We mirror that
logic so we stay compatible with both framings:

* ``connected``  — handshake, carries a clientId
* ``detection``  — a CONFIRMED detection, same rich shape as the REST API
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


def _looks_like_detection(payload: Any) -> bool:
    """Match the frontend's own fallback test."""
    return (
        isinstance(payload, dict)
        and payload.get("id") is not None
        and bool(payload.get("commonName"))
    )


class BirdNetStream:
    """Maintains a reconnecting SSE subscription to the detection stream."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        headers: dict[str, str],
        on_detection: Callable[[dict[str, Any]], None],
        on_state: Callable[[bool], None],
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self._url = f"{base_url.rstrip('/')}/api/v2/detections/stream"
        self._headers = {**headers, "Accept": "text/event-stream"}
        self._on_detection = on_detection
        self._on_state = on_state
        self._verify_ssl = verify_ssl
        self._task: asyncio.Task | None = None
        self._closing = False
        self.connected = False

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
                _LOGGER.debug("BirdNET-Go stream dropped: %s", err)
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
            _LOGGER.debug("BirdNET-Go stream connected")

            event_name: str | None = None
            data_lines: list[str] = []

            async for raw in resp.content:
                if self._closing:
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")

                if not line:
                    # blank line terminates an event
                    if data_lines:
                        self._dispatch(event_name, "\n".join(data_lines))
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

    def _dispatch(self, event_name: str | None, data: str) -> None:
        try:
            payload = json.loads(data)
        except ValueError:
            _LOGGER.debug("Unparseable SSE data: %s", data[:200])
            return

        # The server labels events both ways; prefer the in-band field the
        # official frontend trusts, fall back to the SSE event name.
        kind = None
        if isinstance(payload, dict):
            kind = payload.get("eventType")
        kind = kind or event_name

        if kind in ("heartbeat", "connected", "pending"):
            return

        if kind == "detection":
            # Some builds wrap the detection under a "data" key.
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
