"""Price interval data against the GloBird ZEROHERO plan.

Pure Python with no Home Assistant imports, so it can be tested on its own.
All interval timestamps passed in are timezone-aware UTC datetimes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any

from .const import (
    ALL_STATS,
    FIT_RATE,
    FIT_WINDOW,
    GST_DIVISOR,
    IMPORT_BANDS,
    NEM_TIME,
    SHOULDER_RATE,
    SUPER_DAILY_CAP_KWH,
    SUPER_TOPUP,
    SUPER_WINDOW,
    SUPPLY_PER_DAY,
    ZEROHERO_CREDIT,
    ZEROHERO_MAX_KWH_PER_HOUR,
    ZEROHERO_WINDOW,
)

HOUR = timedelta(hours=1)


@dataclass(slots=True)
class PricedInterval:
    """One metering interval with its local classification and values."""

    utc: datetime
    day: date
    hour: int
    values: dict[str, float]


@dataclass(slots=True)
class ZeroHeroDay:
    """GloBird's ZeroHero test for one local day, with the evidence behind it."""

    earned: bool
    complete: bool
    hours: dict[int, float]  # import kWh per local clock hour of the window
    worst_hour: int | None
    total: float = 0.0  # import kWh across the whole window
    exports: dict[int, float] = field(default_factory=dict)
    export_total: float = 0.0
    halves: dict[str, tuple[float, float]] = field(default_factory=dict)  # "18:30" -> (import, export)


@dataclass
class PricedData:
    """All intervals for a period, priced, plus the ZeroHero verdicts."""

    step: timedelta
    intervals: list[PricedInterval] = field(default_factory=list)
    zerohero: dict[date, ZeroHeroDay] = field(default_factory=dict)


def floor_hour(moment: datetime) -> datetime:
    """Floor an aware datetime to the start of its UTC hour."""
    moment = moment.astimezone(UTC)
    return moment.replace(minute=0, second=0, microsecond=0)


def ceil_hour(moment: datetime) -> datetime:
    """Ceil an aware datetime to the next UTC hour boundary (or itself)."""
    floored = floor_hour(moment)
    return floored if floored == moment.astimezone(UTC) else floored + HOUR


def local_midnight(day: date, local_tz: tzinfo) -> datetime:
    """Return local midnight for a date as an aware datetime."""
    return datetime.combine(day, time(), local_tz)


