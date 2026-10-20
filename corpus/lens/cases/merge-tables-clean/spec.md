# Spec: Merge the addresses table into customers

## Goal
Every customer has exactly one address in a separate table, so every query joins; move the fields onto `customers`.

## Scope
In scope: new address columns on `customers`, a copy migration, switching reads and dropping `addresses`.
Out of scope: supporting several addresses per customer and changing the address form.

## Design
- The codebase already has a `CustomerAddress` value object; the new columns map onto it, so callers keep their type.
- During the switch every write goes to both tables in one transaction, so a failure of either fails the request and they never drift.
- The final step drops `addresses`, which cannot be undone without a backup.

## Rollout
1. Add the columns and start writing both tables.
2. Dry run: run the copy and the drop on a production snapshot, compare row counts, and dump `addresses` before the real drop.
3. Copy existing rows in batches, then switch reads to `customers`; a nightly parity check alerts on any mismatch until the drop.
4. Two weeks after reads switched, stop writing `addresses`, then drop it.
5. Rollback: until the drop, switch reads back; after it, restore `addresses` from the dump.

## Acceptance
- When the copy finishes, the system shall have the same address on every customer as in addresses. Verify: `scripts/address-parity.sh` exits 0.
- When reads switch, the system shall render every checkout address from `customers`. Verify: `pytest tests/checkout/test_address.py::test_reads_customers`.
