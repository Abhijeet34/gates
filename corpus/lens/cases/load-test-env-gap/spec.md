# Spec: Load test checkout before the sale

## Goal
Confirm checkout holds 500 orders a minute before the November sale.

## Scope
In scope: a temporary environment cloned from staging, a k6 script and a written result.
Out of scope: performance fixes (each becomes its own task) and testing other services.

## Design
- Create `loadtest` from the staging Terraform module with three times the app instances, reachable only from the VPN, as staging is.
- Seed it from the anonymised staging snapshot, which holds no customer data.
- The k6 script reuses the checkout journey from the existing smoke test.
- Payments go to the provider's sandbox, which allows 1,000 requests a minute, twice the test's rate, so no real charge is made.

## Rollout
1. Apply the `loadtest` workspace and seed it.
2. Ramp to 500 orders a minute over 10 minutes and hold for 30, recording p95 latency and errors.
3. Rollback: the environment is separate from production, so stopping k6 ends any effect.
4. Running cost: about 40 USD for the day the environment exists.

## Acceptance
- When 500 orders a minute arrive for 30 minutes, the system shall keep p95 checkout latency under 800 ms. Verify: `k6 run load/checkout.js` reports `p(95)<800`.
- When the test ends, the system shall leave no load-test resource running. Verify: `scripts/loadtest-gone.sh` exits 0 once `terraform -chdir=infra/loadtest state list` is empty and the workspace exists.
