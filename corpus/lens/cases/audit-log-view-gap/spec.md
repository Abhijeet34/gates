# Spec: Show the audit log to account owners

## Goal
Owners ask support who changed a setting; show them the audit log we already record.

## Scope
In scope: a read-only audit page for owners listing who changed what and when, newest first.

## Design
- Read from the existing `audit_events` table, filtered to the owner's account id, with its `(account_id, created_at)` index.
- Only the account owner can open the page, checked server side; anyone else gets 403.
- Pages of 50 events, newest first, with the actor's email and the setting's old and new value.
- With no events it says 'No changes recorded yet'; while loading it shows the table skeleton; on a failed load it shows an error with a retry.

## Rollout
1. Ship to everyone behind the `audit_page` flag, which gates both the route and its link.
2. Alert when the page's p95 load time exceeds 1 s.
3. Rollback: turn off `audit_page`.
4. Remove the `audit_page` flag two weeks after launch.

## Acceptance
- When an owner opens the audit page, the system shall list the account's events newest first, 50 a page. Verify: `pytest tests/audit/test_page.py::test_owner_sees_events`.
- When a member who is not the owner opens it, the system shall answer 403. Verify: `pytest tests/audit/test_page.py::test_member_refused`.
