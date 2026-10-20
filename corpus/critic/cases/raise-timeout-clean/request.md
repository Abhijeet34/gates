# Decision request: raise the CI job timeout to 45 minutes

## Recommendation

Raise the CI job timeout from 30 to 45 minutes.
Timeouts cancelled 24 of 400 runs last month, 6%.
The 95th percentile job time is 28 minutes, within 2 minutes of the limit.
Most of the timeouts, 18 of 24, are the integration job's larger fixture download; shrinking it is filed as its own task, and the raise covers the other jobs meanwhile.

## Evidence

- Runs last month: 400; runs cancelled by the timeout: 24.
- 95th percentile job time: 28 minutes.
- 18 of the 24 timeouts were in one job, integration, after its fixture download grew from 1 GB to 4 GB.
