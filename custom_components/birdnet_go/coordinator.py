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
        # source_key -> {"level": int, "clipping": bool} published to entities
        self.audio_levels: dict[str, dict[str, Any]] = {}
        self.audio_stream_connected = False
        # in-memory accumulator between flushes; never read by entities
        self._audio_accum: dict[str, dict[str, Any]] = {}

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

        # Detections only arrive by push, so on a cold start every per-source
        # sensor would sit at `unknown` until that particular microphone next
        # hears something — potentially hours, and again after every restart.
        # Seed from history instead. This only ever fills gaps: a source that
        # has already received a pushed detection is left alone, so live SSE
        # data is never overwritten by a staler REST snapshot.
        try:
            await self._backfill_last_detections()
        except BirdNetApiError as err:
            _LOGGER.debug("detection backfill unavailable: %s", err)

        detections_today = sum(int(s.get("count") or 0) for s in daily)
        species_today = len([s for s in daily if (s.get("count") or 0) > 0])
        new_today = [
            s for s in daily if s.get("days_since_first_seen") == 0
        ]

        week_list = self._species_week_list(summary)

        return {
            "health": health,
            "daily": daily,
            "summary": summary,
            "detections_today": detections_today,
            "species_today": species_today,
            "species_today_list": self._species_today_list(daily),
            "species_total": len(summary),
            "species_week": len(week_list),
            "species_week_list": week_list,
            "new_species_today": len(new_today),
            "new_species_today_names": [s.get("common_name") for s in new_today],
        }

    @staticmethod
    def _species_today_list(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Compact per-species rows for dashboard tables, busiest first."""
        rows = [
            {
                "species": entry.get("common_name"),
                "scientific_name": entry.get("scientific_name"),
                "count": int(entry.get("count") or 0),
                "last": entry.get("latest_heard"),
                "conf": round(float(entry.get("max_confidence") or 0) * 100),
                "new": entry.get("days_since_first_seen") == 0,
            }
            for entry in daily
            if (entry.get("count") or 0) > 0
        ]
        return sorted(rows, key=lambda r: r["count"], reverse=True)

    def _species_week_list(self, summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Same shape as the daily rows, for species heard in the last 7 days."""
        cutoff = dt_util.utcnow().timestamp() - 7 * 86400
        rows = []
        for entry in summary:
            ts = self._parse_ts(entry.get("last_heard"))
            if ts is None or ts < cutoff:
                continue
            rows.append(
                {
                    "species": entry.get("common_name"),
                    "scientific_name": entry.get("scientific_name"),
                    "count": int(entry.get("count") or 0),
                    "last": entry.get("last_heard"),
                    "conf": round(float(entry.get("max_confidence") or 0) * 100),
                }
            )
        return sorted(rows, key=lambda r: r["count"], reverse=True)

    @staticmethod
    def _parse_ts(value: Any) -> float | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)).timestamp()
        except (TypeError, ValueError):
            return None



    # --- audio levels ----------------------------------------------------

    def record_audio_levels(self, levels: dict[str, Any]) -> None:
        """Fold one high-rate audio-level message into the accumulator.

        Called ~16x/sec, so this must stay cheap and must not touch entity
        state. Keeps the window PEAK and latches clipping.
        """
        for entry in levels.values():
            if not isinstance(entry, dict):
                continue
            key = slugify_source(entry.get("name") or entry.get("source") or "")
            if key == "unknown":
                continue
            try:
                level = int(entry.get("level") or 0)
            except (TypeError, ValueError):
                level = 0
            slot = self._audio_accum.setdefault(key, {"level": 0, "clipping": False})
            if level > slot["level"]:
                slot["level"] = level
            if entry.get("clipping"):
                slot["clipping"] = True

    def flush_audio_levels(self) -> set[str]:
        """Publish the accumulated window; return the source keys that changed."""
        changed: set[str] = set()
        for key, slot in self._audio_accum.items():
            previous = self.audio_levels.get(key)
            if previous != slot:
                self.audio_levels[key] = dict(slot)
                changed.add(key)
        # Sources that went completely silent stop appearing in the stream's
        # payload only if removed upstream; a silent mic still reports level 0,
        # so resetting here is what makes peak-per-window meaningful.
        self._audio_accum = {}
        return changed

    async def _backfill_last_detections(self) -> None:
        """Fill in per-source state from recent history without clobbering push."""
        missing = {k for k in self.sources if k not in self.last_detection}
        if not missing and self.last_detection_any is not None:
            return

        recent = await self.client.recent_detections()
        if not recent:
            return

        # Oldest first so the newest detection per source ends up winning.
        for det in sorted(recent, key=lambda d: str(d.get("timestamp") or "")):
            key = self.source_key_for(det)
            if key in missing:
                self.last_detection[key] = det

        if self.last_detection_any is None:
            newest = max(recent, key=lambda d: str(d.get("timestamp") or ""))
            self.last_detection_any = newest

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
