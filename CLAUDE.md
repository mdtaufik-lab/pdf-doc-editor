# Invoice Description Editor — Project Rules

## What this project does

A Python agent with a local web UI (runs on `localhost`) that:

1. Connects to the user's Google Drive (Google login is prompted on first run).
2. Takes Google Drive invoice links pasted into the UI and downloads those invoices to a local folder.
3. Parses each invoice (PDF, DOCX, DOC) and finds its line-item description.
4. When the user clicks **Edit**, rewrites the line-item description to the fixed text
   **`Consultancy Fees`**, leaving everything else on the invoice exactly as it was.
5. Saves the edited copies to `Output/` and lets the user download them.

All invoices currently used are demo files.

Source of truth for which invoices to edit: the Excel file in `docs/`
(currently `Invoices with detailed description - relook - copy.xlsx`) → sub-sheet
**`Relook list`** (~1,104 rows). Columns used: `Name of person`, `Type`,
`Description on the invoice`, `Invoice no.`, `Invoice amount`, `Invoice link` (Google Drive ID).

## How to work on this project

Build in phases, in order. Finish one phase, show the user what it does, and wait for the
user's go-ahead before starting the next. Do not build later-phase features early.

---

## Phase 0 — Setup and understanding the invoices

Goal: understand the inputs before writing the parser.

- Read the `Relook list` sub-sheet from the Excel file in `docs/`.
- Set up Google Drive login (see *Google Drive access*). On first run the user is sent to the
  browser to log in and allow access.
- Download a small sample of invoices (about 10–20, covering PDF, DOCX and DOC) from the
  `Invoice link` column into `samples/`.
- Open each sample and note its format: text PDF, scanned/image-only PDF, DOCX, DOC, native
  Google Doc, or image. For table invoices, note the description-column heading and row layout.
- Write the findings to `docs/invoice-formats.md`. The Phase 1 parser is built from these findings.

Done when: Drive login works, the sheet loads, samples are downloaded, and
`docs/invoice-formats.md` exists.

## Phase 1 — Download and parse (UI button: **Parse**)

UI:
- A text box where the user pastes one or more Google Drive links (one per line).
- A **Parse** button.

When **Parse** is clicked:
1. Extract the file ID from each link. Supported links: `drive.google.com/file/d/<ID>/...`,
   `drive.google.com/open?id=<ID>`, `drive.google.com/uc?id=<ID>`,
   `docs.google.com/document/d/<ID>/...`. A link with no file ID is reported as `invalid_link`.
2. Download each file into `Input/` as `<fileId>__<original filename>`, so the Drive ID is in
   the filename for matching. Native Google Docs are exported as DOCX. Files already in `Input/`
   are not downloaded again. Files that can't be downloaded (no access, deleted) are reported
   as `download_failed`.
3. Match each file to a Relook row (see *Matching*), apply the *Exclusions*, and run the
   planner to find the description cells. **Nothing is edited in this phase.**
4. Show the result in the UI:
   - Counts: total links, downloaded, ready to edit, excluded, not in Relook list,
     needs review, failed.
   - A table with one row per file: file name, format, matched person, current description,
     planned change, status.

Statuses: `ready_to_edit`, `excluded_not_invoice`, `excluded_company`, `not_in_relook_list`,
`needs_review`, `invalid_link`, `download_failed`.

Done when: pasting the demo links downloads them and the counts and table are correct.

## Phase 2 — Edit descriptions (UI button: **Edit**)

- An **Edit** button, enabled only after Parse has run. Only files with status `ready_to_edit`
  are edited.
- For each file: remove the existing description text from the description cell(s) and write
  `Consultancy Fees` in its place, following *Hard rules*, *What counts as the description*,
  *Fonts* and *Formats*.
- Verify every file before writing it (Hard rule 6). If verification fails, the file is
  reported as `failed` and not written.
- Show counts (edited, failed, skipped) and the before/after preview for each edited file.

Done when: the demo files are edited, verification passes, and the previews look right.

## Phase 3 — Output and download

- Edited files go to `Output/` with the **same filename** as the input file.
- Each run writes `runs/<timestamp>/` with `report.csv`, the JSON edit plans, and before/after
  PNG previews.
- The UI shows the final summary and a **Download** button that gives a ZIP of the edited files
  from this run plus `report.csv`.

