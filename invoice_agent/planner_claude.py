"""Optional planner that asks Claude which blocks are line-item descriptions.

Used for invoices the rule-based planner can't read (no recognisable item table, free-text
descriptions). Needs the `anthropic` package and an API key (ANTHROPIC_API_KEY or `ant auth login`).
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from .config import CLAUDE_EFFORT, CLAUDE_MODEL, REPLACEMENT_TEXT
from .models import EditGroup, Layout, Plan

SYSTEM_PROMPT = f"""You plan edits to contractor invoices. Every line-item description must be
replaced with the text "{REPLACEMENT_TEXT}"; nothing else on the invoice may change.

You receive the invoice as text blocks with ids (table cells and free text lines/paragraphs),
plus page images when available. Choose the blocks that hold the description of a billable line
item (the work/service being charged for). Rules:
- One edit group = the blocks that together form ONE description in ONE place (e.g. all lines of
  one description cell, or consecutive lines of one free-text description). The first block of a
  group receives "{REPLACEMENT_TEXT}"; the other blocks in the group are blanked.
- If a billable row has two descriptive columns (e.g. Services and Description), make one group
  per column.
- Never select: invoice number, dates, period, names, addresses, GST/PAN, phone, amounts,
  amount in words, bank details, signatures, table headings, or arithmetic rows (Total,
  Sub-total, TDS, GST/IGST/CGST/SGST, Less/Deduction, Net/Total Payable). List arithmetic rows
  you deliberately left alone in kept_rows.
- If you cannot tell what the description is, return no edits and explain in notes."""

SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["block_ids", "reason"],
                "additionalProperties": False,
            },
        },
        "kept_rows": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["edits", "kept_rows", "notes"],
    "additionalProperties": False,
}


class ClaudePlannerError(Exception):
    pass


def available() -> bool:
    """True when the SDK is installed and some credential source is configured."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or (Path.home() / ".config" / "anthropic").exists())


def plan_with_claude(file: str, layout: Layout) -> Plan:
    import anthropic

    blocks = {
        "tables": [
            {"id": t.id, "page": t.page,
             "rows": [[{"id": c.id, "text": c.text} for c in r.cells] for r in t.rows]}
            for t in layout.tables
        ],
        "free_text": [{"id": c.id, "text": c.text} for c in layout.free_blocks],
    }
    content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": base64.b64encode(png).decode()}}
        for png in layout.page_images
    ]
    content.append({"type": "text", "text": f"Invoice file: {file}\n\nBlocks:\n{json.dumps(blocks, ensure_ascii=False)}"})

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": CLAUDE_EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
        # Server-side fallback: if a request is declined, Anthropic re-runs it on a fallback model.
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
        extra_body={"fallbacks": "default"},
    )
    if response.stop_reason == "refusal":
        raise ClaudePlannerError("Claude declined to plan this invoice")
    if response.stop_reason == "max_tokens":
        raise ClaudePlannerError("Claude's plan was cut off (max_tokens)")
    data = json.loads(next(b.text for b in response.content if b.type == "text"))

    known = {c.id for t in layout.tables for r in t.rows for c in r.cells} | {c.id for c in layout.free_blocks}
    plan = Plan(file=file, planner=f"claude:{CLAUDE_MODEL}", kept_rows=data["kept_rows"])
    for e in data["edits"]:
        ids = [i for i in e["block_ids"] if i in known]
        if ids:
            plan.edits.append(EditGroup(ids, reason=e["reason"]))
        if len(ids) != len(e["block_ids"]):
            plan.notes.append(f"dropped unknown block ids from Claude: {set(e['block_ids']) - known}")
    if data["notes"]:
        plan.notes.append(data["notes"])
    return plan
