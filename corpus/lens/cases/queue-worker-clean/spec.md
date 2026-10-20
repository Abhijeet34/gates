# Spec: Move receipt emails onto a queue worker

## Goal
Checkout waits up to 2 s on the mail provider; send receipt emails from a queue worker so checkout returns at once.

## Scope
In scope: enqueueing the receipt job at checkout and a worker that sends it with the existing mail client.
Out of scope: other email types, template changes and a new queue technology.

## Design
- Use the existing `jobs` queue and its retry policy (5 attempts, exponential backoff); no new dependency.
- Checkout writes the job inside its order transaction, so an order never lacks its receipt job and a rolled-back order has none.
- The worker records the sent message id on the order, so a retried job never sends a second receipt.
- A job that fails all attempts lands in the dead-letter list with the order id.

## Rollout
1. Deploy the worker with the `receipts_via_queue` flag off, so it starts idle.
2. Turn the flag on for 5% of checkouts, then 100% after 24 h with no dead letters.
3. Alert when queue depth exceeds 500 or any job reaches the dead-letter list.
4. Rollback: turn `receipts_via_queue` off, and checkout sends inline again; queued jobs still drain.
5. Two weeks after 100%, remove the `receipts_via_queue` flag and the inline send path.
6. Running cost: one more worker process on the existing jobs host.

## Acceptance
- When a checkout completes, the system shall return before the email is sent. Verify: `pytest tests/checkout/test_receipt_queue.py::test_returns_before_send`.
- When the order transaction rolls back, the system shall leave no receipt job. Verify: `pytest tests/checkout/test_receipt_queue.py::test_rollback_leaves_no_job`.
- When the mail provider is down, the system shall retry 5 times and then dead-letter the job with its order id. Verify: `pytest tests/jobs/test_receipt_worker.py::test_dead_letters_after_retries`.
- When a job is retried after its email was sent, the system shall not send a second receipt. Verify: `pytest tests/jobs/test_receipt_worker.py::test_retry_sends_once`.
