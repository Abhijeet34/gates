# Spec: Sheetleaf, an open spreadsheet for Northwind Sheets documents

## Request
Incumbent: Northwind Sheets. Product: Sheetleaf. Licence: MPL-2.0.
Formats: xlsx, ods. Workflows: total a ledger, filter a table. Jurisdictions: EU, UK.

## Name
Marks checked: "Northwind Sheets", "Northwind", "NW Sheets" and the Northwind compass logo (the word "sheet" alone is generic and not claimed). Sheetleaf contains none of them, compared ignoring case, spacing, punctuation and diacritics.

## Look
- Every icon is drawn for Sheetleaf and published CC0; none is traced, exported or recoloured from Northwind Sheets.
- Screens come from our own usability sessions; the toolbar, menus and dialogs are not arranged to match Northwind Sheets's.

## Interoperability
- Reads and writes xlsx per ECMA-376 (standard).
- Keeps Northwind Sheets' circular-reference warning text (vendor-spec: its published error table, v3).
- Opens Northwind Sheets files with external links as inert text (observed: 30 sample files from our corpus; no spec exists).

## Acceptance
- When a user opens a xlsx file of 10,000 rows, the system shall show it within 2 seconds. Verify: `cargo test --test open_xlsx`.
- When a user saves an edited ledger, the system shall write a xlsx file that the reference reader opens without repair. Verify: `make interop-check`.
- When the product name changes, the system shall fail the build if it contains a listed mark. Verify: `make name-check`.
