"""Sensor platform for BirdNET-Go."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_DETECTION
from .coordinator import BirdNetCoordinator, parse_uptime
from .entity import BirdNetHubEntity, BirdNetSourceEntity

_LOGGER = logging.getLogger(__name__)


def _iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = dt_util.parse_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    return dt_util.as_utc(parsed) if parsed.tzinfo else parsed.replace(tzinfo=dt_util.UTC)


# --- hub sensors ---------------------------------------------------------
# key, name, icon, unit, device_class, state_class, entity_category, value_fn

HUB_SENSORS: list[dict[str, Any]] = [
    {
        "key": "species_today",
        "name": "Species Today",
        "icon": "mdi:bird",
        "state_class": SensorStateClass.MEASUREMENT,
        "value": lambda d: d.get("species_today"),
        # `species` is a table-ready list (species/count/last/conf/new) so
        # dashboards can render a breakdown without extra template sensors.
        "attrs": lambda d: {
            "species": d.get("species_today_list") or [],
            "detections": d.get("detections_today") or 0,
        },
    },
    {
        "key": "species_week",
        "name": "Species This Week",
        "icon": "mdi:calendar-week",
        "state_class": SensorStateClass.MEASUREMENT,
        "value": lambda d: d.get("species_week"),
        "attrs": lambda d: {"species": d.get("species_week_list") or []},
    },
    {
        "key": "species_total",
        "name": "Species All Time",
        "icon": "mdi:format-list-bulleted",
        "state_class": SensorStateClass.MEASUREMENT,
        "value": lambda d: d.get("species_total"),
    },
    {
        "key": "detections_today",
        "name": "Detections Today",
        "icon": "mdi:counter",
        "state_class": SensorStateClass.MEASUREMENT,
        "value": lambda d: d.get("detections_today"),
    },
    {
        "key": "new_species_today",
        "name": "New Species Today",
        "icon": "mdi:star-outline",
        "state_class": SensorStateClass.MEASUREMENT,
        "value": lambda d: d.get("new_species_today"),
        "attrs": lambda d: {"species": d.get("new_species_today_names") or []},
    },
    {
        "key": "cpu_usage",
        "name": "CPU Usage",
        "unit": PERCENTAGE,
        "icon": "mdi:cpu-64-bit",
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "value": lambda d: round(
            float(((d.get("health") or {}).get("system") or {}).get("cpu_usage") or 0), 1
        ),
    },
    {
        "key": "memory_usage",
        "name": "Memory Usage",
        "unit": PERCENTAGE,
        "icon": "mdi:memory",
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "value": lambda d: round(
            float(
                (((d.get("health") or {}).get("system") or {}).get("memory") or {}).get(
                    "used_percent"
                )
                or 0
            ),
            1,
        ),
    },
    {
        "key": "disk_free",
        "name": "Disk Free",
        "unit": UnitOfInformation.GIGABYTES,
        "device_class": SensorDeviceClass.DATA_SIZE,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "value": lambda d: round(
            float(
                (
                    ((d.get("health") or {}).get("system") or {}).get("disk_space") or {}
                ).get("free_gb")
                or 0
            ),
            2,
        ),
    },
    {
        "key": "uptime",
        "name": "Uptime",
        "unit": UnitOfTime.SECONDS,
        "device_class": SensorDeviceClass.DURATION,
        "icon": "mdi:clock-outline",
        "entity_category": EntityCategory.DIAGNOSTIC,
        "value": lambda d: (
            round(v) if (v := parse_uptime((d.get("health") or {}).get("uptime_seconds")
                                          or (d.get("health") or {}).get("uptime"))) else None
        ),
    },
    {
        "key": "version",
        "name": "Version",
        "icon": "mdi:tag-outline",
        "entity_category": EntityCategory.DIAGNOSTIC,
        "value": lambda d: (d.get("health") or {}).get("version"),
    },
    {
        "key": "database_status",
        "name": "Database Status",
        "icon": "mdi:database-outline",
        "entity_category": EntityCategory.DIAGNOSTIC,
        "value": lambda d: (d.get("health") or {}).get("database_status"),
    },
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up BirdNET-Go sensors."""
    coordinator: BirdNetCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[SensorEntity] = [
        BirdNetHubSensor(coordinator, entry.entry_id, spec) for spec in HUB_SENSORS
    ]
    entities.append(BirdNetLastDetectionSensor(coordinator, entry.entry_id))

    known: set[str] = set()

    def _source_entities(keys: list[str]) -> list[SensorEntity]:
        built: list[SensorEntity] = []
        for key in keys:
            if key in known:
                continue
            known.add(key)
            built.append(BirdNetSourceSpeciesSensor(coordinator, entry.entry_id, key))
            built.append(BirdNetSourceConfidenceSensor(coordinator, entry.entry_id, key))
            built.append(BirdNetSourceLastHeardSensor(coordinator, entry.entry_id, key))
        return built

    entities.extend(_source_entities(list(coordinator.sources)))
    async_add_entities(entities)

    @callback
    def _discover(_source_key: str | None = None) -> None:
        """Add entities for a source that appeared after setup."""
        new = _source_entities(list(coordinator.sources))
        if new:
            async_add_entities(new)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{SIGNAL_DETECTION}_{entry.entry_id}", _discover)
    )
    entry.async_on_unload(coordinator.async_add_listener(_discover))


