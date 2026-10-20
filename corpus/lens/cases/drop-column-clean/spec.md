# Spec: Drop the unused legacy name column

## Goal
`users.legacy_name` has not been written since 2024 and confuses new code; remove it.

## Scope
In scope: removing every read of the column and a migration dropping it.
Out of scope: renaming other columns and changing `display_name`.

## Design
- A search of the codebase finds two reads, in the admin template and the CSV export; both switch to `display_name`.
- The migration runs `ALTER TABLE users DROP COLUMN legacy_name`, which cannot be undone without a backup.

## Rollout
1. Ship the code that stops reading the column; the error tracker alerts on any error naming `legacy_name` for a week.
2. Dry run: run the migration on a production snapshot first, timing the lock, then copy `id, legacy_name` into `legacy_name_backup` in production.
3. Run the migration in production in the low-traffic window.
4. Rollback: re-add the column and restore it from `legacy_name_backup`, which is dropped after 90 days.

## Acceptance
- When the migration has run, the system shall have no `legacy_name` column. Verify: `scripts/column-absent.sh users legacy_name` exits 0.
- When the admin page and the CSV export render a user, the system shall show `display_name`. Verify: `pytest tests/admin/test_user_page.py tests/export/test_users_csv.py`.
