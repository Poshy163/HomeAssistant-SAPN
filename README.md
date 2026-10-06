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

The change in `bill_total` over a billing period is the invoice total. ZeroHero follows GloBird's rule, judged on the retailer's meter: under 0.03 kWh an hour averaged across 6pm to 9pm, so under 0.09 kWh for the whole window. One busy hour does not lose the night on its own. `sapn.bill_report` lists every night's import and export by hour and half hour, so you can check any credit GloBird gives or withholds.

Sensors per meter:

| Sensor | Shows |
|---|---|
| Bill this cycle | The invoice so far for the current billing cycle, counting only days SAPN has fully published. Attributes carry every invoice line, the GST and the ZeroHero days. |
| Projected bill | Bill this cycle scaled to the full cycle length. |
| Last bill | The previous full cycle, with the same attributes. Compare it with GloBird's invoice. |
| ZeroHero days this cycle | Nights earned so far, with the earned and missed dates as attributes. |
| Grid import, Grid export this cycle | The current cycle's metered totals in kWh. |
| Peak import, Free offpeak import, Shoulder import this cycle | Import split into the three tariff bands. |
| Paid export, Unpaid export, Super export this cycle | Export in the paid and unpaid windows, and the quantity eligible for the super export top-up. Super export overlaps paid export. |
| Import cost, Export credit, Supply charge, ZeroHero credit this cycle | Individual invoice components in AUD. Credits are positive amounts to subtract from the charges. |
| Latest complete day | The newest finished NEM12 date with every import and export interval present. |
| Grid import, Grid export, Cost latest day | Metered totals and net cost for that date, including supply and credits. |
| ZeroHero latest day, ZeroHero grid draw latest day | Earned, missed or pending, and the grid draw in the local 6pm to 9pm window. Attributes include the date, threshold, and hourly and half-hourly evidence. |
| Data up to, Last successful import | Diagnostics. The second carries the last error, if any. |

The billing-cycle sensors stay unknown until you set **Billing cycle start** under **Configure**. The latest-day sensors work without a cycle setting and retain the last complete day while newer data is still arriving. Their date and period attributes show exactly which published day they cover, including daylight saving.

These sensors are snapshots of published meter data, rather than live power readings. They update after an import and when the integration loads its cache. Daily costs are rounded separately, so adding them can differ by a few cents from pricing the whole billing period at once. The existing 13 external statistics remain the source for historical energy charts and the Energy Dashboard; snapshots do not create a second set of accumulating statistics. See [Home Assistant's sensor guidance](https://developers.home-assistant.io/docs/core/entity/sensor/) for state-class semantics.

## Actions

| Action | Does |
|---|---|
| `sapn.fetch` | Downloads and imports from `start_date`, or re-imports the last few days. `download: false` rebuilds from stored data. |
| `sapn.import_file` | Imports a NEM12 CSV you downloaded yourself. Put it under `/media` (the Samba `media` share). |
| `sapn.bill_report` | Returns GloBird's invoice lines, GST and ZeroHero days for any period, rebuilt from stored data. |

## Schedule and settings

It runs two minutes after start-up and at 10:15 and 22:15, re-importing the last 7 days each time so SAPN's corrections replace estimated reads. **Configure** changes the times, the re-import window and the billing cycle. For the cycle, enter any bill's start date and the cycle length; GloBird bills every 28 days. It keeps 400 days of interval data in `.storage`, so reports and rebuilds need no download.

If SAPN rejects the stored password, Home Assistant raises a repair asking you to sign in again. Home Assistant keeps the password in the config entry, as it does for every integration that signs in to a cloud account, so give SAPN a password you use nowhere else.

Rates and windows live in `custom_components/sapn/const.py`. Edit them when GloBird changes the plan. Windows follow local clock time, so daylight saving needs no change unless GloBird moves the windows. Your bill labels the free window "Offpeak Usage - Step 1"; if a Step 2 line appears, GloBird has capped it and `const.py` needs the cap.

GloBird bills whole NEM12 dates, midnight to midnight in NEM time: 23:30 to 23:30 in Adelaide, or 00:30 to 00:30 during daylight saving. `sapn.bill_report` uses the same dates and works from the raw intervals. Home Assistant keeps hourly statistics on UTC hours, which line up with NEM12 dates, so the statistics can be summed over exactly the same period.

## Dashboard

`examples/electricity-bill-dashboard.yaml` is a ready-made dashboard: the bill so far laid out like a GloBird invoice, the last bill, live estimates for today, daily charts of cost, import by band, export by window and ZeroHero credits, and a check of SAPN's figures against your inverter's meter.

1. Replace `20012345678` with your NMI and `2001234567` with its first ten digits.
2. Settings → Dashboards → Add dashboard → New dashboard from scratch, and open it.
3. ⋮ → Edit dashboard → ⋮ → Raw configuration editor, paste the file, save.

The "Today" section reads the live tariff sensors from the author's own setup. Delete that section if you do not have them.

## Development

```bash
pip install pytest-homeassistant-custom-component
pytest
```

The portal client is adapted from [sapnmeterdata](https://github.com/bfulham/sapnmeterdata) by Brady Fulham (MIT), with its licence kept in `portal.py`. SAPN can change its portal without notice; if sign-in starts failing, check that project for fixes.
