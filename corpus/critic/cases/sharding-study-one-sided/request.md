# Study: the new test sharding

## Recommendation

Call the test sharding a win and keep it.
Mean test time fell from 20.0 to 12.0 minutes, 40% faster.
That saves 8 minutes a run, 720 minutes over the 90 runs measured after the change.

## Evidence

- Runs measured: 90 successful runs before and 90 after.
- Mean test time: 20.0 minutes before, 12.0 minutes after.
- Runs that failed or were cancelled were not measured: 4 before the change and 19 after.
