# Decision request: quarantine the three flakiest tests

## Recommendation

Quarantine the three flakiest tests until their owners fix them.
They caused 62% of last month's red runs.
Each red run they cause costs a 14-minute rerun, so quarantining them saves about 294 minutes a month.
Each test stays visible in a nightly job, so a real regression in them is still seen within a day.

## Evidence

- Red runs last month: 50.
- Red runs caused by the three tests: 21.
- Rerun time of the full pipeline: 14 minutes.
- The nightly job runs every quarantined test and reports to the owners' channel.
