"""Billing-cycle sensors."""

from datetime import date
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sapn.const import CONF_NMI, DOMAIN
from custom_components.sapn.tariff import bill_report, cycle_bounds

from .test_import import ADL, FETCH, NMI, engine

PREFIX = "sensor.sapn_meter_20012345678_"


def test_cycle_bounds_reproduce_globird_periods():
    anchor = date(2026, 8, 3)
    assert cycle_bounds(anchor, 28, date(2026, 8, 30)) == (date(2026, 8, 3), date(2026, 8, 30))
    assert cycle_bounds(anchor, 28, date(2026, 8, 31)) == (date(2026, 8, 31), date(2026, 9, 27))
    assert cycle_bounds(anchor, 28, date(2026, 9, 29)) == (date(2026, 9, 28), date(2026, 10, 25))
    assert cycle_bounds(anchor, 28, date(2026, 7, 20)) == (date(2026, 7, 6), date(2026, 8, 2))


async def _setup(hass, freezer, options):
    await hass.config.async_set_time_zone("Australia/Adelaide")
    freezer.move_to("2026-09-29T00:30:00+00:00")  # 10:00 in Adelaide
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=NMI[:10], title=f"SAPN {NMI}", options=options,
        data={CONF_EMAIL: "me@example.com", CONF_PASSWORD: "pw", CONF_NMI: NMI},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_cycle_sensors_match_bill_report(hass, freezer, nem12_text):
    entry = await _setup(hass, freezer, {"cycle_start": "2026-08-31", "cycle_days": 28})
    with patch(FETCH, return_value=nem12_text):
        await hass.services.async_call(DOMAIN, "fetch", {"start_date": "2026-08-31"}, blocking=True)
    await hass.async_block_till_done()

    priced = engine(nem12_text)
    last = bill_report(priced, date(2026, 8, 31), date(2026, 9, 27), ADL)
    # 29 Sep is still arriving, so the cycle to date covers 28 Sep only.
    current = bill_report(priced, date(2026, 9, 28), date(2026, 9, 28), ADL)

    last_bill = hass.states.get(PREFIX + "last_bill")
    assert float(last_bill.state) == pytest.approx(last["total"])
    assert last_bill.attributes["complete"] is True
    assert last_bill.attributes["lines"] == last["lines"]
    assert last_bill.attributes["gst"] == last["gst"]
    assert last_bill.attributes["unit_of_measurement"] == "AUD"

    bill = hass.states.get(PREFIX + "bill_this_cycle")
    assert float(bill.state) == pytest.approx(current["total"])
    assert bill.attributes["cycle_start"] == "2026-09-28"
    assert bill.attributes["cycle_end"] == "2026-10-25"
    assert bill.attributes["data_to"] == "2026-09-28"
    assert bill.attributes["days"] == 1 and bill.attributes["days_in_cycle"] == 28

    projected = hass.states.get(PREFIX + "projected_bill")
    assert float(projected.state) == pytest.approx(round(current["total"] * 28, 2))

    zerohero = hass.states.get(PREFIX + "zerohero_days_this_cycle")
    assert int(zerohero.state) == len(current["zerohero"]["earned"])

    # Values come back from the cache after a reload, before any new download.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(PREFIX + "last_bill").state) == pytest.approx(last["total"])


async def test_cycle_sensors_unknown_without_anchor(hass, freezer, nem12_text):
    await _setup(hass, freezer, {})
    with patch(FETCH, return_value=nem12_text):
        await hass.services.async_call(DOMAIN, "fetch", {"start_date": "2026-08-31"}, blocking=True)
    await hass.async_block_till_done()
    for key in ("bill_this_cycle", "projected_bill", "last_bill", "zerohero_days_this_cycle"):
        assert hass.states.get(PREFIX + key).state == "unknown", key
