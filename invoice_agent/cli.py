"""Command line: `python -m invoice_agent {run,plan,check} ...`"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .agent import InvoiceAgent
from .config import DEFAULT_INPUT, DEFAULT_OUTPUT, DEFAULT_SHEET
from .sheet import exclusion_reason, load_relook


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="invoice_agent", description="Rewrite invoice descriptions to 'Consultancy Fees'.")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="edit invoices and write them to the output folder")
    run.add_argument("--input", type=Path, nargs="+", default=[DEFAULT_INPUT], help="files or folders")
    run.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    run.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    run.add_argument("--planner", choices=["auto", "rules", "claude"], default="auto")
    run.add_argument("--plans-from", type=Path, help="folder of saved (hand-edited) plan JSON files")
    run.add_argument("--dry-run", action="store_true", help="plan only; write nothing to the output folder")
    run.add_argument("--limit", type=int, help="process only the first N files")

    plan = sub.add_parser("plan", help="print the edit plan for one file")
    plan.add_argument("file", type=Path)
    plan.add_argument("--planner", choices=["rules", "claude"], default="rules")

    check = sub.add_parser("check", help="summarise the Relook list and exclusions")
    check.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)

    args = p.parse_args(argv)
    if args.cmd == "run":
        files = _collect(args.input)[: args.limit]
        agent = InvoiceAgent(args.sheet, args.output, args.planner, args.dry_run, args.plans_from,
                             input_roots=[i for i in args.input if i.is_dir()])
        agent.run(files)
    elif args.cmd == "plan":
        _print_plan(args.file, args.planner)
    else:
        _check(args.sheet)


def _collect(inputs: list[Path]) -> list[Path]:
    files = []
    for item in inputs:
        if item.is_dir():
            # every file, so photos/spreadsheets show up in the report as unsupported
            files += sorted(f for f in item.rglob("*") if f.is_file() and not f.name.startswith((".", "~$")))
        elif item.is_file():
            files.append(item)
        else:
            raise SystemExit(f"not found: {item}")
    return files


def _print_plan(file: Path, planner: str) -> None:
    from . import docx_doc, pdf_doc, planner_claude
    from .planner import plan_heuristic

    handler = pdf_doc if file.suffix.lower() == ".pdf" else docx_doc
    layout = handler.extract_layout(file, with_images=planner == "claude")
    result = plan_heuristic(file.name, layout) if planner == "rules" else planner_claude.plan_with_claude(file.name, layout)
    cells = {c.id: c.text for t in layout.tables for r in t.rows for c in r.cells} | {c.id: c.text for c in layout.free_blocks}
    out = asdict(result)
    for e in out["edits"]:
        e["current_text"] = [cells.get(b, "") for b in e["block_ids"]]
    print(json.dumps(out, indent=2, ensure_ascii=False))


def _check(sheet: Path) -> None:
    rows = load_relook(sheet)
    reasons = Counter((exclusion_reason(r) or "to edit").split(":")[0] for r in rows)
    print(f"Relook list rows: {len(rows)}")
    for k, v in reasons.most_common():
        print(f"  {k:<16} {v}")
    print("Excluded companies:", ", ".join(sorted({r.name for r in rows if (exclusion_reason(r) or '').startswith('company')})))
