"""Constants for the SA Power Networks meter data integration."""

from __future__ import annotations

from datetime import timedelta, timezone
from typing import Final

DOMAIN: Final = "sapn"

CONF_NMI: Final = "nmi"
CONF_DAYS_BACK: Final = "days_back"
CONF_RUN_TIMES: Final = "run_times"
CONF_CYCLE_START: Final = "cycle_start"
CONF_CYCLE_DAYS: Final = "cycle_days"

# AEMO's meter data file format puts NEM12 interval times on NEM time (AEST,
# UTC+10) all year, with no daylight saving, in every NEM state including SA.
# Local tariff windows come from Home Assistant's time zone instead.
NEM_TIME: Final = timezone(timedelta(hours=10), "AEST")

DEFAULT_DAYS_BACK: Final = 7
DEFAULT_RUN_TIMES: Final = "10:15,22:15"
STARTUP_DELAY_SECONDS: Final = 120
DEFAULT_CYCLE_DAYS: Final = 28

STORE_VERSION: Final = 1
STORE_RETENTION_DAYS: Final = 400

IMPORT_SUFFIX: Final = "E1"
EXPORT_SUFFIX: Final = "B1"

# GloBird ZEROHERO (SA). Rates include GST. Windows are local clock hours,
# start inclusive, end exclusive. Edit these when GloBird changes the plan.
SUPPLY_PER_DAY: Final = 2.035
IMPORT_BANDS: Final = {"offpeak": (11, 14, 0.000), "peak": (16, 23, 0.572)}
SHOULDER_RATE: Final = 0.495
FIT_WINDOW: Final = (16, 23)
FIT_RATE: Final = 0.02
SUPER_WINDOW: Final = (18, 21)
SUPER_TOPUP: Final = 0.08
SUPER_DAILY_CAP_KWH: Final = 15.0
ZEROHERO_WINDOW: Final = (18, 21)
# Averaged over the window: a night earns under 0.03 kWh/hour x 3 hours = 0.09 kWh.
ZEROHERO_MAX_KWH_PER_HOUR: Final = 0.03
ZEROHERO_CREDIT: Final = 1.00
GST_DIVISOR: Final = 1.1

CURRENCY: Final = "AUD"
ENERGY_STATS: Final = {
    "grid_import": "grid import",
    "grid_export": "grid export",
    "import_peak": "import peak",
    "import_offpeak": "import offpeak",
    "import_shoulder": "import shoulder",
    "export_4pm_11pm": "feed-in 4pm-11pm",
    "export_11pm_4pm": "feed-in 11pm-4pm",
    "export_super": "super export top-up",
}
MONEY_STATS: Final = {
    "import_cost": "import cost",
    "export_credit": "export credit",
    "supply_charge": "supply charge",
    "zerohero_credit": "ZeroHero credit",
    "bill_total": "bill total",
}
ALL_STATS: Final = (*ENERGY_STATS, *MONEY_STATS)
