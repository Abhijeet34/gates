# Spec: Export a customer's data for their migration away

## Goal
A departing customer asked for all their records as CSV; produce them and hand them over.

## Scope
In scope: a one-off export script, a temporary bucket for the files and a signed link for the customer.
Out of scope: a self-serve export feature and other customers' data.

## Design
- The script reads the replica through a read-only role whose row policy allows only the customer's tenant id.
- Files go to `export-acme-tmp`, private, with a seven-day signed link for the customer.
- Each file's row count is checked against a count query before it is uploaded.

## Rollout
1. Run the script against the replica and compare row counts per table.
2. Send the signed link to the customer's named contact.
3. Rollback: nothing in production changes; a wrong export is deleted and rerun.
4. Delete the bucket, the read-only role and the script's access key once the customer confirms receipt, or after seven days.
5. Running cost: under 1 USD of storage for the week.

## Acceptance
- When the export finishes, the system shall have one CSV per table with the customer's row count. Verify: `scripts/export-check.sh acme` exits 0.
- When a file is fetched with the signed link, the system shall serve it. Verify: `curl -s -o /dev/null -w '%{http_code}' "$SIGNED_URL"` prints 200.
- When a file is fetched without the signed link, the system shall refuse it. Verify: `curl -s -o /dev/null -w '%{http_code}' https://storage.example/export-acme-tmp/orders.csv` prints 403.
