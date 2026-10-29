"""The agent loop: match each invoice to the Relook list, plan, edit, verify, report."""
from __future__ import annotations

import csv
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

from . import docx_doc, pdf_doc, planner_claude
from .config import FLAGGED_WORDS_RE, REPLACEMENT_TEXT, RUNS_DIR
from .models import Plan, VerificationError
from .planner import plan_heuristic
from .sheet import exclusion_reason, load_relook, match_row

SUPPORTED = {".pdf", ".docx", ".doc"}


def file_format(path: Path) -> str:
    """The file's real format, read from its content when the extension isn't a supported one."""
    if path.suffix.lower() in SUPPORTED:
        return path.suffix.lower()
    with path.open("rb") as fh:
        head = fh.read(4)
    if head == b"%PDF":
        return ".pdf"
    if head == b"PK\x03\x04" and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            if "word/document.xml" in z.namelist():
                return ".docx"
    return path.suffix.lower()


@dataclass
class FileResult:
    file: str
    # ready_to_edit (dry run) | edited | failed | excluded_not_invoice | excluded_company
    # | not_in_relook_list | needs_review
    status: str
    detail: str = ""
    format: str = ""
    current: str = ""  # the description text that is (or would be) replaced
    sheet_row: str = ""
    person: str = ""
    type: str = ""
    planner: str = ""
    edits: int = 0
    kept_rows: str = ""
    residual_flags: str = ""
    fonts: str = ""
    warnings: str = ""
    output: str = ""
    previews: str = ""  # before/after PNGs, "|"-separated


