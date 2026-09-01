"""The BirdNET-Go integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from datetime import timedelta

from homeassistant.helpers.event import async_track_time_interval

from .api import BirdNetClient
from .audio_stream import BirdNetAudioLevelStream
from .const import (
    CONF_AUDIO_LEVEL_INTERVAL,
    CONF_HOST,
    CONF_MIN_CONFIDENCE,
    CONF_PORT,
    CONF_SSL,
    CONF_TOKEN,
    CONF_VERIFY_SSL,
    DEFAULT_AUDIO_LEVEL_INTERVAL,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_PORT,
    DOMAIN,
    EVENT_DETECTION,
    EVENT_NEW_SPECIES,
    SIGNAL_AUDIO_LEVEL,
    SIGNAL_DETECTION,
    SIGNAL_STREAM_STATE,
)
from .coordinator import BirdNetCoordinator
from .stream import BirdNetStream

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


def build_base_url(data: dict[str, Any]) -> str:
    scheme = "https" if data.get(CONF_SSL) else "http"
    return f"{scheme}://{data[CONF_HOST]}:{data.get(CONF_PORT, DEFAULT_PORT)}"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BirdNET-Go from a config entry."""
    session = async_get_clientsession(hass)
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, True)
    client = BirdNetClient(
        session,
        build_base_url(entry.data),
        token=entry.data.get(CONF_TOKEN) or None,
        verify_ssl=verify_ssl,
    )

    coordinator = BirdNetCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    min_confidence = entry.options.get(
        CONF_MIN_CONFIDENCE, entry.data.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE)
    )

    @callback
    def _handle_detection(detection: dict[str, Any]) -> None:
        """Fold a pushed detection into state and fire event-bus events."""
        try:
            confidence = float(detection.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < min_confidence:
            return

        source_key = coordinator.record_detection(detection)
        payload = {
            "source": coordinator.sources.get(source_key, {}).get("name", source_key),
            "source_key": source_key,
            "common_name": detection.get("commonName"),
            "scientific_name": detection.get("scientificName"),
            "species_code": detection.get("speciesCode"),
            "confidence": confidence,
            "detection_id": detection.get("id"),
            "timestamp": detection.get("timestamp"),
            "days_since_first_seen": detection.get("daysSinceFirstSeen"),
            "days_this_year": detection.get("daysThisYear"),
            "days_this_season": detection.get("daysThisSeason"),
            "current_season": detection.get("currentSeason"),
        }
        hass.bus.async_fire(EVENT_DETECTION, payload)

        # daysSinceFirstSeen == 0 means BirdNET-Go had never recorded this
        # species before today — the "lifer" case worth notifying on.
        if detection.get("daysSinceFirstSeen") == 0:
            hass.bus.async_fire(EVENT_NEW_SPECIES, payload)

        async_dispatcher_send(hass, f"{SIGNAL_DETECTION}_{entry.entry_id}", source_key)

    @callback
    def _handle_stream_state(connected: bool) -> None:
        coordinator.stream_connected = connected
        async_dispatcher_send(hass, f"{SIGNAL_STREAM_STATE}_{entry.entry_id}", connected)

    stream = BirdNetStream(
        session,
        client.base_url,
        client.headers,
        on_detection=_handle_detection,
        on_state=_handle_stream_state,
        verify_ssl=verify_ssl,
    )
    stream.start()

    # --- audio levels -----------------------------------------------------
    # The upstream endpoint fires ~16x/sec. Accumulate in memory and flush a
    # summary on a timer so the recorder isn't flooded; interval 0 opts out.
    audio_interval = int(
        entry.options.get(CONF_AUDIO_LEVEL_INTERVAL, DEFAULT_AUDIO_LEVEL_INTERVAL)
    )
    audio_stream: BirdNetAudioLevelStream | None = None
    cancel_flush = None

    if audio_interval > 0:

        @callback
        def _handle_levels(levels: dict[str, Any]) -> None:
            coordinator.record_audio_levels(levels)

        @callback
        def _handle_audio_state(connected: bool) -> None:
            coordinator.audio_stream_connected = connected

        audio_stream = BirdNetAudioLevelStream(
            session,
            client.base_url,
            client.headers,
            on_levels=_handle_levels,
            on_state=_handle_audio_state,
            verify_ssl=verify_ssl,
        )
        audio_stream.start()

        @callback
        def _flush(_now: Any) -> None:
            if changed := coordinator.flush_audio_levels():
                async_dispatcher_send(
                    hass, f"{SIGNAL_AUDIO_LEVEL}_{entry.entry_id}", changed
                )

        cancel_flush = async_track_time_interval(
            hass, _flush, timedelta(seconds=audio_interval)
        )
        entry.async_on_unload(cancel_flush)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
        "stream": stream,
        "audio_stream": audio_stream,
        "audio_interval": audio_interval,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    stored = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if stored:
        if stream := stored.get("stream"):
            await stream.stop()
        if audio_stream := stored.get("audio_stream"):
            await audio_stream.stop()
    return unload_ok
