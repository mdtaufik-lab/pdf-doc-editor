"""The steps behind the web page: download & check, edit, package the results.

Checking is a dry run of the agent (match, exclusions, plan; nothing is edited). Editing
re-applies exactly the plans the check made, so what the table promised is what gets edited.
"""
from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from . import drive
from .agent import FileResult, InvoiceAgent
from .config import DEFAULT_INPUT, DEFAULT_OUTPUT, DEFAULT_SHEET

Progress = Callable[[str, int, int], None]  # (stage, done, total)
DOWNLOAD_WORKERS = 4


@dataclass
class Item:
    """One pasted link on its way through download, check and edit."""
    link: str
    file_id: str | None
    status: str = ""  # see CLAUDE.md: ready_to_edit, edited, invalid_link, download_failed, ...
    detail: str = ""
    file: Path | None = None
    check: FileResult | None = None
    edit: FileResult | None = None


@dataclass
class Batch:
    items: list[Item] = field(default_factory=list)
    check_run: Path | None = None
    edit_run: Path | None = None
    zip_path: Path | None = None

    @property
    def ready(self) -> list[Item]:
        return [i for i in self.items if i.status == "ready_to_edit"]


def check(text: str, progress: Progress | None = None) -> Batch:
    """Download every linked invoice and plan its edit, without editing anything."""
    progress = progress or (lambda *_: None)
    batch = Batch([Item(l.text, l.file_id) for l in drive.parse_links(text)])
    for item in batch.items:
        if not item.file_id:
            item.status, item.detail = "invalid_link", "no Google Drive file ID in this link"

    todo = [i for i in batch.items if i.file_id]
    with ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
        futures = {pool.submit(drive.download, i.file_id, DEFAULT_INPUT): i for i in todo}
        for n, fut in enumerate(as_completed(futures), 1):
            item = futures[fut]
            try:
                item.file = fut.result()
            except Exception as e:
                item.status, item.detail = "download_failed", str(e)
            progress("Downloading", n, len(todo))

    files = [i for i in todo if i.file]
    agent = InvoiceAgent(DEFAULT_SHEET, DEFAULT_OUTPUT, dry_run=True, input_roots=[DEFAULT_INPUT])
    results = agent.run([i.file for i in files], progress=lambda n, total, _: progress("Checking", n, total))
    for item, r in zip(files, results):
        item.check, item.status, item.detail = r, r.status, r.detail
    batch.check_run = agent.run_dir
    return batch


def edit(batch: Batch, progress: Progress | None = None) -> Batch:
    """Edit every ready_to_edit invoice into Output/ and package them as a ZIP."""
    progress = progress or (lambda *_: None)
    ready = batch.ready
    agent = InvoiceAgent(DEFAULT_SHEET, DEFAULT_OUTPUT, plans_from=batch.check_run / "plans",
                         input_roots=[DEFAULT_INPUT])
    results = agent.run([i.file for i in ready], progress=lambda n, total, _: progress("Editing", n, total))
    for item, r in zip(ready, results):
        item.edit, item.status, item.detail = r, r.status, r.detail
    batch.edit_run = agent.run_dir
    batch.zip_path = _package(batch)
    return batch


def check_counts(batch: Batch) -> dict[str, int]:
    count = lambda *statuses: sum(i.status in statuses for i in batch.items)  # noqa: E731
    return {
        "Links": len(batch.items),
        "Downloaded": sum(i.file is not None for i in batch.items),
        "Ready to edit": count("ready_to_edit", "edited", "failed") if batch.edit_run else count("ready_to_edit"),
        "Excluded": count("excluded_not_invoice", "excluded_company"),
        "Not in Relook list": count("not_in_relook_list"),
        "Needs review": count("needs_review"),
        "Failed": count("invalid_link", "download_failed"),
    }


def edit_counts(batch: Batch) -> dict[str, int]:
    edited = sum(i.status == "edited" for i in batch.items)
    failed = sum(i.status == "failed" for i in batch.items)
    return {"Edited": edited, "Edit failed": failed, "Skipped": len(batch.items) - edited - failed}


def table(batch: Batch) -> list[dict[str, str]]:
    rows = []
    for i in batch.items:
        r = i.edit or i.check
        rows.append({
            "File": i.file.name if i.file else i.link,
            "Format": (r.format.lstrip(".") if r else ""),
            "Person": r.person if r else "",
            "Current description": (r.current if r else "").replace(" || ", "  |  "),
            "Planned change": f"{r.edits} cell(s) → Consultancy Fees" if r and r.edits else "",
            "Status": i.status,
            "Note": i.detail,
        })
    return rows


def previews(item: Item) -> list[Path]:
    return [Path(p) for p in item.edit.previews.split("|") if p] if item.edit else []


def _package(batch: Batch) -> Path:
    """ZIP of this run's edited invoices plus report.csv (one row per pasted link)."""
    path = batch.edit_run / "edited-invoices.zip"
    report = io.StringIO()
    writer = csv.DictWriter(report, fieldnames=["Link", *table(batch)[0].keys()] if batch.items else ["Link"])
    writer.writeheader()
    for item, row in zip(batch.items, table(batch)):
        writer.writerow({"Link": item.link, **row})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:  # PDFs/DOCX are compressed already
        for item in batch.items:
            if item.status == "edited" and item.edit and item.edit.output:
                z.write(item.edit.output, Path(item.edit.output).name)
        z.writestr("report.csv", report.getvalue())
    return path
