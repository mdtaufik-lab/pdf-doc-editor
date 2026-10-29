"""Project-wide settings: paths, replacement text, exclusions and classification patterns."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REPLACEMENT_TEXT = "Consultancy Fees"

DEFAULT_SHEET = next(iter(sorted((ROOT / "docs").glob("*.xlsx"))), None) \
    or ROOT / "Sheet" / "Invoices with detailed description - relook - copy.xlsx"
RELOOK_SHEET_NAME = "relook list"  # matched case-insensitively
DEFAULT_INPUT = ROOT / "Input"
DEFAULT_OUTPUT = ROOT / "Output"
RUNS_DIR = ROOT / "runs"
FONTS_DIR = ROOT / "fonts"

# --- Google Drive ------------------------------------------------------------------------
RCLONE_REMOTE = "gdrive:"  # rclone remote already signed in to Google Drive on this Mac
DRIVE_CACHE = ROOT / "Invoices"  # earlier bulk download, <drive id>/<name>; reused instead of re-downloading

# --- Exclusions (see CLAUDE.md) -------------------------------------------------------------
EXCLUDED_TYPES = {"not an invoice (passbook / cheque photo)"}

# Vendor companies named in the Relook list; matched as lowercase substrings of the name.
EXCLUDED_COMPANIES = [
    "shivaami", "shivami", "cloudify", "nodowntime", "ai arsenal", "apna samaaj",
    "pix brand", "rsg consultancy", "t.m.i. enterprise",
]
COMPANY_SUFFIX_RE = re.compile(
    r"\b(pvt|private limited|ltd|limited|llp|opc|technologies|enterprises?)\b", re.I
)

# --- Table classification ---------------------------------------------------------------------
# Header cells that name the description column(s).
DESCRIPTION_HEADER_RE = re.compile(
    r"\b(description|particulars|services?|details?|nature of (work|services?)|work done"
    r"|narration|item)\b",
    re.I,
)
AMOUNT_HEADER_RE = re.compile(r"\b(amount|amt|rs\.?|inr|fees?|price|value)\b|₹", re.I)

# Rows whose label starts like this are arithmetic rows (totals, taxes, deductions): keep as-is.
ARITHMETIC_LABEL_RE = re.compile(
    r"^\W*(less\b|total|sub[- ]?total|grand total|net\b|tds\b|gst\b|igst\b|cgst\b|sgst\b"
    r"|tax\b|round(ed)?[- ]?off|advance\b|deduction|amount payable|payable\b)",
    re.I,
)
ARITHMETIC_ANYWHERE_RE = re.compile(r"\b(deduction|deducted|total payable|net payable)\b", re.I)

# Employment-like wording that may remain on an invoice after editing (reported, not changed).
FLAGGED_WORDS_RE = re.compile(
    r"\b(leave|salary|remuneration|trial period|appointed|appointment|position|joining|joined"
    r"|working days|days working|attendance|employee|employment|stipend|notice period|probation"
    r"|ctc|designation|full\s*&\s*final|fnf)\b",
    re.I,
)

# --- Optional Claude planner -----------------------------------------------------------------
CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_EFFORT = "medium"
