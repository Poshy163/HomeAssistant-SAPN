"""Imports into a real recorder, re-runs, file imports, the bill report and sensors."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import list_statistic_ids, statistics_during_period
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.sapn.const import ALL_STATS, CONF_NMI, DOMAIN
from custom_components.sapn.coordinator import parse_offset
from custom_components.sapn.importer import statistic_id
from custom_components.sapn.nem12 import nem12_days, parse_nem12
from custom_components.sapn.portal import SapnAuthError
from custom_components.sapn.tariff import bill_report, hourly_increments, price_intervals

ADL = ZoneInfo("Australia/Adelaide")
NMI = "20012345678"
FETCH = "custom_components.sapn.coordinator.fetch_nem12"


def engine(text: str, tz: str = "+10:00"):
    offset = parse_offset(tz)
    intervals = []
    for day, entry in nem12_days(parse_nem12(text), NMI, "E1", "B1").items():
        for index, (imp, exp) in enumerate(zip(entry["imp"], entry["exp"], strict=True)):
            if imp is None and exp is None:
                continue
            naive = datetime.combine(day, datetime.min.time()) + timedelta(minutes=index * entry["step"])
            intervals.append((naive.replace(tzinfo=offset).astimezone(UTC), imp or 0.0, exp or 0.0))
    return price_intervals(intervals, ADL)


def slice_nem12(text: str, first: str | None = None, last: str | None = None) -> str:
    """Keep only 300 records whose YYYYMMDD date is within [first, last]."""
    out = []
    for line in text.splitlines():
        if line.startswith("300,"):
            day = line.split(",")[1]
            if (first and day < first) or (last and day > last):
                continue
        out.append(line)
    return "\n".join(out) + "\n"


async def all_sums(hass, key: str) -> list[tuple[float, float]]:
    sid = statistic_id(NMI, key)
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, datetime(2026, 1, 1, tzinfo=UTC), None, {sid}, "hour", None, {"sum"}
    )
    return [(row["start"], row["sum"]) for row in rows.get(sid, [])]


@pytest.fixture
async def entry(hass, freezer):
    await hass.config.async_set_time_zone("Australia/Adelaide")
    freezer.move_to("2026-09-29T00:30:00+00:00")  # 10:00 in Adelaide
    config_entry = MockConfigEntry(
        domain=DOMAIN, unique_id=NMI[:10], title=f"SAPN {NMI}",
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw", CONF_NMI: NMI},
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def backfill(hass, text: str) -> None:
    with patch(FETCH, return_value=text):
        await hass.services.async_call(DOMAIN, "fetch", {"start_date": "2026-08-31"}, blocking=True)
    await async_wait_recording_done(hass)


async def test_backfill_writes_exactly_what_the_engine_computes(hass, entry, nem12_text):
    with patch(FETCH, return_value=nem12_text) as fetch:
        await hass.services.async_call(DOMAIN, "fetch", {"start_date": "2026-08-31"}, blocking=True)
    await async_wait_recording_done(hass)
    assert fetch.call_args.args[3:] == (date(2026, 8, 29), date(2026, 9, 30))

    listed = await get_instance(hass).async_add_executor_job(list_statistic_ids, hass)
    ours = {item["statistic_id"] for item in listed if item["statistic_id"].startswith("sapn:")}
    assert ours == {statistic_id(NMI, key) for key in ALL_STATS}

    increments = hourly_increments(engine(nem12_text), ADL)
    first_hour = datetime(2026, 8, 30, 14, 0, tzinfo=UTC)  # local midnight 31 Aug, floored to the UTC hour
    for key in ALL_STATS:
        rows = await all_sums(hass, key)
        assert datetime.fromtimestamp(rows[0][0], UTC) == first_hour
        previous = 0.0
        for start, total in rows:
            hour = datetime.fromtimestamp(start, UTC)
            assert total - previous == pytest.approx(increments[hour][key], abs=2e-6), (key, hour)
            previous = total


async def test_scheduled_rerun_leaves_history_unchanged(hass, entry, nem12_text):
    await backfill(hass, nem12_text)
    before = {key: await all_sums(hass, key) for key in ALL_STATS}
    with patch(FETCH, return_value=nem12_text) as fetch:
        await hass.services.async_call(DOMAIN, "fetch", {}, blocking=True)  # default window
    await async_wait_recording_done(hass)
    assert fetch.call_args.args[3] == date(2026, 9, 20)  # today - 7 days - 2 days of lead-in
    for key in ALL_STATS:
        after = await all_sums(hass, key)
        assert [start for start, _ in after] == [start for start, _ in before[key]], key
        worst = max(abs(a - b) for (_, a), (_, b) in zip(after, before[key], strict=True))
        assert worst < 1e-6, (key, worst)


async def test_gap_then_later_file_keeps_totals_rising(hass, entry, nem12_text, tmp_path):
    with patch(FETCH, return_value=slice_nem12(nem12_text, last="20260920")):
        await hass.services.async_call(DOMAIN, "fetch", {"start_date": "2026-08-31"}, blocking=True)
    await async_wait_recording_done(hass)
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    later = tmp_path / "later.csv"
    later.write_text(slice_nem12(nem12_text, first="20260923"))
    await hass.services.async_call(DOMAIN, "import_file", {"path": str(later)}, blocking=True)
    await async_wait_recording_done(hass)
    for key in ("grid_import", "grid_export", "supply_charge", "zerohero_credit", "import_cost"):
        totals = [total for _start, total in await all_sums(hass, key)]
        assert all(b >= a - 1e-9 for a, b in zip(totals, totals[1:], strict=False)), key


async def test_import_file_rejects_paths_outside_allowlist(hass, entry, tmp_path):
    outside = tmp_path / "nope.csv"
    outside.write_text("100,NEM12\n900\n")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "import_file", {"path": str(outside)}, blocking=True)


async def test_bill_report_matches_engine_under_both_timestamp_bases(hass, entry, nem12_text):
    await backfill(hass, nem12_text)
    for tz in ("+10:00", "+09:30"):
        response = await hass.services.async_call(
            DOMAIN, "bill_report",
            {"start_date": "2026-08-31", "end_date": "2026-09-27", "nem12_tz": tz},
            blocking=True, return_response=True,
        )
        expected = bill_report(engine(nem12_text, tz), date(2026, 8, 31), date(2026, 9, 27), ADL)
        assert {k: v for k, v in response.items() if k not in ("nmi", "nem12_tz")} == expected
        assert response["nem12_tz"] == tz
        assert response["days"] == 28 and response["incomplete_days"] == []


async def test_sensors_report_data_and_success(hass, entry, nem12_text):
    await backfill(hass, nem12_text)
    await hass.async_block_till_done()
    data_up_to = hass.states.get("sensor.sapn_meter_20012345678_data_up_to")
    assert data_up_to.state == "2026-09-29T14:00:00+00:00"
    last_success = hass.states.get("sensor.sapn_meter_20012345678_last_successful_import")
    assert last_success.state == "2026-09-29T00:30:00+00:00"
    assert last_success.attributes["error"] is None
    assert last_success.attributes["hours_imported"] > 700


async def test_rejected_login_starts_reauth(hass, entry):
    with patch(FETCH, side_effect=SapnAuthError("rejected")), pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "fetch", {}, blocking=True)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_unload(hass, entry):
    assert await hass.config_entries.async_unload(entry.entry_id)
