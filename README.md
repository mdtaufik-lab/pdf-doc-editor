# Invoice Description Editor

A small local web app that rewrites the **line-item description** on contractor invoices
(PDF, Word `.docx` and `.doc`) to the fixed text **`Consultancy Fees`** and leaves everything
else on the invoice exactly as it was. You paste Google Drive links, the app downloads and
checks the invoices, edits them, and gives you a ZIP of the edited copies to re-share.

Which invoices to edit comes from the **Relook list** sheet of the business's Excel file.

## What it changes, and what it never touches

- **Changes:** the text in the description column(s) of the item table (headings like
  *Description*, *Particulars*, *Services*, *Details*, *Nature of work*). The whole cell
  becomes `Consultancy Fees`, in the same font, size, weight, colour and alignment.
- **Never changes:** invoice number, dates, names, addresses, GST/PAN, amounts, totals,
  TDS/GST rows, amount in words, bank details, signatures, logos, headers/footers, table
  headings, row heights, or the page count.
- **Checks every file before saving it.** Every word outside the edited cells must still be
  in the same place, and images, drawings and page count must be unchanged. A file that fails
  the check is **not saved** and is reported as `failed`.
- **Skips:** documents that are not invoices (passbook/cheque photos), invoices from companies
  (Shivaami, Cloudify Apps and other `Pvt Ltd` / `LLP` / … vendors), and files that are not in
  the Relook list.
- **Read-only on Google Drive.** The app only downloads. It never uploads, edits, moves,
  shares or deletes anything on Drive, and it never changes the downloaded originals.

## Requirements

