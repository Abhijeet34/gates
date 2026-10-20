# Spec: Serve search from the rebuilt product index

## Goal
Search misses products added in the last hour; serve queries from the new index that updates within a minute.

## Scope
In scope: building `products_v2` beside `products_v1` and moving reads to it by percentage.
Out of scope: ranking changes, the search UI and deleting `products_v1` (a later task, once v2 has run for two weeks).

## Design
- The indexer already writes v1 from the change stream; it writes v2 as well, and a failed v2 write retries from its stream offset, as v1 does.
- The search service sends the percentage of queries set by the `search_v2_percent` flag to v2 and the rest to v1.

## Rollout
1. Build v2 and compare result counts with v1 on the 1,000 most frequent queries; differences above 2% stop the rollout.
2. Set `search_v2_percent` to 10, then 100 after two days.
3. Alert when v2 indexing lag exceeds 60 s, or when its p95 latency or zero-result rate rises 20% over v1's.
4. The v1 deletion task removes the `search_v2_percent` flag with v1.
5. Running cost: the second index doubles search storage, about 40 GB, until v1 is deleted.

## Acceptance
- When a product is created, the system shall return it from `products_v2` within 60 s. Verify: `scripts/search-freshness.sh --index products_v2 --wait 60` exits 0.
- When all reads use v2, the system shall keep p95 latency under 200 ms. Verify: `scripts/search-latency.sh --index products_v2 --max-p95 200` exits 0.