class BirdNetHubSensor(BirdNetHubEntity, SensorEntity):
    """A server-level sensor described by a HUB_SENSORS spec."""

    def __init__(
        self, coordinator: BirdNetCoordinator, entry_id: str, spec: dict[str, Any]
    ) -> None:
        super().__init__(coordinator, entry_id)
        self._spec = spec
        self._attr_unique_id = f"{entry_id}_{spec['key']}"
        self._attr_translation_key = spec["key"]
        self._attr_name = spec["name"]
        self._attr_icon = spec.get("icon")
        self._attr_native_unit_of_measurement = spec.get("unit")
        self._attr_device_class = spec.get("device_class")
        self._attr_state_class = spec.get("state_class")
        self._attr_entity_category = spec.get("entity_category")

    @property
    def native_value(self) -> Any:
        try:
            return self._spec["value"](self.coordinator.data or {})
        except (TypeError, ValueError, AttributeError):  # defensive
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if fn := self._spec.get("attrs"):
            try:
                return fn(self.coordinator.data or {})
            except (TypeError, ValueError, AttributeError):
                return None
        return None


class BirdNetLastDetectionSensor(BirdNetHubEntity, SensorEntity):
    """Most recent detection across every source."""

    _attr_name = "Last Detection"
    _attr_icon = "mdi:bird"

    def __init__(self, coordinator: BirdNetCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_last_detection"

    @property
    def native_value(self) -> str | None:
        det = self.coordinator.last_detection_any
        return det.get("commonName") if det else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        det = self.coordinator.last_detection_any
        if not det:
            return None
        return _detection_attrs(det, self.coordinator)


class _SourceSensorBase(BirdNetSourceEntity, SensorEntity):
    """Shared wiring: re-render when this source gets a detection."""

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


class BirdNetSourceSpeciesSensor(_SourceSensorBase):
    """Last species heard on this source.

    Unlike the MQTT sensors this replaces, the value PERSISTS: a detection on a
    different microphone can never blank this one.
    """

    _attr_name = "Last Species"
    _attr_icon = "mdi:bird"

    def __init__(
        self, coordinator: BirdNetCoordinator, entry_id: str, source_key: str
    ) -> None:
        super().__init__(coordinator, entry_id, source_key)
        self._attr_unique_id = f"{entry_id}_{source_key}_last_species"

    @property
    def native_value(self) -> str | None:
        det = self.detection
        return det.get("commonName") if det else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        det = self.detection
        if not det:
            return None
        return _detection_attrs(det, self.coordinator)


class BirdNetSourceConfidenceSensor(_SourceSensorBase):
    """Confidence of this source's most recent detection."""

    _attr_name = "Last Confidence"
    _attr_icon = "mdi:percent"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: BirdNetCoordinator, entry_id: str, source_key: str
    ) -> None:
        super().__init__(coordinator, entry_id, source_key)
        self._attr_unique_id = f"{entry_id}_{source_key}_last_confidence"

    @property
    def native_value(self) -> float | None:
        det = self.detection
        if not det:
            return None
        try:
            return round(float(det.get("confidence") or 0) * 100, 1)
        except (TypeError, ValueError):
            return None


class BirdNetSourceLastHeardSensor(_SourceSensorBase):
    """Timestamp of this source's most recent detection."""

    _attr_name = "Last Heard"
    _attr_icon = "mdi:clock-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self, coordinator: BirdNetCoordinator, entry_id: str, source_key: str
    ) -> None:
        super().__init__(coordinator, entry_id, source_key)
        self._attr_unique_id = f"{entry_id}_{source_key}_last_heard"

    @property
    def native_value(self) -> datetime | None:
        det = self.detection
        return _iso(det.get("timestamp")) if det else None


def _detection_attrs(det: dict[str, Any], coordinator: BirdNetCoordinator) -> dict[str, Any]:
    """Attribute payload shared by the species sensors."""
    try:
        confidence = round(float(det.get("confidence") or 0) * 100, 1)
    except (TypeError, ValueError):
        confidence = None
    source_key = coordinator.source_key_for(det)
    return {
        "scientific_name": det.get("scientificName"),
        "species_code": det.get("speciesCode"),
        "confidence": confidence,
        "source": coordinator.sources.get(source_key, {}).get("name", source_key),
        "detection_id": det.get("id"),
        "timestamp": det.get("timestamp"),
        "days_since_first_seen": det.get("daysSinceFirstSeen"),
        "days_this_year": det.get("daysThisYear"),
        "days_this_season": det.get("daysThisSeason"),
        "current_season": det.get("currentSeason"),
        "verified": det.get("verified"),
        # Convenience for picture-glance style cards; resolved against the
        # server's base URL so it works straight from a dashboard.
        "thumbnail_url": (
            f"{coordinator.client.base_url}/api/v2/media/image/"
            f"{det.get('scientificName', '').replace(' ', '%20')}"
            if det.get("scientificName")
            else None
        ),
        "spectrogram_url": (
            f"{coordinator.client.base_url}/api/v2/spectrogram/{det['id']}"
            if det.get("id") is not None
            else None
        ),
        "clip_url": (
            f"{coordinator.client.base_url}/api/v2/media/audio?id={det['id']}"
            if det.get("id") is not None
            else None
        ),
    }
