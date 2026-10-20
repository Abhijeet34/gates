# Spec: Add a document upload dialog

## Goal
Users email documents to support; let them upload a document to their case from the case page.

## Scope
In scope: an upload dialog on the case page and its call to the existing upload API.
Out of scope: drag and drop, several files at once and previews.

## Design
- The dialog uses the existing modal component, with a file picker labelled 'Choose a document' and an 'Upload' button.
- It posts to the existing `POST /api/cases/{id}/documents`, which checks the caller's access to the case and refuses files over 20 MB.
- Until a file is chosen 'Upload' is disabled; while the upload runs the button shows progress; when it fails the dialog shows the server's reason and keeps the chosen file for a retry.

## Rollout
1. Ship behind the `case_upload` flag to 10% of users, then everyone after a week.
2. Alert when uploads fail with a server error more than 2% of the time.
3. Rollback: turn off `case_upload`, which hides the button.
4. Remove the `case_upload` flag two weeks after it reaches everyone.

## Acceptance
- When a user uploads a 1 MB PDF, the system shall attach it to the case. Verify: `npx playwright test e2e/upload.spec.ts --grep attaches`.
- When the file is over 20 MB or the user has no access to the case, the system shall refuse it. Verify: `pytest tests/cases/test_documents.py::test_refusals`.
