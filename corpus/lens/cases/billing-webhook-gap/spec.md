# Spec: Receive billing provider webhooks

## Goal
Plans change late because we poll the billing provider hourly; receive its `subscription.updated` webhook instead.

## Scope
In scope: `POST /webhooks/billing`, parsing the event and updating the account's plan.
Out of scope: other event types and removing the hourly poll (kept as a fallback).

## Design
- A delivery whose `Billing-Timestamp` is more than five minutes old is refused; the provider sets it afresh on every redelivery.
- The account is found by the provider's customer id; an unknown id is logged and acknowledged.
- Event ids go into `billing_events` (unique, kept 30 days) in the same transaction as the plan update, so a redelivered event changes nothing twice.
- Each plan write carries the provider's event time, and an older write never replaces a newer one, whether it comes from the webhook or the poll.

## Rollout
1. Register the endpoint in the provider's test mode and send each event type from its console.
2. Register it in live mode; the hourly poll keeps running, so a missed event is caught within an hour.
3. Alert when refused deliveries or handler errors exceed 5 in 10 minutes.
4. Rollback: unregister the endpoint in the provider's console; the poll alone updates plans as before.

## Acceptance
- When a signed `subscription.updated` event arrives, the system shall update the account's plan. Verify: `pytest tests/webhooks/test_billing.py::test_signed_event_updates_plan`.
- When an event is older than five minutes, the system shall refuse it. Verify: `pytest tests/webhooks/test_billing.py::test_stale_delivery_refused`.
- When the same event arrives twice, the system shall apply it once. Verify: `pytest tests/webhooks/test_billing.py::test_redelivery_is_idempotent`.
