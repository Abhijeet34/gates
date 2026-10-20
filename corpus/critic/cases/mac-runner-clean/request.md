# Decision request: buy a self-hosted Mac runner

## Recommendation

Buy one self-hosted Mac mini for the macOS jobs.
macOS jobs used 80% of our CI minutes in the last 30 days.
Those minutes cost 147.20 USD at the hosted rate, against 600 USD once for the Mac and about 6 USD a month in power.
So the Mac pays for itself in 4.25 months, and the hosted bill falls by the same amount every month after.

## Evidence

- CI minutes in the last 30 days: 2,300 in total, 1,840 of them on macOS runners.
- Hosted macOS rate: 0.08 USD a minute.
- Mac mini price: 600 USD; its measured power draw costs about 6 USD a month.
- The macOS jobs need no hardware the Mac mini lacks; a trial run of all of them passed on it.
