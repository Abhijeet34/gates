# Decision request: merge the three client repositories into one

## Recommendation

Merge the three client repositories into one.
Cross-repository changes were 36 of last quarter's 120 changes, 30%.
Each took a mean 2.5 extra hours to coordinate, 90 hours in all, which one repository removes.
The trial merge raised full CI time from 12 to 31 minutes, so the merge ships only with path-scoped builds, which ran a one-client change in 13 minutes in the same trial.

## Evidence

- Changes last quarter: 120, of which 36 touched more than one client repository.
- Mean coordination time of a cross-repository change: 2.5 extra hours.
- A trial merge raised the full CI time from 12 to 31 minutes, because every change built all three clients.
- With path-scoped builds, the same trial ran a one-client change in 13 minutes.