def price_intervals(
    intervals: list[tuple[datetime, float, float]], local_tz: tzinfo
) -> PricedData:
    """Classify and price (utc_start, import_kwh, export_kwh) intervals."""
    ordered = sorted({start: (imp, exp) for start, imp, exp in intervals}.items())
    diffs = Counter(b[0] - a[0] for a, b in zip(ordered, ordered[1:], strict=False))
    step = diffs.most_common(1)[0][0] if diffs else timedelta(minutes=30)
    data = PricedData(step=step)

    super_used: dict[date, float] = defaultdict(float)
    zh_sums: dict[date, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    zh_exports: dict[date, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    zh_counts: dict[date, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    zh_halves: dict[date, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))

    for start, (imp, exp) in ordered:
        local = start.astimezone(local_tz)
        day, hour = local.date(), local.hour
        band, rate = "shoulder", SHOULDER_RATE
        for name, (first, last, band_rate) in IMPORT_BANDS.items():
            if first <= hour < last:
                band, rate = name, band_rate
        in_fit = FIT_WINDOW[0] <= hour < FIT_WINDOW[1]
        super_kwh = 0.0
        if SUPER_WINDOW[0] <= hour < SUPER_WINDOW[1]:
            remaining = max(SUPER_DAILY_CAP_KWH - super_used[day], 0.0)
            super_kwh = min(exp, remaining)
            super_used[day] += exp
        fit_kwh = exp if in_fit else 0.0
        values = {
            "grid_import": imp,
            "grid_export": exp,
            "import_peak": imp if band == "peak" else 0.0,
            "import_offpeak": imp if band == "offpeak" else 0.0,
            "import_shoulder": imp if band == "shoulder" else 0.0,
            "export_4pm_11pm": fit_kwh,
            "export_11pm_4pm": 0.0 if in_fit else exp,
            "export_super": super_kwh,
            "import_cost": imp * rate,
            "export_credit": fit_kwh * FIT_RATE + super_kwh * SUPER_TOPUP,
        }
        data.intervals.append(PricedInterval(start, day, hour, values))
        if ZEROHERO_WINDOW[0] <= hour < ZEROHERO_WINDOW[1]:
            zh_sums[day][hour] += imp
            zh_exports[day][hour] += exp
            zh_counts[day][hour] += 1
            half = zh_halves[day][f"{hour:02d}:{local.minute // 30 * 30:02d}"]
            half[0] += imp
            half[1] += exp

    # GloBird's threshold is 0.03 kWh/hour averaged over the window, so a night
    # earns the credit when the whole 6-9pm window draws under 0.09 kWh. One
    # busy hour does not cost the night: GloBird credited 18 Sep 2026 with
    # 0.045 kWh in its worst hour and missed 14 Sep with 0.041.
    needed = max(int(round(HOUR / step)), 1)
    window = range(ZEROHERO_WINDOW[0], ZEROHERO_WINDOW[1])
    limit = round(ZEROHERO_MAX_KWH_PER_HOUR * len(window), 4)
    for day, sums in zh_sums.items():
        hours = {h: round(v, 4) for h, v in sums.items()}
        exports = {h: round(v, 4) for h, v in zh_exports[day].items()}
        total = round(sum(sums.values()), 4)
        complete = all(zh_counts[day].get(h, 0) >= needed for h in window)
        earned = complete and total < limit
        worst = max(hours, key=lambda h: hours[h]) if hours else None
        halves = {
            slot: (round(pair[0], 4), round(pair[1], 4))
            for slot, pair in sorted(zh_halves[day].items())
        }
        data.zerohero[day] = ZeroHeroDay(
            earned, complete, hours, worst, total, exports, round(sum(exports.values()), 4), halves
        )
    return data


def hourly_increments(data: PricedData, local_tz: tzinfo) -> dict[datetime, dict[str, float]]:
    """Sum every statistic per UTC hour, including daily supply and ZeroHero.

    Home Assistant keeps hourly statistics on UTC hours. Supply is booked in the
    first UTC hour that starts on or after local midnight, and a ZeroHero credit
    in the hour holding the end of the window, so both land on the right local day.
    """
    if not data.intervals:
        return {}
    first = floor_hour(data.intervals[0].utc)
    last = floor_hour(data.intervals[-1].utc)
    hours: dict[datetime, dict[str, float]] = {}
    cursor = first
    while cursor <= last:
        hours[cursor] = dict.fromkeys(ALL_STATS, 0.0)
        cursor += HOUR
    for interval in data.intervals:
        bucket = hours[floor_hour(interval.utc)]
        for key, value in interval.values.items():
            bucket[key] += value
    for day in sorted({i.day for i in data.intervals}):
        slot = ceil_hour(local_midnight(day, local_tz))
        if slot in hours:
            hours[slot]["supply_charge"] += SUPPLY_PER_DAY
    for day, verdict in data.zerohero.items():
        if verdict.earned:
            close = datetime.combine(day, time(ZEROHERO_WINDOW[1] - 1, 59), local_tz)
            slot = floor_hour(close)
            if slot in hours:
                hours[slot]["zerohero_credit"] += ZEROHERO_CREDIT
    for bucket in hours.values():
        bucket["bill_total"] = (
            bucket["import_cost"]
            + bucket["supply_charge"]
            - bucket["export_credit"]
            - bucket["zerohero_credit"]
        )
    return hours


def cents(value: float) -> float:
    """Round to the cent. Adding 0.0 turns -0.0 (a credit that rounds away) into 0.0."""
    return round(value, 2) + 0.0


def nem_date(moment: datetime) -> date:
    """The NEM12 date an interval starting at `moment` is filed under."""
    return moment.astimezone(NEM_TIME).date()


def cycle_bounds(anchor: date, length: int, day: date) -> tuple[date, date]:
    """First and last day of the billing cycle containing `day`.

    `anchor` is any known cycle start; cycles repeat every `length` days either side.
    """
    start = day - timedelta(days=(day - anchor).days % length)
    return start, start + timedelta(days=length - 1)


def bill_report(data: PricedData, first: date, last: date) -> dict[str, Any]:
    """Rebuild GloBird's invoice lines, GST and ZeroHero days for a period.

    GloBird bills whole NEM12 dates, midnight to midnight in NEM time (23:30 to
    23:30 in Adelaide outside daylight saving), and prices each interval by local
    clock time. ZeroHero days are local dates.
    """
    days = (last - first).days + 1
    part = [i for i in data.intervals if first <= nem_date(i.utc) <= last]

    def total(key: str) -> float:
        return round(sum(i.values[key] for i in part), 2)

    qty = {
        "peak": total("import_peak"),
        "offpeak": total("import_offpeak"),
        "shoulder": total("import_shoulder"),
        "super": total("export_super"),
        "fit": total("export_4pm_11pm"),
        "unpaid": total("export_11pm_4pm"),
    }
    verdicts = {d: v for d, v in data.zerohero.items() if first <= d <= last}
    earned = sorted(d for d, v in verdicts.items() if v.earned)
    peak_rate = IMPORT_BANDS["peak"][2]
    offpeak_rate = IMPORT_BANDS["offpeak"][2]
    lines = [
        ("Daily Charge", days, "Days", SUPPLY_PER_DAY),
        ("Peak Usage", qty["peak"], "kWh", peak_rate),
        ("Offpeak Usage", qty["offpeak"], "kWh", offpeak_rate),
        ("Shoulder Usage", qty["shoulder"], "kWh", SHOULDER_RATE),
        ("Super Export top up", qty["super"], "kWh", -SUPER_TOPUP),
        ("Solar/Generation Feed in (4pm-11pm)", qty["fit"], "kWh", -FIT_RATE),
        ("Solar/Generation Feed in (11pm-4pm)", qty["unpaid"], "kWh", 0.0),
        ("ZeroHero", len(earned), "Days", -ZEROHERO_CREDIT),
    ]
    priced_lines = [
        {"description": d, "quantity": q, "unit": u, "rate": r, "total": cents(q * r)}
        for d, q, u, r in lines
    ]
    grand_total = cents(sum(line["total"] for line in priced_lines))
    # GloBird's GST: GST-inclusive line totals minus ex-GST line totals, each rounded.
    charged = [(days, SUPPLY_PER_DAY), (qty["peak"], peak_rate), (qty["shoulder"], SHOULDER_RATE)]
    gst = cents(
        sum(round(q * r, 2) for q, r in charged)
        - sum(round(q * r / GST_DIVISOR, 2) for q, r in charged)
    )
    counts = Counter(nem_date(i.utc) for i in part)
    per_day = int(round(timedelta(days=1) / data.step))  # NEM time has no daylight saving
    incomplete = [
        (first + timedelta(days=n)).isoformat()
        for n in range(days)
        if counts.get(first + timedelta(days=n), 0) < per_day
    ]
    missed = [
        {
            "date": d.isoformat(),
            "kwh": v.total,
            "hour": v.worst_hour,
            "hour_kwh": v.hours.get(v.worst_hour, 0.0),
        }
        for d, v in sorted(verdicts.items())
        if v.complete and not v.earned
    ]
    nights = [
        {
            "date": d.isoformat(),
            "earned": v.earned,
            "complete": v.complete,
            "import_kwh": v.total,
            "export_kwh": v.export_total,
            "import_by_hour": {str(h): kwh for h, kwh in sorted(v.hours.items())},
            "export_by_hour": {str(h): kwh for h, kwh in sorted(v.exports.items())},
            "import_by_half_hour": {slot: pair[0] for slot, pair in v.halves.items()},
            "export_by_half_hour": {slot: pair[1] for slot, pair in v.halves.items()},
        }
        for d, v in sorted(verdicts.items())
    ]
    return {
        "start_date": first.isoformat(),
        "end_date": last.isoformat(),
        "days": days,
        "interval_minutes": int(data.step.total_seconds() // 60),
        "lines": priced_lines,
        "total": grand_total,
        "gst": gst,
        "metered": {"import_kwh": total("grid_import"), "export_kwh": total("grid_export")},
        "zerohero": {
            "earned": [d.isoformat() for d in earned],
            "missed": missed,
            "pending": [d.isoformat() for d, v in sorted(verdicts.items()) if not v.complete],
            "nights": nights,
        },
        "incomplete_days": incomplete,
    }
