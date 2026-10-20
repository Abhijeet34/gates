# Spec: Openwrite, an open word processor for Acme Writer documents

## Request
Incumbent: Acme Writer. Product: Openwrite. Licence: MPL-2.0.
Formats: docx, odt. Workflows: edit a letter, review tracked changes. Jurisdictions: EU, UK.

## Name
Marks checked: "Acme Writer", "Acme", "AcmeDoc" and the Acme flame logo. Openwrite contains none of them, compared ignoring case, spacing, punctuation and diacritics.

## Look
- Every icon is drawn for Openwrite and published CC0; none is traced, exported or recoloured from Acme Writer.
- Menus, toolbars and dialogs keep the same layout as Acme Writer's, so current users find everything where they expect it.

## Interoperability
- Reads and writes docx per ECMA-376 (standard).
- Keeps Acme Writer's smart-quote autocorrect table (vendor-spec: its published format notes, v7).
- Opens Acme Writer files with legacy macros as inert text (observed: 40 sample files from our corpus; no spec exists).

## Acceptance
- When a user opens a docx file of 50 pages, the system shall show it within 2 seconds. Verify: `cargo test --test open_docx`.
- When a user saves an edited letter, the system shall write a docx file that the reference reader opens without repair. Verify: `make interop-check`.
- When the product name changes, the system shall fail the build if it contains a listed mark. Verify: `make name-check`.
