# SA Power Networks Meter Data for Home Assistant

Imports your smart meter's interval data from SA Power Networks' [Your Meter Data](https://www.sapowernetworks.com.au/your-power/manage-your-power-use/your-meter-data/) portal into Home Assistant long-term statistics, priced against the GloBird ZEROHERO plan. It reads the E1 and B1 channels, the same registers GloBird bills from, so a billing period in Home Assistant adds up to the invoice.

No extra Python packages: the portal client and NEM12 parser use the standard library plus `requests`, which Home Assistant already ships.

## Install

**HACS:** HACS → ⋮ → Custom repositories → add this repository as an Integration → download **SA Power Networks Meter Data** → restart Home Assistant.

**By hand:** copy `custom_components/sapn` into `/config/custom_components/` (the Samba `config` share works) and restart.

Then Settings → Devices & services → Add integration → **SA Power Networks Meter Data**, and sign in with your SAPN portal login. With one meter on the account it picks the NMI for you.

## First run

Backfill, then check the result against a bill you already have. In Developer tools → Actions, switch to YAML mode:

```yaml
action: sapn.fetch
data:
  start_date: "2026-08-03"
```

Time zones need no setting. AEMO's file format puts NEM12 timestamps on NEM time (UTC+10) all year, with no daylight saving, in every state. The integration converts them to Home Assistant's own time zone, so tariff windows follow Adelaide clock time, including after the switch to daylight saving.

To check the result against a GloBird bill, set the dates to the bill's period:

```yaml
action: sapn.bill_report
data:
  start_date: "2026-08-31"
  end_date: "2026-09-27"
```

## What it creates

Thirteen statistics per meter, named `sapn:<nmi>_<key>`. Find them in Developer tools → Statistics or chart them with a Statistics Graph card.

| Energy (kWh) | Money (AUD) |
|---|---|
| `grid_import`, `grid_export` | `import_cost` |
| `import_peak`, `import_offpeak`, `import_shoulder` | `export_credit` |
| `export_4pm_11pm`, `export_11pm_4pm` | `supply_charge` |
| `export_super` (first 15 kWh a day in 6pm to 9pm) | `zerohero_credit` |
| | `bill_total` |

The change in `bill_total` over a billing period is the invoice total. ZeroHero follows GloBird's rule, judged on the retailer's meter: under 0.03 kWh drawn in each hour from 6pm to 9pm.

Two diagnostic sensors show **Data up to** and **Last successful import**. The second carries the last error, if any, as an attribute.

## Actions

| Action | Does |
|---|---|
| `sapn.fetch` | Downloads and imports from `start_date`, or re-imports the last few days. `download: false` rebuilds from stored data. |
| `sapn.import_file` | Imports a NEM12 CSV you downloaded yourself. Put it under `/media` (the Samba `media` share). |
| `sapn.bill_report` | Returns GloBird's invoice lines, GST and ZeroHero days for any period, rebuilt from stored data. |

## Schedule and settings

It runs two minutes after start-up and at 10:15 and 22:15, re-importing the last 7 days each time so SAPN's corrections replace estimated reads. **Configure** changes the times and the re-import window. It keeps 400 days of interval data in `.storage`, so reports and rebuilds need no download.

If SAPN rejects the stored password, Home Assistant raises a repair asking you to sign in again. Home Assistant keeps the password in the config entry, as it does for every integration that signs in to a cloud account, so give SAPN a password you use nowhere else.

Rates and windows live in `custom_components/sapn/const.py`. Edit them when GloBird changes the plan. Windows follow local clock time, so daylight saving needs no change unless GloBird moves the windows. Your bill labels the free window "Offpeak Usage - Step 1"; if a Step 2 line appears, GloBird has capped it and `const.py` needs the cap.

GloBird bills whole NEM12 dates, midnight to midnight in NEM time: 23:30 to 23:30 in Adelaide, or 00:30 to 00:30 during daylight saving. `sapn.bill_report` uses the same dates and works from the raw intervals. Home Assistant keeps hourly statistics on UTC hours, which line up with NEM12 dates, so the statistics can be summed over exactly the same period.

## Development

```bash
pip install pytest-homeassistant-custom-component
pytest
```

The portal client is adapted from [sapnmeterdata](https://github.com/bfulham/sapnmeterdata) by Brady Fulham (MIT), with its licence kept in `portal.py`. SAPN can change its portal without notice; if sign-in starts failing, check that project for fixes.
