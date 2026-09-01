"""Coordinator: aggregates BirdNET-Go analytics, health and live detections.

Detections arrive by SSE (push) and are folded into per-source state
immediately; the polled half only refreshes aggregate counters and server
diagnostics, so ``DEFAULT_SCAN_INTERVAL`` can stay lazy.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import BirdNetApiError, BirdNetClient
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


def slugify_source(name: str) -> str:
    """Stable per-source key.

    Keyed on the human-set stream NAME rather than BirdNET-Go's ``rtsp_<hash>``
    sourceID, because that hash is derived from the stream URL — re-addressing a
    camera would otherwise orphan every entity on that source.
    """
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_") or "unknown"


def parse_uptime(value: Any) -> float | None:
    """Parse Go's duration string (e.g. ``113h17m49.05s``) into seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    total = 0.0
    for amount, unit in re.findall(r"([\d.]+)\s*(h|m|s|ms|us|ns)", value):
        try:
            num = float(amount)
        except ValueError:
            continue
        total += num * {
            "h": 3600, "m": 60, "s": 1,
            "ms": 1e-3, "us": 1e-6, "ns": 1e-9,
        }[unit]
    return total or None


class BirdNetCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls analytics/health and holds push state from the SSE stream."""

    def __init__(self, hass: HomeAssistant, client: BirdNetClient) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL
        )
        self.client = client
        # source_key -> most recent confirmed detection
        self.last_detection: dict[str, dict[str, Any]] = {}
        self.last_detection_any: dict[str, Any] | None = None
        # source_key -> {"name": str, "url": str, "enabled": bool}
        self.sources: dict[str, dict[str, Any]] = {}
        self.stream_connected = False
        self.server_info: dict[str, Any] = {}

    # --- polled half -----------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            health = await self.client.health()
            daily = await self.client.species_daily()
        except BirdNetApiError as err:
            raise UpdateFailed(str(err)) from err

        # These two are metadata/extra credit — a failure here should not tank
        # the whole update and mark every entity unavailable.
        realtime: dict[str, Any] = {}
        summary: list[dict[str, Any]] = []
        try:
            realtime = await self.client.realtime_settings()
        except BirdNetApiError as err:
            _LOGGER.debug("realtime settings unavailable: %s", err)
        try:
            summary = await self.client.species_summary()
        except BirdNetApiError as err:
            _LOGGER.debug("species summary unavailable: %s", err)

        if not self.server_info:
            try:
                self.server_info = await self.client.system_info()
            except BirdNetApiError as err:
                _LOGGER.debug("system info unavailable: %s", err)

        self._refresh_sources(realtime)

        detections_today = sum(int(s.get("count") or 0) for s in daily)
        species_today = len([s for s in daily if (s.get("count") or 0) > 0])
        new_today = [
            s for s in daily if s.get("days_since_first_seen") == 0
        ]

        return {
            "health": health,
            "daily": daily,
            "summary": summary,
            "detections_today": detections_today,
            "species_today": species_today,
            "species_total": len(summary),
            "species_week": self._species_week(summary),
            "new_species_today": len(new_today),
            "new_species_today_names": [s.get("common_name") for s in new_today],
        }

    def _refresh_sources(self, realtime: dict[str, Any]) -> None:
        streams = (realtime.get("rtsp") or {}).get("streams") or []
        for stream in streams:
            name = stream.get("name")
            if not name:
                continue
            self.sources[slugify_source(name)] = {
                "name": name,
                "url": stream.get("url"),
                "enabled": stream.get("enabled", True),
                "models": stream.get("models") or [],
            }

    @staticmethod
    def _species_week(summary: list[dict[str, Any]]) -> int:
        """Distinct species heard in the last 7 days, from all-time summary."""
        if not summary:
            return 0
        cutoff = dt_util.utcnow().timestamp() - 7 * 86400
        count = 0
        for entry in summary:
            last = entry.get("last_heard")
            if not last:
                continue
            try:
                ts = datetime.fromisoformat(last)
            except (TypeError, ValueError):
                continue
            if ts.timestamp() >= cutoff:
                count += 1
        return count

    # --- push half -------------------------------------------------------

    def source_key_for(self, detection: dict[str, Any]) -> str:
        """Resolve a detection to a source key across both payload shapes.

        REST/SSE detections carry ``source: {id, displayName}``; the older
        pending/MQTT framing carries a flat ``source`` string plus ``sourceID``.
        """
        src = detection.get("source")
        name: str | None = None
        if isinstance(src, dict):
            name = src.get("displayName") or src.get("name")
        elif isinstance(src, str):
            name = src
        return slugify_source(name or detection.get("sourceID") or "unknown")

    def record_detection(self, detection: dict[str, Any]) -> str:
        """Fold a confirmed detection into per-source state."""
        key = self.source_key_for(detection)
        self.last_detection[key] = detection
        self.last_detection_any = detection
        self.sources.setdefault(
            key, {"name": self._display_name(detection, key), "url": None, "enabled": True}
        )
        return key

    @staticmethod
    def _display_name(detection: dict[str, Any], fallback: str) -> str:
        src = detection.get("source")
        if isinstance(src, dict) and src.get("displayName"):
            return str(src["displayName"])
        if isinstance(src, str) and src:
            return src
        return fallback.replace("_", " ").title()
