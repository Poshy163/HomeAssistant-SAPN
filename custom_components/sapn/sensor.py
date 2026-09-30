"""Sensors for the SAPN meter data integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
from .const import CURRENCY, DOMAIN
from .coordinator import SapnCoordinator, SapnStatus


def _bill_attributes(report: dict[str, Any] | None) -> dict[str, Any] | None:
    """Invoice-style attributes shared by the cycle sensors."""
    if report is None:
        return None
    return {
        "start_date": report["start_date"],
        "end_date": report["end_date"],
        "days": report["days"],
        "lines": report["lines"],
        "gst": report["gst"],
        "import_kwh": report["metered"]["import_kwh"],
        "export_kwh": report["metered"]["export_kwh"],
        "zerohero_earned": report["zerohero"]["earned"],
        "zerohero_missed": [item["date"] for item in report["zerohero"]["missed"]],
    }


def _current_attributes(status: SapnStatus) -> dict[str, Any] | None:
    attributes = _bill_attributes(status.cycle)
    if attributes is None or status.cycle is None:
        return attributes
    return {
        **attributes,
        "cycle_start": status.cycle["cycle_start"],
        "cycle_end": status.cycle["cycle_end"],
        "days_in_cycle": status.cycle["days_in_cycle"],
        "data_to": status.cycle["data_to"],
    }


def _previous_attributes(status: SapnStatus) -> dict[str, Any] | None:
    attributes = _bill_attributes(status.previous_cycle)
    if attributes is None or status.previous_cycle is None:
        return attributes
    return {**attributes, "complete": status.previous_cycle["complete"]}


def _import_attributes(status: SapnStatus) -> dict[str, Any]:
    return {
        "last_attempt": status.last_attempt.isoformat() if status.last_attempt else None,
        "error": status.error,
        "hours_imported": status.hours_imported,
        "import_first": status.import_first.isoformat() if status.import_first else None,
        "import_last": status.import_last.isoformat() if status.import_last else None,
    }


@dataclass(frozen=True, kw_only=True)
class SapnSensorDescription(SensorEntityDescription):
    """Describes a SAPN sensor."""

    value_fn: Callable[[SapnStatus], Any]
    attributes_fn: Callable[[SapnStatus], dict[str, Any] | None] = lambda status: None


SENSORS = (
    SapnSensorDescription(
        key="bill_this_cycle",
        translation_key="bill_this_cycle",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda status: status.cycle["total"] if status.cycle else None,
        attributes_fn=_current_attributes,
    ),
    SapnSensorDescription(
        key="projected_bill",
        translation_key="projected_bill",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda status: status.projected_total,
        attributes_fn=lambda status: (
            {"based_on_days": status.cycle["days"], "cycle_end": status.cycle["cycle_end"]}
            if status.cycle
            else None
        ),
    ),
    SapnSensorDescription(
        key="last_bill",
        translation_key="last_bill",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        value_fn=lambda status: status.previous_cycle["total"] if status.previous_cycle else None,
        attributes_fn=_previous_attributes,
    ),
    SapnSensorDescription(
        key="zerohero_this_cycle",
        translation_key="zerohero_this_cycle",
        value_fn=lambda status: (
            len(status.cycle["zerohero"]["earned"]) if status.cycle else None
        ),
        attributes_fn=lambda status: (
            {
                "earned": status.cycle["zerohero"]["earned"],
                "missed": [item["date"] for item in status.cycle["zerohero"]["missed"]],
            }
            if status.cycle
            else None
        ),
    ),
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
        attributes_fn=_import_attributes,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SapnConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(SapnSensor(coordinator, description) for description in SENSORS)


class SapnSensor(CoordinatorEntity[SapnCoordinator], SensorEntity):
    """A value derived from the stored SAPN interval data."""

    _attr_has_entity_name = True
    # Invoice lines and date lists are for cards, not for history.
    _unrecorded_attributes = frozenset(
        {"lines", "zerohero_earned", "zerohero_missed", "earned", "missed"}
    )
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
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.status)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.entity_description.attributes_fn(self.coordinator.status)
