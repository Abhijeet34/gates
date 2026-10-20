# Spec: Change order ids from integers to UUIDs

## Goal
Sequential order ids leak our order volume; make public order ids UUIDs.

## Scope
In scope: a `public_id` UUID column on orders, URLs and API responses using it, and removing the integer id from responses.
Out of scope: changing the primary key and other tables' ids.

## Design
- Add `public_id` with a unique index; the integer `id` stays the primary key, so foreign keys are untouched.
- Every order URL and the orders API's responses use `public_id`; an old integer URL redirects for 90 days, only for a signed-in user whose account owns the order, else 404.
- Filling `public_id` rewrites every order row, and the integer ids then leave every response, which partners notice.

## Rollout
1. Add the column, nullable, and fill it for new orders.
2. Fill existing orders in batches of 5,000, logging progress and pausing when replication lag passes 10 s, then make `public_id` NOT NULL.
3. Switch responses to `public_id` after partners have had a 30-day notice.
4. Rollback: responses switch back by the `order_public_id` flag; the integer ids are never removed.
5. At day 90, remove the redirects and the `order_public_id` flag.

## Acceptance
- When the fill finishes, the system shall have a unique, non-null public id on every order. Verify: `scripts/public-id-check.sh` exits 0.
- When an owner opens an old integer order URL, the system shall redirect to its UUID URL, and answer 404 to anyone else. Verify: `pytest tests/orders/test_redirect.py`.
