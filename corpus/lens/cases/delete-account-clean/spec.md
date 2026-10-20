# Spec: Let users delete their account

## Goal
Users must email support to close an account; add `DELETE /accounts/{id}` and a button in settings.

## Scope
In scope: the endpoint, the settings button and a confirmation step.
Out of scope: deleting organisation accounts and exporting data before deletion.

## Design
- The endpoint requires a signed-in session (else 401) and refuses with 403 unless the session's user owns the account `{id}`.
- It requires the user's password again in the request body, checked with the existing `verify_password`; a wrong password gets 403 with 'Password incorrect'.
- Deletion sets the existing `closed_at` column at once, and the existing retention job, which already erases accounts closed for 30 days, erases its data.
- The settings button opens a dialog naming what is deleted, with a password field labelled 'Your password'; the Delete button is disabled while the request runs, and an error shows inline.

## Rollout
1. Ship behind the `self_delete` flag for staff accounts, then everyone after a week.
2. Alert when closed accounts per day exceed three times the weekly average.
3. Rollback: turn off `self_delete`; a closed account is reopened by clearing its `closed_at` within the 30 days.
4. Remove the `self_delete` flag two weeks after it reaches everyone.

## Acceptance
- When a user deletes their own account with the right password, the system shall close it. Verify: `pytest tests/accounts/test_delete.py::test_owner_closes`.
- When a user asks to delete another user's account, the system shall answer 403. Verify: `pytest tests/accounts/test_delete.py::test_other_account_refused`.
- When the password is wrong, the system shall answer 403 and keep the account open. Verify: `pytest tests/accounts/test_delete.py::test_wrong_password_refused`.
- When there is no session, the system shall answer 401. Verify: `pytest tests/accounts/test_delete.py::test_anonymous_refused`.