Done when: the user can download the ZIP and the edited files open correctly.

---

## Google Drive access

- Downloads use **rclone's `gdrive:` remote**, already signed in on this Mac
  (`invoice_agent/drive.py`, `rclone backend copyid gdrive: <id> <dir>/`). The user chose this
  over a Google Cloud OAuth client: no `credentials.json` / `token.json` setup.
- The agent only reads and downloads from Drive. It never uploads, edits, moves, shares or
  deletes anything on Drive.
- Files already in `Input/`, or in the earlier bulk download `Invoices/<id>/`, are not
  downloaded again.
- rclone's shared Google client is rate-limited (403 "Quota exceeded"; downloads retry) and
  Google is retiring it during 2026. If downloads start failing for every file, the fix is a
  one-time own client ID for rclone (`rclone config`, https://rclone.org/drive/#making-your-own-client-id).
- Never print rclone's config (`rclone config show`): it holds the Google token.

## The task rule (from the business)

> Check the relook list. Except the cases below, change the description in every invoice to
> **Consultancy Fees** and reshare.
> - Not an invoice (passbook / cheque photo)
> - Company names like Shivaami or Cloudify Apps etc.

## Hard rules — never break these

1. **Never modify originals.** Input folders (`Input/`, `samples/`, `Demo-Invoices/`, any
   downloaded invoice folder) are read-only, and files on Google Drive are never changed.
   Write results only to `Output/`, using the **same filename** as the input.
2. **Change only line-item descriptions.** Do not touch: invoice number, dates, period of invoice,
   names, addresses, GST/PAN, mobile numbers, amounts, totals, TDS/GST lines, amount-in-words,
   bank details, signatures, logos, headers/footers, table headings, or any other text.
3. **No structural change.** Do not add or remove rows, lines, pages, columns, borders, shading
   or images. Table row heights stay the same; the freed space inside a cell stays blank.
4. **Look identical.** The replacement text must use the same font family, size, weight, colour,
   and alignment as the text it replaces, sitting on the same baseline as the row's other text.
5. **Exact replacement text:** `Consultancy Fees` (capital C, capital F, no full stop).
6. **Verify every file before writing it.** Every word outside the edited cells must be unchanged
   (same text, same position), drawing/image counts and page count unchanged, and each edited
   cell must read exactly `Consultancy Fees`. If verification fails, do **not** write the output;
   report the file as `failed` instead.
7. **When unsure, skip and flag — never guess.** Scanned/image-only PDFs, invoices with no
   identifiable description column, and files that can't be matched to a Relook row are reported
   as `needs_review`, not edited.

## Exclusions (do NOT edit)

- `Type` == `Not an invoice (passbook / cheque photo)` (10 rows).
- Vendor companies (not individual contractors). Current list, kept in `invoice_agent/config.py`:
  Shivaami Cloud Services Pvt Ltd, Cloudifyapps (OPC) Private Limited, Nodowntime Technologies,
  Ai Arsenal Private Limited, Apna Samaaj Tech Private Limited, Pix Brand Pvt Ltd,
  RSG Consultancy, T.M.I. Enterprise. Any name with a corporate suffix (Pvt, Private Limited,
  Ltd, LLP, OPC, Technologies, Enterprise…) is also treated as a company.
- Files not present in the Relook list are left alone (`not_in_relook_list`).

Ask the user before adding to or removing from the exclusion list.

## What counts as "the description"

- The description is the text in the description-type column(s) of the item table — headers like
  *Description*, *Particulars*, *Services*, *Details*, *Description of sessions conducted*,
  *Nature of work*. If a table has two such columns (e.g. *Services* + *Description*), both
  cells of a billable row become `Consultancy Fees`.
- Replace the **whole cell** (including sub-lines such as "Moderator: …" or
  "(Session for 2 hours)"), not just the first line.
- Only **billable rows** are rewritten: rows that carry their own amount. **Arithmetic rows keep
  their labels**: Total, Sub-total, TDS, GST/IGST/CGST/SGST, Less/Deduction, Net/Total Payable,
  merged summary rows. These are listed in the run report when they still contain flagged
  words (leave, salary, remuneration, trial, appointed, working days…) so a person can decide.
- Table headings (e.g. "Description of sessions conducted") are not changed.

## Matching a file to a Relook row

Drive file ID in the filename (exact) → otherwise fuzzy match on person name + invoice number +
amount found in the document text.

## Fonts

Resolution order for the replacement text (see `invoice_agent/fonts.py`):
1. The font embedded in the PDF, if its subset has every glyph of `Consultancy Fees`.
2. The real font installed on this Mac (e.g. `Times New Roman.ttf`, `Arial.ttf`).
3. A metric-compatible clone in `fonts/` (Carlito = Calibri, Caladea = Cambria).
4. Base-14 fallback (Times/Helvetica) — reported as a warning.

## Formats

- **PDF**: PyMuPDF. Remove the description glyphs with redactions that leave vector graphics and
  images untouched (`graphics=PDF_REDACT_LINE_ART_NONE`, `images=PDF_REDACT_IMAGE_NONE`, no fill),
  then insert `Consultancy Fees` at the original baseline.
- **DOCX**: python-docx, and LibreOffice is required. The longest run of the cell's first line
  keeps its formatting and gets the text. Word lays text out when the file opens, so a wrapped
  description would shrink its row: the agent renders the invoice with LibreOffice (edited
  paragraphs in marker colours, which never change layout) to measure it. An edited table row
  gets its original height as a minimum (`atLeast`) row height, the cell keeps only the
  paragraph with the text (list numbering removed), and when the row's amount sits lower than
  the description's first line the cell takes the amount cell's vertical alignment. A second
  render must show every other word in place (±1pt) on the same page count, or the file is
  `failed`.
- **DOC**: convert to DOCX with LibreOffice (`soffice`), edit, convert back to `.doc`. Without
  LibreOffice the agent falls back to macOS `textutil`, which **drops tables**, so table invoices
  come out as `needs_review` — install LibreOffice (`brew install --cask libreoffice`) before a
  batch that contains `.doc` files. The round trip is checked end to end (original `.doc` vs
  final `.doc`, rendered); LibreOffice's `.doc` round trip usually moves the layout, so `.doc`
  invoices mostly come out `failed` and are edited by hand.
- **Files without an extension** are recognised by content (PDF / Word) and keep their name.
- **Native Google Docs**: exported from Drive as DOCX in Phase 1, then handled as DOCX.
- **Images** (JPG/PNG): not edited; reported as `needs_review`.

## Project layout

```
docs/               Relook Excel sheet, invoice-formats.md
samples/            Phase 0 sample downloads (read-only)
Input/              invoices downloaded from Drive (read-only)
Output/             edited invoices (same filenames as Input/)
runs/<timestamp>/   report.csv, plans/, previews/
invoice_agent/      agent code (config.py, planner.py, fonts.py, drive.py, ...)
app.py              local web UI (.streamlit/config.toml: localhost only, no usage stats)
fonts/              metric-compatible font clones
Invoices/<id>/      earlier bulk download of the Relook list (used as a download cache)
```

## How to run

```bash
# one-time
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
brew install rclone && rclone config   # once: a Google Drive remote named "gdrive"
brew install --cask libreoffice        # needed for Word/.doc invoices

# start the local UI (opens http://localhost:8501)
.venv/bin/streamlit run app.py

# command line, same engine as the UI
.venv/bin/python -m invoice_agent run --input Input --output Output
.venv/bin/python -m invoice_agent run --input Input --dry-run
.venv/bin/python -m invoice_agent plan "Input/<file>.pdf"
```

**Always look at the previews** before telling the user a batch is done.

Planner: the rule-based planner (`planner.py`) handles table invoices. `--planner claude` (or
`auto`, the default, when the rule-based planner finds nothing) asks Claude to pick the
description blocks; it needs `ANTHROPIC_API_KEY`. A saved plan JSON can be edited by hand and
re-applied with `--plans-from runs/<timestamp>/plans`.

## Code conventions

- Python 3.11+, type hints, `pathlib.Path`, dataclasses. Keep modules small and single-purpose.
- UI is Streamlit (`app.py`). The UI only calls functions in `invoice_agent/`; no parsing or
  editing logic lives in `app.py`.
- Network calls only to the Google Drive API and the optional Claude planner.
- Main libraries: PyMuPDF, python-docx, openpyxl, pandas, streamlit, google-api-python-client,
  google-auth-oauthlib.
- Large batches: run on a small sample first, review previews, then the full set.