class InvoiceAgent:
    def __init__(self, sheet: Path, output_dir: Path, planner: str = "auto",
                 dry_run: bool = False, plans_from: Path | None = None,
                 input_roots: list[Path] | None = None):
        self.rows = load_relook(sheet)
        self.output_dir = output_dir
        self.planner = planner
        self.dry_run = dry_run
        self.plans_from = plans_from
        self.input_roots = [r.resolve() for r in input_roots or []]
        self.run_dir = RUNS_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
        for sub in ("plans", "previews", "tmp"):
            (self.run_dir / sub).mkdir(parents=True, exist_ok=True)

    def run(self, files: list[Path],
            progress: Callable[[int, int, FileResult], None] | None = None) -> list[FileResult]:
        if not self.dry_run:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        results = []
        with (self.run_dir / "report.csv").open("w", newline="") as fh:
            # written row by row, so a crash part-way through a batch keeps what was done
            writer = csv.DictWriter(fh, fieldnames=[f.name for f in fields(FileResult)])
            writer.writeheader()
            for i, f in enumerate(files, 1):
                try:
                    r = self.process(f)
                except Exception as e:  # one bad file must not stop a batch of 1,100
                    r = FileResult(str(self._relative(f)), "failed", detail=f"{type(e).__name__}: {e}")
                results.append(r)
                writer.writerow(asdict(r))
                fh.flush()
                print(f"[{i}/{len(files)}] [{r.status:>18}] {r.file}  {r.detail}", flush=True)
                if progress:
                    progress(i, len(files), r)
        self._summarise(results)
        shutil.rmtree(self.run_dir / "tmp", ignore_errors=True)
        return results

    def process(self, path: Path) -> FileResult:
        rel = self._relative(path)  # keeps same-named files in different folders apart
        res = FileResult(str(rel), "needs_review")
        fmt = res.format = file_format(path)
        if fmt not in SUPPORTED:
            row, _ = match_row(self.rows, path, "")  # the Drive ID still matches photos/spreadsheets
            if row and self._excluded(res, row):
                return res
            res.detail = f"{path.suffix or 'extension-less'} files are not handled - edit manually"
            return res

        work, warnings = path, []
        if fmt != path.suffix.lower():  # e.g. a Word file saved without ".docx": edit a copy that has it
            work = self.run_dir / "tmp" / f"{path.name}{fmt}"
            shutil.copyfile(path, work)
        if fmt == ".doc":
            work = docx_doc.convert(work, "docx", self.run_dir / "tmp")
            if not docx_doc.soffice():
                warnings.append("converted .doc with textutil; output is .docx - check formatting")
        handler = pdf_doc if work.suffix.lower() == ".pdf" else docx_doc

        layout = handler.extract_layout(work)
        if not layout.full_text.strip():
            res.detail = "no text layer (scanned/image file) - edit manually"
            return res

        row, why = match_row(self.rows, path, layout.full_text)
        if row is None:
            res.status = "not_in_relook_list" if why.startswith("no row") else "needs_review"
            res.detail = why
            return res
        if self._excluded(res, row):
            return res

        plan = self._plan(rel, work, handler, layout)
        plan_file = self.run_dir / "plans" / f"{rel}.json"
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan.save(plan_file)
        res.planner, res.edits = plan.planner, len(plan.edits)
        res.kept_rows = " || ".join(plan.kept_rows)
        res.current = " || ".join(e.reason for e in plan.edits)
        if not plan.edits:
            res.detail = "; ".join(plan.notes) or "no description found"
            return res
        if self.dry_run:
            res.status, res.detail = "ready_to_edit", f"matched by {why}"
            return res

        out = self._output_path(rel)
        edited = out if out.suffix.lower() != ".doc" else self.run_dir / "tmp" / f"{path.stem}.edited.docx"
        previews = self.run_dir / "previews" / rel.parent
        previews.mkdir(parents=True, exist_ok=True)
        try:
            applied = handler.apply_plan(work, plan, edited, REPLACEMENT_TEXT, previews)
        except VerificationError as e:
            res.status, res.detail = "failed", f"verification: {e}"
            return res
        if out.suffix.lower() == ".doc":
            final = docx_doc.convert(edited, "doc", self.run_dir / "tmp" / "doc")
            ids = {b for e in plan.edits for b in e.block_ids}
            removed = [c.text for c in [c for t in layout.tables for r in t.rows for c in r.cells] + layout.free_blocks
                       if c.id in ids]
            try:
                docx_doc.verify_roundtrip(path, final, removed, len(plan.edits), REPLACEMENT_TEXT,
                                          self.run_dir / "tmp" / "check", previews / work.stem)
            except VerificationError as e:
                res.status, res.detail = "failed", f"verification (.doc round trip): {e}"
                return res
            shutil.move(final, out)

        flags = {m.group(0).lower() for m in FLAGGED_WORDS_RE.finditer(handler.extract_layout(edited).full_text)}
        res.status, res.detail = "edited", f"matched by {why}"
        res.residual_flags = ", ".join(sorted(flags))
        res.fonts = ", ".join(applied.fonts)
        res.warnings = "; ".join(warnings + applied.warnings)
        res.output = str(out)
        res.previews = "|".join(str(p) for p in applied.previews)
        return res

    @staticmethod
    def _excluded(res: FileResult, row) -> bool:
        """Record the matched row on res; True (and the status set) when the row is excluded."""
        res.sheet_row, res.person, res.type = str(row.sheet_row), row.name, row.type
        if reason := exclusion_reason(row):
            res.status = "excluded_not_invoice" if reason.startswith("type") else "excluded_company"
            res.detail = reason
            return True
        return False

    def _plan(self, rel: Path, work: Path, handler, layout) -> Plan:
        saved = self.plans_from / f"{rel}.json" if self.plans_from else None
        if saved and saved.exists():
            plan = Plan.load(saved)
            plan.planner = f"saved:{saved}"
            return plan
        if self.planner != "claude":
            plan = plan_heuristic(rel.name, layout)
            if plan.edits or self.planner == "rules" or not planner_claude.available():
                return plan
        return planner_claude.plan_with_claude(rel.name, handler.extract_layout(work, with_images=True))

    def _relative(self, path: Path) -> Path:
        """Path below the input folder it came from (just the name for files given directly)."""
        full = path.resolve()
        return next((full.relative_to(r) for r in self.input_roots if full.is_relative_to(r)), Path(path.name))

    def _output_path(self, rel: Path) -> Path:
        if rel.suffix.lower() == ".doc" and not docx_doc.soffice():
            rel = rel.with_suffix(".docx")
        out = self.output_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        return out

    def _summarise(self, results: list[FileResult]) -> None:
        counts: dict[str, int] = {}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
        print(f"\n{len(results)} file(s): " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        print(f"Report, plans and before/after previews: {self.run_dir}")
