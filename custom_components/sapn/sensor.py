"""Diagnostic sensors for the SAPN meter data integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import SapnConfigEntry
from .const import DOMAIN
from .coordinator import SapnCoordinator, SapnStatus


@dataclass(frozen=True, kw_only=True)
class SapnSensorDescription(SensorEntityDescription):
    """Describes a SAPN diagnostic sensor."""

    value_fn: Callable[[SapnStatus], datetime | None]


SENSORS = (
    SapnSensorDescription(
        key="data_through",
        translation_key="data_through",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.data_through,
    ),
    SapnSensorDescription(
        key="last_success",
        translation_key="last_success",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.last_success,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SapnConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the diagnostic sensors."""
    coordinator = entry.runtime_data
    async_add_entities(SapnSensor(coordinator, description) for description in SENSORS)


class SapnSensor(CoordinatorEntity[SapnCoordinator], SensorEntity):
    """A diagnostic timestamp from the last SAPN run."""

    _attr_has_entity_name = True
    entity_description: SapnSensorDescription

    def __init__(self, coordinator: SapnCoordinator, description: SapnSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        nmi = coordinator.nmi[:10]
        self._attr_unique_id = f"{nmi}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, nmi)},
            name=f"SAPN meter {coordinator.nmi}",
            manufacturer="SA Power Networks",
            model="NEM12 interval data",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://customer.portal.sapowernetworks.com.au/meterdata/",
        )

    @property
    def native_value(self) -> datetime | None:
        return self.entity_description.value_fn(self.coordinator.status)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "last_success":
            return None
        status = self.coordinator.status
        return {
            "last_attempt": status.last_attempt.isoformat() if status.last_attempt else None,
            "error": status.error,
            "hours_imported": status.hours_imported,
            "import_first": status.import_first.isoformat() if status.import_first else None,
            "import_last": status.import_last.isoformat() if status.import_last else None,
        }
