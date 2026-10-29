"""Format-independent data structures shared by the extractors, planners and editors."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Cell:
    """A piece of text the planner can choose: a table cell, a PDF text line or a paragraph.

    x0/x1 give the horizontal extent used to line cells up with header columns (PDF points
    for PDFs, grid-column units for Word tables).
    """
    id: str
    text: str
    x0: float = 0.0
    x1: float = 0.0


@dataclass
class Row:
    cells: list[Cell]

    @property
    def text(self) -> str:
        return " | ".join(c.text for c in self.cells if c.text.strip())


@dataclass
class Table:
    id: str
    page: int
    x0: float
    x1: float
    rows: list[Row]
    page_flow: bool = True  # PDF tables can continue on the next page without a header row


@dataclass
class Layout:
    tables: list[Table]
    free_blocks: list[Cell] = field(default_factory=list)  # text outside tables
    page_images: list[bytes] = field(default_factory=list)  # PNGs, PDF only (Claude planner)
    full_text: str = ""


@dataclass
class EditGroup:
    """Blocks replaced together by a single occurrence of the replacement text."""
    block_ids: list[str]
    reason: str = ""


@dataclass
class Plan:
    file: str
    planner: str
    edits: list[EditGroup] = field(default_factory=list)
    kept_rows: list[str] = field(default_factory=list)  # arithmetic rows left unchanged
    notes: list[str] = field(default_factory=list)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path) -> "Plan":
        data = json.loads(path.read_text())
        data["edits"] = [EditGroup(**e) for e in data.get("edits", [])]
        return cls(**data)


@dataclass
class ApplyResult:
    fonts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    previews: list[Path] = field(default_factory=list)


class VerificationError(Exception):
    """The edited document differs from the original outside the planned edits."""
