# Spec: Expire password reset links after 30 minutes

## Goal
Reset links never expire, so a leaked email works forever; make them expire after 30 minutes and after one use.

## Scope
In scope: the token's expiry time, single use and the message shown for a dead link.
Out of scope: changing the reset email's wording and rate-limiting reset requests.

## Design
- Store `expires_at` and `used_at` on the existing `password_resets` row; a row with no `expires_at` counts as expired.
- `used_at` is set by one conditional update (`used_at is null and expires_at > now()`), so two requests cannot both use a link.
- A link past `expires_at` or with `used_at` set shows 'This link has expired' and a button to request a new one.

## Rollout
1. Deploy; the migration expires every link issued before the deploy, so their owners request new ones.
2. Alert when reset completions per day fall below half the weekly average.
3. Rollback: revert the deploy; the new columns are ignored by the old code.

## Acceptance
- When a reset link is used within 30 minutes, the system shall let the user set a password. Verify: `pytest tests/auth/test_reset.py::test_fresh_link_works`.
- When a reset link is opened after 30 minutes, the system shall show the expired message. Verify: `pytest tests/auth/test_reset.py::test_old_link_expired`.
- When a reset link is used twice, including at the same moment, the system shall refuse the second use. Verify: `pytest tests/auth/test_reset.py::test_second_use_refused`.
- When a link was issued before the deploy, the system shall treat it as expired. Verify: `pytest tests/auth/test_reset.py::test_pre_deploy_link_expired`.
