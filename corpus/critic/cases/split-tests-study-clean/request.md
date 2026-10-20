# Study: splitting the test job in two

## Recommendation

Keep the test job split in two parallel halves.
Median pipeline time fell 28%, from 25 to 18 minutes.
Runner minutes rose 12%, from 2,500 to 2,800 a week, which the time saved is worth.
Failure rates were the same before and after, so the split hides nothing.

## Evidence

- Median pipeline time over 120 runs before: 25 minutes.
- Median pipeline time over 120 runs after: 18 minutes.
- Runner minutes a week: 2,500 before, 2,800 after.
- Failed runs: 6 of 120 before and 6 of 120 after.
