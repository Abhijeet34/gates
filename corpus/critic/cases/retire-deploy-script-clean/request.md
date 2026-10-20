# Decision request: retire the old deploy script this week

## Recommendation

Retire the old deploy script this week and delete it from the repository.
The new pipeline deployed 48 of the last 50 releases, 96%, with no failed deploy.
It takes 6 minutes a deploy against the script's 22, which is 73% faster.
Billing and search still deploy with the script, so both move to the new pipeline on Monday and the script is deleted only once their next deploy has gone through it.

## Evidence

- Releases in the last quarter: 50, of which the new pipeline deployed 48.
- Failed deploys through the new pipeline: 0.
- Mean deploy time: 22 minutes with the script, 6 minutes with the pipeline.
- Two services still deploy only with the script: billing and search.