- macOS (this is where it has been tested), Python 3.11 or newer
- [LibreOffice](https://www.libreoffice.org/), used to measure and check Word invoices
- [rclone](https://rclone.org/) with a Google Drive remote named `gdrive`
- The Relook Excel file (not included in this repository, because it holds personal data)

## Setup (one time)

```bash
# 1. Get the code
git clone https://github.com/mdtaufik-lab/pdf-doc-editor.git
cd pdf-doc-editor

# 2. Python packages
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. LibreOffice (needed for Word invoices)
brew install --cask libreoffice

# 4. Google Drive access through rclone
brew install rclone
rclone config
```

In `rclone config`: choose **n** (new remote), name it **`gdrive`**, choose **Google Drive** as
the storage type, leave the client ID and secret empty, pick the **read-only** scope
(`drive.readonly`), and say **yes** to auto config. A browser window opens: sign in with the
Google account that can see the invoices.

5. Put the Relook Excel file in the `docs/` folder. The app reads its **`Relook list`** sheet
   (columns `Name of person`, `Type`, `Description on the invoice`, `Invoice no.`,
   `Invoice amount`, `Invoice link`).

## Run the app

```bash
.venv/bin/streamlit run app.py
```

Open **http://localhost:8501**. The app only listens on this computer. Stop it with
`Ctrl+C` in the terminal.

## How to use it

1. **Paste the Google Drive links**, one per line, and click **Download & check**.
   Links like `drive.google.com/open?id=…`, `drive.google.com/file/d/…/view` and
   `docs.google.com/document/d/…` all work; duplicates are ignored.
   The app downloads each file into `Input/`, matches it to its Relook row and works out what
   it would change. **Nothing is edited yet.** A table shows each file, the description it
   found and its status.
2. Click **Edit**. Every file marked `ready_to_edit` is edited and checked. Pick any file in
   the list to see a before/after preview.
3. Click **Download edited invoices (ZIP)**. The ZIP has the edited invoices and a
   `report.csv` with one row per link. The edited files are also in `Output/`.

Keep the browser tab open while a step is running. Large batches take a while: about
0.5 s per file to check and 2–3 s per file to edit (roughly 10 + 40 minutes for 1,000 links).
Files that were already downloaded are not downloaded again.

### What the statuses mean

| Status | Meaning |
|---|---|
| `ready_to_edit` | Description found; will be changed when you click **Edit** |
| `edited` | Edited, checked and saved |
| `failed` | The check found a change outside the description, so nothing was saved; edit by hand |
| `needs_review` | The app could not safely tell what to change (scanned/photo PDF, Excel file, no description column, row without its own amount…); edit by hand |
| `excluded_not_invoice` | Passbook or cheque photo; left alone on purpose |
| `excluded_company` | Invoice from a company vendor; left alone on purpose |
| `not_in_relook_list` | File is not in the Relook list; left alone |
| `download_failed` | Could not download (no access, deleted, or Drive rate limit; try again later) |
| `invalid_link` | No Google Drive file ID in the pasted text |

## Command line (optional)

The same engine runs without the web page:

```bash
# edit every invoice in a folder
.venv/bin/python -m invoice_agent run --input Input --output Output

# preview what would change, without writing anything
.venv/bin/python -m invoice_agent run --input Input --dry-run

# show the edit plan for one file
.venv/bin/python -m invoice_agent plan "Input/<file>.pdf"

# summarise the Relook list and the exclusions
.venv/bin/python -m invoice_agent check
```

## Where things are saved

| Folder | Contents |
|---|---|
| `Input/` | Downloaded invoices, named `<drive file id>__<original name>` (never modified) |
| `Output/` | Edited invoices, same file names as in `Input/` |
| `runs/<date-time>/` | `report.csv`, the edit plan for each file (JSON) and before/after PNG previews |

A saved plan can be corrected by hand and re-applied with
`run --plans-from runs/<date-time>/plans`.

## How it works

- **PDF** (PyMuPDF): the description text is removed with redactions that leave lines,
  shading and images untouched, and `Consultancy Fees` is written on the original baseline.
  The font is the one embedded in the PDF when it has all the letters needed; otherwise the
  same font installed on the Mac; otherwise a metric-compatible clone from `fonts/`
  (Carlito for Calibri, Caladea for Cambria).
- **Word `.docx`** (python-docx + LibreOffice): Word lays text out when a file opens, so the app
  renders the invoice with LibreOffice to measure it, keeps each edited row at its original
  height and alignment, then renders it again to check nothing else moved.
- **Word `.doc`**: converted to `.docx` with LibreOffice, edited, converted back, and checked
  end to end. LibreOffice's `.doc` round trip often shifts the layout, so most `.doc` invoices
  come out `failed` and need a manual edit.
- **Choosing the description**: a rule-based planner reads the item table, finds the
  description column(s) and edits only rows that carry their own amount. Total, Sub-total, TDS,
  GST, deduction and payable rows keep their labels. An optional Claude-based planner
  (`--planner claude`, needs `ANTHROPIC_API_KEY`) can handle invoices without a clear table.

## Troubleshooting

- **"Google Drive is not connected"**: run `rclone config` and create a remote named `gdrive`
  (see Setup).
- **Downloads fail with a rate-limit message**: wait a few minutes and click
  **Download & check** again; finished files are kept. If *every* download fails, rclone's
  shared Google client may have been retired; set up your own client ID once:
  https://rclone.org/drive/#making-your-own-client-id
- **Word files fail with "LibreOffice is needed"**: install LibreOffice (see Setup).

## Project layout

```
app.py                 the web page (Streamlit); no logic, only calls invoice_agent/
invoice_agent/
  workflow.py          download & check, edit, ZIP: the steps behind the page
  drive.py             reads Drive links, downloads with rclone
  agent.py             per-file pipeline: match, exclusions, plan, edit, verify, report
  sheet.py             reads the Relook list, matches files to rows, exclusions
  planner.py           rule-based choice of the description cells
  planner_claude.py    optional Claude-based planner
  pdf_doc.py           PDF extraction, editing, verification, previews
  docx_doc.py          Word extraction, editing, verification
  fonts.py             picks the font for the replacement text
  config.py            paths, replacement text, exclusion list, patterns
  cli.py               command line
fonts/                 Carlito and Caladea (SIL Open Font License)
docs/                  put the Relook Excel file here (not committed)
CLAUDE.md              detailed project rules
```

## Data and privacy

Invoices and the Relook sheet contain names, addresses, PAN and bank details. The folders that
hold them (`Input/`, `Output/`, `runs/`, `docs/*.xlsx` and other download folders) are listed in
`.gitignore` and are never committed. Keep it that way.
