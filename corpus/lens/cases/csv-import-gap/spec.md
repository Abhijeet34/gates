# Spec: Import contacts from a CSV file

## Goal
Users type contacts in one at a time; let them upload a CSV of name, email and phone.

## Scope
In scope: an upload form, a parser and a summary of imported and skipped rows.
Out of scope: other file formats, deduplication across existing contacts and mapping custom columns.

## Design
- The form and its endpoint require a signed-in user, and contacts are created in that user's account.
- Parse with the standard library's `csv` module; a file whose first row is not `name,email,phone` is refused with that message.
- A row with an invalid email is skipped and listed in the summary with its line number.
- A file over 5 MB is refused before it is read, and one over 10,000 rows is refused as soon as row 10,001 is read, before any contact is created.
- Each import gets a batch id, and the summary offers 'Undo this import', which deletes that batch.
- The form shows progress while the import runs and, on a refusal, the reason and the limit.

## Rollout
1. Ship behind the `csv_import` flag for ten pilot accounts, then everyone after a week.
2. Alert when more than 5% of imports fail with a server error.
3. Rollback: turn off `csv_import`; imported contacts stay, as if typed in.
4. Remove the `csv_import` flag two weeks after it reaches everyone.

## Acceptance
- When a valid 3-row file is uploaded, the system shall create 3 contacts.
- When a row has an invalid email, the system shall skip it and name its line.
- When a file has a wrong header, the system shall refuse it and create nothing.
- When a file has 10,001 rows or is over 5 MB, the system shall refuse it and create nothing.
