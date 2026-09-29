"""SA Power Networks meter data: NEM12 interval data as Home Assistant statistics."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_RUN_TIMES,
    DEFAULT_RUN_TIMES,
    DOMAIN,
    NEM12_TZ_OPTIONS,
    STARTUP_DELAY_SECONDS,
)
from .coordinator import SapnCoordinator, parse_run_times
from .nem12 import Nem12Error
from .portal import SapnError

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type SapnConfigEntry = ConfigEntry[SapnCoordinator]

ATTR_ENTRY = "config_entry_id"
SERVICE_FETCH = "fetch"
SERVICE_IMPORT_FILE = "import_file"
SERVICE_BILL_REPORT = "bill_report"

FETCH_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY): cv.string,
        vol.Optional("start_date"): cv.date,
        vol.Optional("download", default=True): cv.boolean,
    }
)
IMPORT_FILE_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_ENTRY): cv.string, vol.Required("path"): cv.string}
)
BILL_REPORT_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY): cv.string,
        vol.Required("start_date"): cv.date,
        vol.Required("end_date"): cv.date,
        vol.Optional("nem12_tz"): vol.In(NEM12_TZ_OPTIONS),
    }
)


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> SapnCoordinator:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    wanted = call.data.get(ATTR_ENTRY)
    if wanted:
        entries = [entry for entry in entries if entry.entry_id == wanted]
    if len(entries) != 1:
        raise ServiceValidationError(
            "Pass config_entry_id: found "
            f"{len(entries)} loaded SAPN entries matching this call"
        )
    return entries[0].runtime_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""

    async def fetch(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        status = await coordinator.async_fetch(
            start=call.data.get("start_date"), download=call.data["download"]
        )
        if status.error:
            raise HomeAssistantError(status.error)

    async def import_file(call: ServiceCall) -> None:
        coordinator = _coordinator(hass, call)
        path = call.data["path"]
        if not hass.config.is_allowed_path(path):
            raise ServiceValidationError(
                f"{path} is not in an allowed directory. Put the file under /media."
            )
        try:
            text = await hass.async_add_executor_job(Path(path).read_text, "utf-8")
            await coordinator.async_import_text(text)
        except OSError as err:
            raise HomeAssistantError(f"Cannot read {path}: {err}") from err
        except (Nem12Error, SapnError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err

    async def bill_report(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        first: date = call.data["start_date"]
        last: date = call.data["end_date"]
        if last < first:
            raise ServiceValidationError("end_date is before start_date")
        report: dict[str, Any] = await coordinator.async_bill_report(
            first, last, call.data.get("nem12_tz")
        )
        return report

    hass.services.async_register(DOMAIN, SERVICE_FETCH, fetch, schema=FETCH_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_IMPORT_FILE, import_file, schema=IMPORT_FILE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_BILL_REPORT,
        bill_report,
        schema=BILL_REPORT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SapnConfigEntry) -> bool:
    """Set up one SAPN meter."""
    coordinator = SapnCoordinator(hass, entry)
    await coordinator.async_load()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _scheduled_run(_now: datetime | None = None) -> None:
        entry.async_create_background_task(
            hass, coordinator.async_fetch(), f"{DOMAIN} scheduled fetch"
        )

    for slot in parse_run_times(entry.options.get(CONF_RUN_TIMES, DEFAULT_RUN_TIMES)):
        entry.async_on_unload(
            async_track_time_change(
                hass, _scheduled_run, hour=slot.hour, minute=slot.minute, second=0
            )
        )
    entry.async_on_unload(async_call_later(hass, STARTUP_DELAY_SECONDS, _scheduled_run))
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: SapnConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SapnConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
