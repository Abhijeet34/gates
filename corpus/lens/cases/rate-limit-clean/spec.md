# Spec: Raise the public API rate limit for paid plans

## Goal
Paid-plan customers hit the 100 requests per minute limit during nightly syncs; raise it to 300 for paid plans only.

## Scope
In scope: the per-plan limit value in `config/limits.yaml` and the limiter reading it per plan.
Out of scope: free-plan limits, burst allowances and any new limiter code.

## Design
- The limiter already reads `limits.<plan>.per_minute`; add `paid: 300` beside `free: 100`, so no code changes.
- Requests are still counted in the existing Redis bucket per API key.

## Rollout
1. Ship the config to staging and replay one paid customer's nightly sync against it.
2. Ship to production for 10% of paid keys by the existing `limits_rollout` percentage, then 100% after a day.
3. Alert when Redis CPU passes 70% or paid-plan rejections rise above 1% of requests, from the existing `ratelimit.rejected` metric.
4. Rollback: set `limits_rollout` to 0, which restores 100 per minute within one config reload (about 30 s).
5. Remove the `limits_rollout` setting a week after it reaches 100%, leaving `paid: 300` as the only value.
6. No new running cost: the same Redis instance holds the buckets.

## Acceptance
- When a paid key sends 301 requests in a minute, the system shall accept 300 and reject the 301st. Verify: `scripts/burst.sh --plan paid --count 301` prints `accepted=300 rejected=1`.
- When a free key sends 150 requests in a minute, the system shall reject 50. Verify: `scripts/burst.sh --plan free --count 150` prints `rejected=50`.
- When `limits_rollout` is 0, the system shall limit a paid key to 100 a minute. Verify: `scripts/burst.sh --plan paid --count 150 --rollout 0` prints `rejected=50`.
