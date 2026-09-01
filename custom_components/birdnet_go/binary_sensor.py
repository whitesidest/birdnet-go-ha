"""Binary sensor platform for BirdNET-Go."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_DETECTION, SIGNAL_STREAM_STATE, SOURCE_ACTIVE_WINDOW
from .coordinator import BirdNetCoordinator
from .entity import BirdNetHubEntity, BirdNetSourceEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up BirdNET-Go binary sensors."""
    coordinator: BirdNetCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[BinarySensorEntity] = [
        BirdNetOnlineSensor(coordinator, entry.entry_id),
        BirdNetStreamSensor(coordinator, entry.entry_id),
    ]

    known: set[str] = set()

    def _source_entities(keys: list[str]) -> list[BinarySensorEntity]:
        built: list[BinarySensorEntity] = []
        for key in keys:
            if key in known:
                continue
            known.add(key)
            built.append(BirdNetSourceActiveSensor(coordinator, entry.entry_id, key))
        return built

    entities.extend(_source_entities(list(coordinator.sources)))
    async_add_entities(entities)

    @callback
    def _discover(_arg: Any = None) -> None:
        if new := _source_entities(list(coordinator.sources)):
            async_add_entities(new)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_DETECTION}_{entry.entry_id}", _discover)
    )
    entry.async_on_unload(coordinator.async_add_listener(_discover))


class BirdNetOnlineSensor(BirdNetHubEntity, BinarySensorEntity):
    """Whether BirdNET-Go reports itself healthy."""

    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: BirdNetCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_online"

    @property
    def is_on(self) -> bool:
        health = (self.coordinator.data or {}).get("health") or {}
        return health.get("status") == "healthy"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        health = (self.coordinator.data or {}).get("health") or {}
        return {
            "status": health.get("status"),
            "environment": health.get("environment"),
            "build_date": health.get("build_date"),
        }


class BirdNetStreamSensor(BirdNetHubEntity, BinarySensorEntity):
    """Whether the live SSE detection stream is currently connected.

    Diagnostic: if this is off, detections are not arriving in real time even
    though the polled counters keep updating.
    """

    _attr_name = "Detection Stream"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BirdNetCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_stream_connected"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_STREAM_STATE}_{self._entry_id}",
                self._on_state,
            )
        )

    @callback
    def _on_state(self, _connected: bool) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.coordinator.stream_connected


class BirdNetSourceActiveSensor(BirdNetSourceEntity, BinarySensorEntity):
    """Whether this source produced a detection recently.

    Birds are bursty, so this is a liveness hint over a generous window, not a
    silence detector — a quiet microphone at night is normal, not a fault.
    """

    _attr_name = "Recently Active"
    _attr_device_class = BinarySensorDeviceClass.SOUND

    def __init__(
        self, coordinator: BirdNetCoordinator, entry_id: str, source_key: str
    ) -> None:
        super().__init__(coordinator, entry_id, source_key)
        self._attr_unique_id = f"{entry_id}_{source_key}_recently_active"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DETECTION}_{self._entry_id}",
                self._on_detection,
            )
        )

    @callback
    def _on_detection(self, source_key: str) -> None:
        if source_key == self._source_key:
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        det = self.detection
        if not det:
            return False
        ts = dt_util.parse_datetime(str(det.get("timestamp") or ""))
        if ts is None:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt_util.UTC)
        return (dt_util.utcnow() - dt_util.as_utc(ts)) < SOURCE_ACTIVE_WINDOW

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "stream_url": self._source.get("url"),
            "enabled": self._source.get("enabled"),
            "models": self._source.get("models"),
        }
