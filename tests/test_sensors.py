"""Published-day snapshots and separate billing components."""

from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.sapn.const import DOMAIN
from custom_components.sapn.sensor import SENSORS

from .test_cycles import PREFIX, _setup
from .test_import import FETCH, slice_nem12


async def _fetch(hass, text):
    with patch(FETCH, return_value=text):
        await hass.services.async_call(
            DOMAIN, "fetch", {"start_date": "2026-08-31"}, blocking=True
        )
    await hass.async_block_till_done()


def _state(hass, suffix):
    state = hass.states.get(PREFIX + suffix)
    assert state is not None, suffix
    return state


async def test_cycle_components_add_up_to_invoice(hass, freezer, nem12_text):
    await _setup(hass, freezer, {"cycle_start": "2026-08-31", "cycle_days": 28})
    await _fetch(hass, slice_nem12(nem12_text, last="20260928"))

    def value(suffix):
        return float(_state(hass, suffix).state)

    assert value("bill_this_cycle") == pytest.approx(
        value("import_cost_this_cycle") + value("supply_charge_this_cycle")
        - value("export_credit_this_cycle") - value("zerohero_credit_this_cycle")
    )
    bill = _state(hass, "bill_this_cycle")
    for suffix, description in (
        ("peak_import_this_cycle", "Peak Usage"),
        ("free_offpeak_import_this_cycle", "Offpeak Usage"),
        ("shoulder_import_this_cycle", "Shoulder Usage"),
        ("paid_export_this_cycle", "Solar/Generation Feed in (4pm-11pm)"),
        ("unpaid_export_this_cycle", "Solar/Generation Feed in (11pm-4pm)"),
        ("super_export_this_cycle", "Super Export top up"),
    ):
        expected = next(
            line["quantity"] for line in bill.attributes["lines"]
            if line["description"] == description
        )
        assert value(suffix) == expected, suffix
    assert value("grid_import_this_cycle") == bill.attributes["import_kwh"]
    assert value("grid_export_this_cycle") == bill.attributes["export_kwh"]
    assert value("export_credit_this_cycle") >= 0
    assert _state(hass, "peak_import_this_cycle").attributes["data_to"] == "2026-09-28"


async def test_latest_day_updates_and_restores_without_cycle(hass, freezer, nem12_text):
    entry = await _setup(hass, freezer, {})
    await _fetch(hass, slice_nem12(nem12_text, last="20260928"))
    assert _state(hass, "latest_complete_day").state == "2026-09-28"
    report = await hass.services.async_call(
        DOMAIN, "bill_report", {"start_date": "2026-09-28", "end_date": "2026-09-28"},
        blocking=True, return_response=True,
    )
    for suffix, expected in (
        ("grid_import_latest_day", report["metered"]["import_kwh"]),
        ("grid_export_latest_day", report["metered"]["export_kwh"]),
        ("cost_latest_day", report["total"]),
        ("zerohero_grid_draw_latest_day", report["zerohero"]["nights"][0]["import_kwh"]),
    ):
        state = _state(hass, suffix)
        assert float(state.state) == expected
        assert state.attributes["date"] == "2026-09-28"
        assert "state_class" not in state.attributes
    result = _state(hass, "zerohero_latest_day")
    assert result.state == ("earned" if report["zerohero"]["nights"][0]["earned"] else "missed")
    assert result.attributes["threshold_kwh"] == 0.09
    assert result.attributes["import_by_hour"] == report["zerohero"]["nights"][0]["import_by_hour"]
    assert _state(hass, "bill_this_cycle").state == "unknown"

    freezer.move_to("2026-09-30T00:30:00+00:00")
    await _fetch(hass, nem12_text)
    assert _state(hass, "latest_complete_day").state == "2026-09-29"
    before = _state(hass, "cost_latest_day").state
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, "latest_complete_day").state == "2026-09-29"
    assert _state(hass, "cost_latest_day").state == before


async def test_latest_day_rejects_future_or_missing_channel_intervals(hass, freezer, nem12_text):
    entry = await _setup(hass, freezer, {})
    await _fetch(hass, nem12_text)
    coordinator = entry.runtime_data
    now = datetime(2026, 9, 29, 0, 30, tzinfo=UTC)
    local_tz = ZoneInfo("Australia/Adelaide")
    # The fixture contains a full future day, which must not become the latest day.
    assert coordinator._compute_latest_day(now, local_tz)["start_date"] == "2026-09-28"
    coordinator._days["2026-09-28"]["exp"][0] = None
    assert coordinator._compute_latest_day(now, local_tz)["start_date"] == "2026-09-27"
    coordinator._days["2026-09-27"]["imp"].pop()
    assert coordinator._compute_latest_day(now, local_tz)["start_date"] == "2026-09-26"
    coordinator._days.clear()
    assert coordinator._compute_latest_day(now, local_tz) is None


async def test_latest_day_period_and_zerohero_follow_adelaide_dst(hass, freezer):
    entry = await _setup(hass, freezer, {})
    coordinator = entry.runtime_data
    imp = [0.0] * 48
    # 17:30 NEM time becomes 18:00 Adelaide daylight time.
    imp[35] = 0.05
    imp[37] = 0.02
    coordinator._days = {"2026-10-05": {"step": 30, "imp": imp, "exp": [0.0] * 48}}
    freezer.move_to("2026-10-06T00:30:00+00:00")
    await coordinator._async_update_cycles()
    coordinator.async_set_updated_data(coordinator.status)
    await hass.async_block_till_done()
    day = _state(hass, "latest_complete_day")
    assert day.attributes["period_start"] == "2026-10-05T00:30:00+10:30"
    assert day.attributes["period_end"] == "2026-10-06T00:30:00+10:30"
    assert _state(hass, "zerohero_latest_day").state == "earned"
    assert float(_state(hass, "zerohero_grid_draw_latest_day").state) == 0.07
    assert _state(hass, "zerohero_latest_day").attributes["import_by_hour"]["18"] == 0.05


async def test_new_sensors_unknown_until_a_complete_day_arrives(hass, freezer):
    entry = await _setup(hass, freezer, {})
    for description in SENSORS:
        assert description.value_fn(entry.runtime_data.status) is None, description.key
    # A report without complete evening evidence must never call the result earned.
    coordinator = entry.runtime_data
    coordinator._days = {"2026-09-28": {"step": 30, "imp": [0.0] * 48, "exp": [0.0] * 48}}
    await coordinator._async_update_cycles()
    report = coordinator.status.latest_day
    assert report is not None
    report["zerohero"]["nights"] = []
    description = next(item for item in SENSORS if item.key == "zerohero_latest_day")
    assert description.value_fn(coordinator.status) == "pending"
