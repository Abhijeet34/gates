# Study: the dependency cache after the key change

## Recommendation

Keep the new cache key, which hashes the lockfile alone.
The cache hit rate rose from 41% to 78% across the two samples.
Median job time fell by 3.5 minutes, from 11.0 to 7.5 minutes.
No run restored a stale cache in the after sample, so the key is safe to keep.

## Evidence

- Before the change: 82 cache hits in 200 runs; median job time 11.0 minutes.
- After the change: 156 cache hits in 200 runs; median job time 7.5 minutes.
- Stale restores, checked by comparing the restored lockfile hash: 0 in the 200 runs after.
