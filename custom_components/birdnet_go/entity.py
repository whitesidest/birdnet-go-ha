"""Base entities for the BirdNET-Go integration."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import BirdNetCoordinator


class BirdNetHubEntity(CoordinatorEntity[BirdNetCoordinator]):
    """Entity bound to the BirdNET-Go server itself."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BirdNetCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id

    @property
    def device_info(self) -> DeviceInfo:
        info = self.coordinator.server_info or {}
        health = (self.coordinator.data or {}).get("health") or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry_id)},
            manufacturer=MANUFACTURER,
            name="BirdNET-Go",
            model=info.get("os_display") or "BirdNET-Go",
            sw_version=str(health.get("version") or ""),
            configuration_url=self.coordinator.client.base_url,
        )


class BirdNetSourceEntity(CoordinatorEntity[BirdNetCoordinator]):
    """Entity bound to a single audio source (one microphone / RTSP stream).

    Each source is its own device. This is the structural fix for the MQTT
    setup it replaces, where all sources shared one topic and every detection
    blanked the sources it did not match.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: BirdNetCoordinator, entry_id: str, source_key: str
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._source_key = source_key

    @property
    def _source(self) -> dict[str, Any]:
        return self.coordinator.sources.get(self._source_key, {})

    @property
    def source_name(self) -> str:
        return self._source.get("name") or self._source_key.replace("_", " ").title()

    @property
    def detection(self) -> dict[str, Any] | None:
        return self.coordinator.last_detection.get(self._source_key)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}_{self._source_key}")},
            manufacturer=MANUFACTURER,
            name=self.source_name,
            model="Audio Source",
            via_device=(DOMAIN, self._entry_id),
        )
