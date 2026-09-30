"""GloBird's ZeroHero rule: under 0.09 kWh across the 6-9pm window."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.sapn.tariff import bill_report, price_intervals

ADL = ZoneInfo("Australia/Adelaide")
STEP = timedelta(minutes=5)


def night(day: date, per_hour: dict[int, float], export: float = 0.0, gap_at: int | None = None):
    """A full local day of 5-minute intervals with the given 6-9pm import per hour."""
    out = []
    start = datetime.combine(day, datetime.min.time(), ADL).astimezone(UTC)
    for n in range(288):
        moment = start + n * STEP
        local = moment.astimezone(ADL)
        if gap_at is not None and local.hour == gap_at and local.minute == 30:
            continue
        imp = per_hour.get(local.hour, 0.0) / 12 if 18 <= local.hour < 21 else 0.01
        exp = export / 36 if 18 <= local.hour < 21 else 0.0
        out.append((moment, imp, exp))
    return out


def verdict(per_hour, **kwargs):
    day = date(2026, 9, 18)
    return price_intervals(night(day, per_hour, **kwargs), ADL).zerohero[day]


def test_one_busy_hour_still_earns_when_window_is_under_limit():
    # GloBird credited 18 Sep 2026: 0.045 kWh in the worst hour, under 0.09 overall.
    result = verdict({18: 0.045, 19: 0.02, 20: 0.02})
    assert result.earned and result.total == 0.085 and result.worst_hour == 18


def test_window_over_limit_misses_even_with_no_busy_hour():
    # GloBird missed 14 Sep 2026: 0.041 worst hour but the window crossed 0.09.
    result = verdict({18: 0.03, 19: 0.041, 20: 0.025})
    assert not result.earned and result.total == 0.096


def test_limit_is_strict():
    assert not verdict({18: 0.03, 19: 0.03, 20: 0.03}).earned
    assert verdict({18: 0.03, 19: 0.03, 20: 0.029}).earned


def test_gap_in_window_is_pending_not_earned():
    result = verdict({18: 0.0, 19: 0.0, 20: 0.0}, gap_at=19)
    assert not result.complete and not result.earned


def test_bill_report_lists_every_night_with_hourly_import_and_export():
    day = date(2026, 9, 18)
    data = price_intervals(night(day, {18: 0.045, 19: 0.02, 20: 0.02}, export=0.3), ADL)
    report = bill_report(data, day, day)
    nights = report["zerohero"]["nights"]
    assert [n["date"] for n in nights] == ["2026-09-18"]
    assert nights[0]["earned"] is True
    assert nights[0]["import_kwh"] == 0.085 and nights[0]["export_kwh"] == 0.3
    assert nights[0]["import_by_hour"] == {"18": 0.045, "19": 0.02, "20": 0.02}
    assert set(nights[0]["export_by_hour"]) == {"18", "19", "20"}
    halves = nights[0]["import_by_half_hour"]
    assert list(halves) == ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30"]
    assert round(halves["18:00"] + halves["18:30"], 4) == 0.045
    assert round(sum(nights[0]["export_by_half_hour"].values()), 4) == 0.3
    assert report["zerohero"]["earned"] == ["2026-09-18"]
