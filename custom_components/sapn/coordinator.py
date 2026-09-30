"""Fetch, cache and import SAPN interval data for one NMI."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CYCLE_DAYS,
    CONF_CYCLE_START,
    CONF_DAYS_BACK,
    CONF_NEM12_TZ,
    CONF_NMI,
    DEFAULT_CYCLE_DAYS,
    DEFAULT_DAYS_BACK,
    DEFAULT_NEM12_TZ,
    DOMAIN,
    EXPORT_SUFFIX,
    IMPORT_SUFFIX,
    STORE_RETENTION_DAYS,
    STORE_VERSION,
)
from .importer import async_import, async_last_day
from .nem12 import Nem12Error, nem12_days, parse_nem12
from .portal import SapnAuthError, SapnError, fetch_nem12
from .tariff import (
    bill_report,
    ceil_hour,
    cycle_bounds,
    expected_intervals,
    floor_hour,
    hourly_increments,
    local_midnight,
    price_intervals,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


def parse_offset(value: str) -> tzinfo:
    """Parse a fixed UTC offset such as +10:00."""
    match = re.fullmatch(r"([+-])(\d{1,2}):(\d{2})", value.strip())
    if not match:
        raise ValueError(f"Invalid UTC offset {value!r}")
    sign = 1 if match.group(1) == "+" else -1
    return timezone(sign * timedelta(hours=int(match.group(2)), minutes=int(match.group(3))))


def parse_run_times(value: str) -> list[time]:
    """Parse "10:15,22:15" into times. Raises ValueError on bad input."""
    times = [time.fromisoformat(part.strip()) for part in value.split(",") if part.strip()]
    if not times:
        raise ValueError("No run times given")
    return times


@dataclass
class SapnStatus:
    """What the last run did, for the diagnostic sensors."""

    last_attempt: datetime | None = None
    last_success: datetime | None = None
    data_through: datetime | None = None
    hours_imported: int = 0
    import_first: datetime | None = None
    import_last: datetime | None = None
    error: str | None = None
    cycle: dict[str, Any] | None = None
    previous_cycle: dict[str, Any] | None = None
    projected_total: float | None = None


class SapnCoordinator(DataUpdateCoordinator[SapnStatus]):
    """Owns the NEM12 cache and every import for one config entry."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_NMI]}",
            update_interval=None,
        )
        self.nmi: str = entry.data[CONF_NMI]
        self.status = SapnStatus()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{DOMAIN}.{self.nmi[:10].lower()}"
        )
        self._days: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def nem12_tz(self) -> tzinfo:
        return parse_offset(self.config_entry.options.get(CONF_NEM12_TZ, DEFAULT_NEM12_TZ))

    @property
    def days_back(self) -> int:
        return int(self.config_entry.options.get(CONF_DAYS_BACK, DEFAULT_DAYS_BACK))

    @property
    def cycle_anchor(self) -> date | None:
        value = self.config_entry.options.get(CONF_CYCLE_START)
        return date.fromisoformat(value) if value else None

    @property
    def cycle_days(self) -> int:
        return int(self.config_entry.options.get(CONF_CYCLE_DAYS, DEFAULT_CYCLE_DAYS))

    async def _async_update_data(self) -> SapnStatus:
        return self.status

    # ----------------------------------------------------------------- cache
    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        self._days = stored.get("days", {})
        if last := stored.get("last_success"):
            self.status.last_success = dt_util.parse_datetime(last)
        self.status.data_through = self._data_through()
        await self._async_update_cycles()
        self.data = self.status

    def _data_through(self) -> datetime | None:
        latest: datetime | None = None
        for day_iso, entry in self._days.items():
            step = timedelta(minutes=entry["step"])
            for index in range(len(entry["imp"]) - 1, -1, -1):
                if entry["imp"][index] is not None or entry["exp"][index] is not None:
                    naive = datetime.combine(date.fromisoformat(day_iso), time()) + (index + 1) * step
                    end = naive.replace(tzinfo=self.nem12_tz).astimezone(UTC)
                    latest = end if latest is None or end > latest else latest
                    break
        return latest

    async def _async_save(self) -> None:
        cutoff = (dt_util.now().date() - timedelta(days=STORE_RETENTION_DAYS)).isoformat()
        self._days = {d: v for d, v in self._days.items() if d >= cutoff}
        await self._store.async_save(
            {
                "days": self._days,
                "last_success": self.status.last_success.isoformat()
                if self.status.last_success
                else None,
            }
        )

    def _ingest(self, text: str) -> tuple[date, date]:
        """Merge NEM12 text into the cache. Returns the NEM12 date range."""
        days = nem12_days(parse_nem12(text), self.nmi, IMPORT_SUFFIX, EXPORT_SUFFIX)
        if not days:
            raise Nem12Error("The NEM12 data held no intervals for this NMI")
        for day, entry in days.items():
            self._days[day.isoformat()] = entry
        return min(days), max(days)

    def _intervals(
        self, first: date | None, last: date | None, tz: tzinfo
    ) -> list[tuple[datetime, float, float]]:
        """Cached intervals whose NEM12 date falls in [first, last]."""
        out: list[tuple[datetime, float, float]] = []
        for day_iso in sorted(self._days):
            day = date.fromisoformat(day_iso)
            if (first and day < first) or (last and day > last):
                continue
            entry = self._days[day_iso]
            step = timedelta(minutes=entry["step"])
            midnight = datetime.combine(day, time())
            for index, (imp, exp) in enumerate(zip(entry["imp"], entry["exp"], strict=True)):
                if imp is None and exp is None:
                    continue
                start = (midnight + index * step).replace(tzinfo=tz).astimezone(UTC)
                out.append((start, imp or 0.0, exp or 0.0))
        return out

    # ---------------------------------------------------------------- cycles
    def _compute_cycles(
        self, today: date, local_tz: tzinfo
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, float | None]:
        """Bill so far this cycle, the previous full cycle, and a projection.

        Only local days with every interval count, so SAPN's one-day lag never
        shows up as a half-billed day.
        """
        anchor, length = self.cycle_anchor, self.cycle_days
        if anchor is None or not self._days:
            return None, None, None
        start, end = cycle_bounds(anchor, length, today)
        previous_start = start - timedelta(days=length)
        intervals = self._intervals(previous_start - timedelta(days=2), None, self.nem12_tz)
        if not intervals:
            return None, None, None
        priced = price_intervals(intervals, local_tz)
        counts = Counter(interval.day for interval in priced.intervals)
        last_complete = next(
            (
                day
                for day in (today - timedelta(days=n) for n in range(2 * length + 1))
                if counts.get(day, 0) >= expected_intervals(day, local_tz, priced.step)
            ),
            None,
        )
        if last_complete is None:
            return None, None, None
        current = bill_report(priced, start, min(last_complete, end), local_tz)
        current.update(
            cycle_start=start.isoformat(),
            cycle_end=end.isoformat(),
            days_in_cycle=length,
            data_to=last_complete.isoformat() if last_complete >= start else None,
        )
        projected = round(current["total"] / current["days"] * length, 2) if current["days"] > 0 else None
        previous = bill_report(priced, previous_start, start - timedelta(days=1), local_tz)
        previous["complete"] = not previous["incomplete_days"]
        return current, previous, projected

    async def _async_update_cycles(self) -> None:
        local_tz = dt_util.get_default_time_zone()
        current, previous, projected = await self.hass.async_add_executor_job(
            self._compute_cycles, dt_util.now().date(), local_tz
        )
        self.status.cycle = current
        self.status.previous_cycle = previous
        self.status.projected_total = projected

    # ------------------------------------------------------------------ runs
    async def async_fetch(self, start: date | None = None, download: bool = True) -> SapnStatus:
        """Download (optionally) and import from `start`, or from DAYS_BACK ago."""
        async with self._lock:
            local_tz = dt_util.get_default_time_zone()
            today = dt_util.now().date()
            self.status.last_attempt = dt_util.utcnow()
            try:
                if start is None:
                    start = today - timedelta(days=self.days_back)
                    last = await async_last_day(self.hass, self.nmi, local_tz)
                    if last is not None and last < start:
                        start = last
                if download:
                    text = await self.hass.async_add_executor_job(
                        fetch_nem12,
                        self.config_entry.data[CONF_EMAIL],
                        self.config_entry.data[CONF_PASSWORD],
                        self.nmi,
                        start - timedelta(days=2),
                        today + timedelta(days=1),
                    )
                    self._ingest(text)
                await self._async_import_from(start, local_tz)
            except SapnAuthError as err:
                self.status.error = f"SAPN rejected the login: {err}"
                self.config_entry.async_start_reauth(self.hass)
            except (SapnError, Nem12Error, ValueError) as err:
                self.status.error = str(err)
                _LOGGER.warning("SAPN import failed: %s", err)
            else:
                self.status.error = None
                self.status.last_success = dt_util.utcnow()
            self.status.data_through = self._data_through()
            await self._async_save()
            await self._async_update_cycles()
            self.async_set_updated_data(self.status)
            return self.status

    async def async_import_text(self, text: str) -> SapnStatus:
        """Import a NEM12 file the user downloaded themselves."""
        async with self._lock:
            local_tz = dt_util.get_default_time_zone()
            self.status.last_attempt = dt_util.utcnow()
            try:
                first, _last = self._ingest(text)
                await self._async_import_from(first, local_tz)
            except (SapnError, Nem12Error, ValueError) as err:
                self.status.error = str(err)
                raise
            else:
                self.status.error = None
                self.status.last_success = dt_util.utcnow()
            finally:
                self.status.data_through = self._data_through()
                await self._async_save()
                await self._async_update_cycles()
                self.async_set_updated_data(self.status)
            return self.status

    async def _async_import_from(self, start: date, local_tz: tzinfo) -> None:
        intervals = self._intervals(start - timedelta(days=2), None, self.nem12_tz)
        if not intervals:
            raise SapnError(f"No cached SAPN data from {start}")
        priced = price_intervals(intervals, local_tz)
        increments = hourly_increments(priced, local_tz)
        first_row = floor_hour(local_midnight(start, local_tz))
        first_full = ceil_hour(intervals[0][0])
        result = await async_import(self.hass, self.nmi, increments, max(first_row, first_full))
        if result is None:
            raise SapnError(f"No complete hours to import from {start}")
        self.status.import_first, self.status.import_last, self.status.hours_imported = result
        _LOGGER.info(
            "Imported %d SAPN hours for %s (%s to %s UTC)",
            result[2], self.nmi, result[0].isoformat(), result[1].isoformat(),
        )

    async def async_bill_report(
        self, first: date, last: date, nem12_tz: str | None = None
    ) -> dict[str, Any]:
        """Rebuild a billing period from the cache."""
        tz = parse_offset(nem12_tz) if nem12_tz else self.nem12_tz
        local_tz = dt_util.get_default_time_zone()
        intervals = self._intervals(first - timedelta(days=1), last + timedelta(days=1), tz)
        report = bill_report(price_intervals(intervals, local_tz), first, last, local_tz)
        offset = tz.utcoffset(None)
        hours, rem = divmod(int(offset.total_seconds()) if offset else 0, 3600)
        report["nmi"] = self.nmi
        report["nem12_tz"] = f"{'+' if hours >= 0 else '-'}{abs(hours):02d}:{rem // 60:02d}"
        return report
