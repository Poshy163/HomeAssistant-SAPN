# Changelog

## 0.2.1

- A credit that rounds to nothing totals $0.00, not -$0.00, so cards stop showing "$-0.00".

## 0.2.0

- Billing cycle options: cycle start date and length.
- New sensors: Bill this cycle, Projected bill, Last bill, ZeroHero days this cycle. Invoice lines ride along as attributes and stay out of the recorder's history.
- Example dashboard in `examples/`.

## 0.1.3

- `bill_report` bills whole NEM12 dates, midnight to midnight in NEM time, as GloBird does.

## 0.1.2

- NEM12 timestamps are read as NEM time (UTC+10), as AEMO's file format requires. The timestamp basis option is gone.

## 0.1.1

- Reads NEM12 files that have no 100 header record or start with a byte-order mark, as SAPN's portal sends them.
- Brand icon.

## 0.1.0

- First release: SAPN login, NEM12 cache, hourly statistics, `fetch`, `import_file` and `bill_report` actions.
