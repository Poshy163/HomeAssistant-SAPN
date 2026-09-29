"""Write SAPN interval data into Home Assistant long-term statistics."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, tzinfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant

from .const import ALL_STATS, CURRENCY, DOMAIN, ENERGY_STATS, MONEY_STATS


def statistic_id(nmi: str, key: str) -> str:
    """Statistic id for one NMI, for example sapn:2001234567_grid_import."""
    return f"{DOMAIN}:{nmi[:10].lower()}_{key}"


def statistic_metadata(nmi: str, key: str) -> StatisticMetaData:
    """Metadata for one SAPN statistic."""
    if key in ENERGY_STATS:
        name, unit, unit_class = ENERGY_STATS[key], "kWh", "energy"
    else:
        name, unit, unit_class = MONEY_STATS[key], CURRENCY, None
    return StatisticMetaData(
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        name=f"SAPN {nmi[:10]} {name}",
        source=DOMAIN,
        statistic_id=statistic_id(nmi, key),
        unit_class=unit_class,
        unit_of_measurement=unit,
    )


async def async_last_day(hass: HomeAssistant, nmi: str, local_tz: tzinfo) -> date | None:
    """Local date of the newest SAPN hour already in the recorder."""
    sid = statistic_id(nmi, "grid_import")
    result = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, sid, False, {"sum"}
    )
    rows = result.get(sid)
    if not rows:
        return None
    return datetime.fromtimestamp(rows[0]["start"], tz=UTC).astimezone(local_tz).date()


async def async_base_sums(hass: HomeAssistant, nmi: str, before: datetime) -> dict[str, float]:
    """Running total of every statistic just before `before`.

    Reads hourly rows: for day, week and month periods the recorder stretches
    end_time to the end of that calendar period, which would pull in later sums.
    """
    wanted = {statistic_id(nmi, key): key for key in ALL_STATS}
    found: dict[str, float] = {}
    for lookback in (timedelta(days=2), timedelta(days=3650)):
        missing = set(wanted) - set(found)
        if not missing:
            break
        result = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            before - lookback,
            before,
            missing,
            "hour",
            None,
            {"sum"},
        )
        for sid in missing:
            rows = [r for r in result.get(sid, []) if r.get("sum") is not None]
            if rows:
                found[sid] = float(rows[-1]["sum"])
    return {key: found.get(sid, 0.0) for sid, key in wanted.items()}


async def async_import(
    hass: HomeAssistant,
    nmi: str,
    increments: dict[datetime, dict[str, float]],
    first_hour: datetime,
) -> tuple[datetime, datetime, int] | None:
    """Import hourly rows from `first_hour` on, continuing each running total."""
    hours = sorted(hour for hour in increments if hour >= first_hour)
    if not hours:
        return None
    bases = await async_base_sums(hass, nmi, hours[0])
    for key in ALL_STATS:
        running = bases[key]
        rows: list[StatisticData] = []
        for hour in hours:
            running += increments[hour][key]
            rows.append(StatisticData(start=hour, state=round(running, 6), sum=round(running, 6)))
        async_add_external_statistics(hass, statistic_metadata(nmi, key), rows)
    await get_instance(hass).async_block_till_done()
    return hours[0], hours[-1], len(hours)
