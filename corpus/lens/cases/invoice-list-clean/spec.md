# Spec: Add an invoice list screen

## Goal
Customers find invoices only in emails; add a screen listing their invoices with a download link each.

## Scope
In scope: the invoice list screen in the web app and its route.
Out of scope: paying from the screen, filtering and the mobile apps.

## Design
- The screen reads the existing `GET /api/invoices`, which already checks the caller's account and returns each invoice's `pdf_url`.
- A table shows number, date, amount and a 'Download PDF' link, using the existing table component and tokens.
- With no invoices it says 'No invoices yet'; while loading it shows the table skeleton; on a failed request it shows 'Could not load invoices' with a retry button.

## Rollout
1. Ship behind the `invoice_list` flag, which gates the route and its menu entry, to staff, then everyone after a week.
2. Alert when invoice requests or PDF downloads fail more than 1% of the time.
3. Rollback: turn off `invoice_list`.
4. Remove the `invoice_list` flag two weeks after it reaches everyone.

## Acceptance
- When a customer with invoices opens the screen, the system shall list each invoice with a download link. Verify: `npx playwright test e2e/invoices.spec.ts --grep lists`.
- When a customer follows a download link, the system shall serve that invoice's PDF. Verify: `npx playwright test e2e/invoices.spec.ts --grep download`.
