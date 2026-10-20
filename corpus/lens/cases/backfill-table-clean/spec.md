# Spec: Backfill tax amounts on historical invoices

## Goal
Invoices before March lack the `tax_amount` column value; compute and fill it so finance reports reconcile.

## Scope
In scope: a one-off backfill job and a staging table it fills before copying into `invoices`.
Out of scope: changing how new invoices compute tax, and invoices in other currencies than EUR.

## Design
- Reuse `tax.compute(invoice)` from the billing module, the function new invoices already use.
- The job writes results to `invoice_backfill_staging`, so they can be checked before they touch `invoices`.
- Rows copy from staging in batches of 1,000, each in its own transaction, filling only rows whose `tax_amount` is null and marking each staging row it filled.

## Rollout
1. Run the job against a production snapshot and compare 100 sampled rows with finance's figures.
2. Run it once by hand in production during the Sunday low-traffic window; it pauses itself when replication lag passes 10 s.
3. Rollback: set `tax_amount` back to null for the staging rows marked as filled, which are exactly the rows the copy changed.
4. Drop `invoice_backfill_staging` and delete the backfill job once finance signs off the reconciliation.
5. Running cost: none after cleanup; the staging table holds about 2 GB while it exists.

## Acceptance
- When the backfill finishes, the system shall have no EUR invoice before March with a null tax amount. Verify: `scripts/check-null-tax.sh` exits 0.
- When the backfill finishes, the system shall hold the value `tax.compute` gives for each filled row. Verify: `pytest tests/backfill/test_values_match.py`.
- When a row already has a tax amount, the system shall leave it unchanged. Verify: `pytest tests/backfill/test_skips_filled.py`.
