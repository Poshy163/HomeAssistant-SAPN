"""Minimal NEM12 (AEMO meter data file format) interval parser.

Only the records needed for energy interval data are read: 100 (header),
200 (channel), 300 (interval values) and 900 (end). Reactive channels such as
kVArh are skipped. A later 300 record for the same date replaces an earlier one.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

UOM_TO_KWH = {"KWH": 1.0, "WH": 0.001, "MWH": 1000.0}


class Nem12Error(ValueError):
    """Raised when text is not usable NEM12 interval data."""


@dataclass
class Channel:
    """One NMI suffix (for example E1 or B1) from a NEM12 file."""

    nmi: str
    suffix: str
    interval_minutes: int
    values: dict[datetime, float] = field(default_factory=dict)


def parse_nem12(text: str) -> list[Channel]:
    """Parse NEM12 text into energy channels keyed by naive interval start."""
    channels: dict[tuple[str, str, int], Channel] = {}
    current: tuple[Channel, float] | None = None
    saw_header = False
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip():
            continue
        record = row[0].strip()
        if record == "100":
            saw_header = True
            if len(row) > 1 and row[1].strip().upper() != "NEM12":
                raise Nem12Error(f"Not NEM12 data (header says {row[1].strip()})")
        elif record == "200":
            if len(row) < 9:
                raise Nem12Error("Malformed 200 record")
            factor = UOM_TO_KWH.get(row[7].strip().upper())
            try:
                interval = int(row[8])
            except ValueError as err:
                raise Nem12Error(f"Bad interval length {row[8]!r}") from err
            if factor is None or interval <= 0 or 1440 % interval:
                current = None
                continue
            key = (row[1].strip(), row[4].strip().upper(), interval)
            channel = channels.setdefault(key, Channel(key[0], key[1], interval))
            current = (channel, factor)
        elif record == "300" and current is not None:
            channel, factor = current
            count = 1440 // channel.interval_minutes
            try:
                day = datetime.strptime(row[1].strip(), "%Y%m%d")
            except (IndexError, ValueError) as err:
                raise Nem12Error(f"Bad 300 record date in {row[:2]}") from err
            values = row[2 : 2 + count]
            if len(values) < count:
                raise Nem12Error(f"300 record for {row[1]} has {len(values)} of {count} values")
            step = timedelta(minutes=channel.interval_minutes)
            for index, raw in enumerate(values):
                raw = raw.strip()
                if raw:
                    channel.values[day + index * step] = float(raw) * factor
        elif record == "900":
            break
    if not saw_header:
        raise Nem12Error("No NEM12 100 header record")
    return list(channels.values())


def nmis_match(wanted: str, found: str) -> bool:
    """Match an NMI with or without its trailing checksum digit."""
    wanted, found = wanted.strip(), found.strip()
    if not wanted or wanted == found:
        return True
    shorter, longer = sorted((wanted, found), key=len)
    return len(longer) == len(shorter) + 1 and longer.startswith(shorter)


def nem12_days(
    channels: list[Channel], nmi: str, import_suffix: str, export_suffix: str
) -> dict[date, dict]:
    """Group one meter's import and export values by NEM12 date.

    Returns {date: {"step": minutes, "imp": [...], "exp": [...]}} with None for
    intervals the file did not supply.
    """
    selected = [c for c in channels if nmis_match(nmi, c.nmi)]
    imp = [c for c in selected if c.suffix == import_suffix.upper()]
    exp = [c for c in selected if c.suffix == export_suffix.upper()]
    if not imp and not exp:
        found = sorted({f"{c.nmi}/{c.suffix}" for c in channels})
        raise Nem12Error(
            f"No {import_suffix}/{export_suffix} data for NMI {nmi}. File has {found}."
        )
    days: dict[date, dict] = {}
    for kind, group in (("imp", imp), ("exp", exp)):
        for channel in group:
            step = channel.interval_minutes
            count = 1440 // step
            for start, value in channel.values.items():
                entry = days.setdefault(
                    start.date(), {"step": step, "imp": [None] * count, "exp": [None] * count}
                )
                if entry["step"] != step:
                    raise Nem12Error(f"Mixed interval lengths on {start.date()}")
                slot = (start.hour * 60 + start.minute) // step
                entry[kind][slot] = value
    return days
