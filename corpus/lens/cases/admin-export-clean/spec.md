# Spec: Add an admin endpoint that exports all users

## Goal
Support staff need a CSV of all users for audits; add `GET /admin/users/export`.

## Scope
In scope: the endpoint, its CSV writer and a link in the admin panel.
Out of scope: filtering, scheduling exports and exporting other tables.

## Design
- The handler streams users from the existing `UserRepository.iter_all()`, so memory stays flat, and ends the file with a `# rows=N` line, so a truncated download is detectable.
- The route requires a signed-in session with the `administrator` role, checked by the existing `require_role` decorator; a caller without a session gets 401 and any other user 403.
- Columns are id, email, created_at and plan; password hashes and tokens are never written.
- The export is written to the audit log with the staff member's id before streaming starts; if that write fails, the export is refused with 500.
- The admin panel gets a plain link named 'Export users (CSV)'; the browser shows the download's progress.

## Rollout
1. Ship behind the `admin_export` flag, on for the support team only.
2. Alert when the route returns any 5xx.
3. Rollback: turn off `admin_export`, which removes the route.
4. Remove the `admin_export` flag two weeks after it is on.

## Acceptance
- When an administrator requests the export, the system shall return a CSV with one row per user, the four columns and the rows line. Verify: `pytest tests/admin/test_export.py::test_admin_gets_csv`.
- When any other signed-in user requests it, the system shall answer 403. Verify: `pytest tests/admin/test_export.py::test_non_admin_refused`.
- When a caller without a session requests it, the system shall answer 401. Verify: `pytest tests/admin/test_export.py::test_anonymous_refused`.
