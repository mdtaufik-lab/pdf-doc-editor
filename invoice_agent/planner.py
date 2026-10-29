"""Rule-based planner: decide which table cells hold line-item descriptions.

Works on the format-independent Layout, so PDF and Word invoices share the same rules
(see "What counts as the description" in CLAUDE.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import (
    AMOUNT_HEADER_RE, ARITHMETIC_ANYWHERE_RE, ARITHMETIC_LABEL_RE, DESCRIPTION_HEADER_RE,
    REPLACEMENT_TEXT,
)
from .models import Cell, EditGroup, Layout, Plan, Row, Table


@dataclass
class Header:
    desc_cols: list[tuple[float, float]]
    amount_col: tuple[float, float] | None
    extent: tuple[float, float]  # table x-range, to recognise continuation tables


def plan_heuristic(file: str, layout: Layout) -> Plan:
    plan = Plan(file=file, planner="rules")
    header: Header | None = None
    for table in layout.tables:
        start = 0
        idx = _find_header_row(table)
        if idx is not None:
            header = _header(table, table.rows[idx])
            start = idx + 1
        elif header is None or not table.page_flow or not _same_extent((table.x0, table.x1), header.extent):
            continue  # not an item table (or no header seen yet)
        for row in table.rows[start:]:
            _classify_row(row, header, plan)
    if not plan.edits and header is None:
        plan.notes.append("no item table with a description column found")
    return plan


def _find_header_row(table: Table) -> int | None:
    for i, row in enumerate(table.rows[:4]):
        texts = [c.text for c in row.cells]
        has_desc = any(_is_desc_header(t) for t in texts)
        has_amount = any(AMOUNT_HEADER_RE.search(t) and not _is_desc_header(t) for t in texts)
        no_digits = not re.search(r"\d", row.text)
        if has_desc and (has_amount or no_digits):
            return i
    return None


def _is_desc_header(text: str) -> bool:
    return bool(text) and len(text) <= 60 and bool(DESCRIPTION_HEADER_RE.search(text))


def _header(table: Table, row: Row) -> Header:
    desc = [(c.x0, c.x1) for c in row.cells if _is_desc_header(c.text)]
    amount = next(((c.x0, c.x1) for c in row.cells
                   if AMOUNT_HEADER_RE.search(c.text) and not _is_desc_header(c.text)), None)
    return Header(desc, amount, (table.x0, table.x1))


def _classify_row(row: Row, header: Header, plan: Plan) -> None:
    desc_cells = [c for c in row.cells if c.text.strip() and any(_same_column(c, col) for col in header.desc_cols)]
    if not desc_cells:
        return
    amount_cells = [c for c in row.cells if c not in desc_cells and header.amount_col and _overlaps(c, header.amount_col)]
    label_text = " ".join(c.text for c in desc_cells)
    first_text = next((c.text for c in row.cells if c.text.strip() and not re.fullmatch(r"[\W\d.]*", c.text)), "")

    if (ARITHMETIC_LABEL_RE.match(first_text) or any(ARITHMETIC_LABEL_RE.match(c.text) for c in desc_cells)
            or ARITHMETIC_ANYWHERE_RE.search(label_text)):
        plan.kept_rows.append(_one_line(row.text))
        return
    if all(c.text.strip() == REPLACEMENT_TEXT for c in desc_cells):
        return  # already done
    if header.amount_col and not any(re.search(r"\d", c.text) for c in amount_cells):
        plan.notes.append(f"row without an amount left unchanged: {_one_line(row.text)}")
        return
    for c in desc_cells:
        plan.edits.append(EditGroup([c.id], reason=_one_line(c.text)[:120]))


def _same_column(cell: Cell, col: tuple[float, float]) -> bool:
    overlap = min(cell.x1, col[1]) - max(cell.x0, col[0])
    return overlap > 0 and overlap / max(cell.x1 - cell.x0, col[1] - col[0]) >= 0.8


def _overlaps(cell: Cell, col: tuple[float, float]) -> bool:
    overlap = min(cell.x1, col[1]) - max(cell.x0, col[0])
    return overlap > 0.5 * (col[1] - col[0])


def _same_extent(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= 6 and abs(a[1] - b[1]) <= 6


def _one_line(text: str) -> str:
    return " ".join(text.split())
