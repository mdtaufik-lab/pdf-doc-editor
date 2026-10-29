"""PDF invoices: extract the layout, replace description text in place, verify, preview."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from . import fonts
from .models import ApplyResult, Cell, Layout, Plan, Row, Table, VerificationError

VERIFY_DPI = 100
PREVIEW_DPI = 90


@dataclass
class Line:
    spans: list[dict]

    @property
    def bbox(self) -> pymupdf.Rect:
        r = pymupdf.Rect(self.spans[0]["bbox"])
        for s in self.spans[1:]:
            r |= s["bbox"]
        return r

    @property
    def baseline(self) -> float:
        return self.spans[0]["origin"][1]

    @property
    def text(self) -> str:
        return "".join(s["text"] for s in self.spans).strip()


@dataclass
class Block:
    """Geometry behind one plannable Cell id."""
    page: int
    rect: pymupdf.Rect
    lines: list[Line]
    row_peers: list[str] = field(default_factory=list)


@dataclass
class Job:
    page: int
    clear_rects: list[pymupdf.Rect]
    masks: list[pymupdf.Rect]  # edited cells: words inside them must read the replacement text
    removed: list[pymupdf.Rect]  # removed lines, which can overhang their cell border
    point: pymupdf.Point
    size: float
    color: tuple[float, float, float]
    font: fonts.ResolvedFont


def extract_layout(path: Path, with_images: bool = False) -> Layout:
    with pymupdf.open(path) as doc:
        layout, _ = _extract(doc)
        if with_images:
            layout.page_images = [p.get_pixmap(dpi=80).tobytes("png") for p in doc]
    return layout


def apply_plan(src: Path, plan: Plan, dst: Path, text: str, preview_dir: Path | None) -> ApplyResult:
    result = ApplyResult()
    doc = pymupdf.open(src)
    _, blocks = _extract(doc)
    before = _snapshot(doc)

    jobs: list[Job] = []
    for group in plan.edits:
        unknown = [b for b in group.block_ids if b not in blocks]
        if unknown:
            raise VerificationError(f"plan refers to unknown blocks {unknown}")
        members = [blocks[b] for b in group.block_ids]
        if len({m.page for m in members}) != 1:
            raise VerificationError(f"edit group spans several pages: {group.block_ids}")
        if any(m.lines for m in members):
            jobs.append(_prepare(doc, members, blocks, text, result))

    font_keys: dict[str, str] = {}
    for page in doc:
        page_jobs = [j for j in jobs if j.page == page.number]
        if not page_jobs:
            continue
        for job in page_jobs:
            for r in job.clear_rects:
                page.add_redact_annot(r, fill=False)
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE
        )
        inserted: set[str] = set()
        for job in page_jobs:
            fontname = job.font.base14
            if job.font.buffer:
                fontname = font_keys.setdefault(job.font.label, f"CF{len(font_keys)}")
                if fontname not in inserted:
                    page.insert_font(fontname=fontname, fontbuffer=job.font.buffer)
                    inserted.add(fontname)
            page.insert_text(job.point, text, fontname=fontname, fontsize=job.size, color=job.color)

    out = doc.tobytes(garbage=3, deflate=True, use_objstms=True)
    with pymupdf.open("pdf", out) as edited:
        _verify(before, _snapshot(edited), jobs, text)
    doc.close()
    dst.write_bytes(out)
    if preview_dir:
        result.previews = render_compare(src, dst, preview_dir / src.stem)
    return result


# --- extraction ---------------------------------------------------------------------------

def _extract(doc: pymupdf.Document) -> tuple[Layout, dict[str, Block]]:
    tables: list[Table] = []
    free: list[Cell] = []
    blocks: dict[str, Block] = {}
    texts = []
    for page in doc:
        texts.append(page.get_text())
        spans = []  # (key of the PyMuPDF line it belongs to, span)
        for bi, b in enumerate(page.get_text("dict")["blocks"]):
            for li, line in enumerate(b.get("lines", [])):
                spans += [((bi, li), s) for s in line["spans"] if s["text"].strip()]
        claimed: set[int] = set()

        for ti, tab in enumerate(page.find_tables(use_layout=False).tables):
            rows = []
            for ri, trow in enumerate(tab.rows):
                cells = []
                for ci, bbox in enumerate(trow.cells):
                    if bbox is None:
                        continue
                    rect = pymupdf.Rect(bbox)
                    mine = [i for i, (_, s) in enumerate(spans) if i not in claimed and _inside(s, rect)]
                    claimed.update(mine)
                    lines = _lines_by_baseline([spans[i][1] for i in mine])
                    cid = f"p{page.number}-t{ti}-r{ri}-c{ci}"
                    blocks[cid] = Block(page.number, rect, lines)
                    cells.append(Cell(cid, "\n".join(l.text for l in lines), rect.x0, rect.x1))
                for c in cells:
                    blocks[c.id].row_peers = [o.id for o in cells if o.id != c.id]
                rows.append(Row(cells))
            tables.append(Table(f"p{page.number}-t{ti}", page.number, tab.bbox[0], tab.bbox[2], rows))

        by_line: dict[tuple, list[dict]] = {}
        for i, (key, s) in enumerate(spans):
            if i not in claimed:
                by_line.setdefault(key, []).append(s)
        for li, line_spans in enumerate(by_line.values()):
            line = Line(line_spans)
            lid = f"p{page.number}-l{li}"
            blocks[lid] = Block(page.number, line.bbox, [line])
            free.append(Cell(lid, line.text, line.bbox.x0, line.bbox.x1))
    return Layout(tables, free, full_text="\n".join(texts)), blocks


def _inside(span: dict, rect: pymupdf.Rect) -> bool:
    b = pymupdf.Rect(span["bbox"])
    cx, cy = (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2
    return rect.x0 - 1 <= cx <= rect.x1 + 1 and rect.y0 - 1 <= cy <= rect.y1 + 1


def _lines_by_baseline(spans: list[dict]) -> list[Line]:
    lines: list[Line] = []
    for s in sorted(spans, key=lambda s: (round(s["origin"][1], 1), s["origin"][0])):
        if lines and abs(lines[-1].baseline - s["origin"][1]) < 1.5:
            lines[-1].spans.append(s)
        else:
            lines.append(Line([s]))
    for line in lines:
        line.spans.sort(key=lambda s: s["origin"][0])
    return lines


# --- placement ----------------------------------------------------------------------------

def _prepare(doc, members: list[Block], blocks: dict[str, Block], text: str, result: ApplyResult) -> Job:
    first = next(m for m in members if m.lines)
    page = doc[first.page]
    span = max(first.lines[0].spans, key=lambda s: len(s["text"].strip()))
    font = fonts.resolve(doc, page, span, text)
    if font.label not in result.fonts:
        result.fonts.append(font.label)
    if font.is_fallback:
        result.warnings.append(f"font fallback: {font.label}")

    size = span["size"]
    width = font.measure(text, size)
    room = first.rect.width - 4
    if first.row_peers and width > room:  # table cell too narrow: shrink to fit
        result.warnings.append(f"shrunk text from {size:.1f}pt to fit a {first.rect.width:.0f}pt cell")
        size *= room / width
        width = font.measure(text, size)

    align, anchor_x = _alignment(first)
    x = {"left": anchor_x, "center": anchor_x - width / 2, "right": anchor_x - width}[align]
    rgb = span["color"]
    color = ((rgb >> 16 & 255) / 255, (rgb >> 8 & 255) / 255, (rgb & 255) / 255)

    clear, removed = [], []
    for m in members:
        for line in m.lines:
            b = line.bbox
            pad = b.height * 0.2
            clear.append(pymupdf.Rect(b.x0 - 0.5, b.y0 + pad, b.x1 + 0.5, b.y1 - pad))
            removed.append(b)
    return Job(first.page, clear, [m.rect for m in members], removed,
               pymupdf.Point(x, _baseline(first, blocks)), size, color, font)


def _alignment(block: Block) -> tuple[str, float]:
    """Detect left/center/right alignment; return it with the x anchor to place text at."""
    boxes = [l.bbox for l in block.lines]
    lefts = [b.x0 for b in boxes]
    rights = [b.x1 for b in boxes]
    centers = [(b.x0 + b.x1) / 2 for b in boxes]
    mean_center = sum(centers) / len(centers)
    if len(boxes) > 1:
        if max(lefts) - min(lefts) <= 1.5:
            return "left", min(lefts)
        # centred lines: centres agree far better than left edges (trailing spaces blur them)
        if max(centers) - min(centers) <= max(4, 0.25 * (max(lefts) - min(lefts))):
            return "center", mean_center
        if max(rights) - min(rights) <= 1.5:
            return "right", max(rights)
        return "left", min(lefts)
    left_gap, right_gap = lefts[0] - block.rect.x0, block.rect.x1 - rights[0]
    if left_gap > 8 and abs(left_gap - right_gap) <= 3:
        return "center", mean_center
    if right_gap < left_gap and right_gap <= 8:
        return "right", rights[0]
    return "left", lefts[0]


def _baseline(block: Block, blocks: dict[str, Block]) -> float:
    """Top line's baseline, unless the row's amount sits lower inside the block (centred rows)."""
    first, last = block.lines[0].baseline, block.lines[-1].baseline
    peers = [blocks[p] for p in block.row_peers if blocks[p].lines]
    if peers and len(block.lines) > 1:
        anchor = max(peers, key=lambda b: b.rect.x0).lines[0].baseline  # amount column
        if first + 2 < anchor <= last + 0.5:
            return anchor
    return first


# --- verification -------------------------------------------------------------------------

def _snapshot(doc: pymupdf.Document) -> list[dict]:
    return [
        {
            "rect": tuple(page.rect),
            "words": page.get_text("words"),
            "drawings": len(page.get_drawings()),
            # images actually drawn: rewriting the page drops image resources nothing draws
            "images": sorted(tuple(round(v) for v in i["bbox"]) for i in page.get_image_info()),
            "pix": page.get_pixmap(dpi=VERIFY_DPI),
        }
        for page in doc
    ]


def _verify(before: list[dict], after: list[dict], jobs: list[Job], text: str) -> None:
    if len(before) != len(after):
        raise VerificationError(f"page count changed {len(before)} -> {len(after)}")
    for pno, (b, a) in enumerate(zip(before, after)):
        page_jobs = [j for j in jobs if j.page == pno]
        masks = [r for j in page_jobs for r in j.masks]
        if b["rect"] != a["rect"]:
            raise VerificationError(f"page {pno + 1} size changed")
        if (b["drawings"], b["images"]) != (a["drawings"], a["images"]):
            raise VerificationError(f"page {pno + 1}: drawings/images changed "
                                    f"{b['drawings']}/{len(b['images'])} -> {a['drawings']}/{len(a['images'])}")

        def outside(words):
            return Counter((w[4], round(w[0]), round(w[1])) for w in words if not _in_any(w, masks))

        lost = outside(b["words"]) - outside(a["words"])
        added = outside(a["words"]) - outside(b["words"])
        if lost or added:
            raise VerificationError(f"page {pno + 1}: text outside the edits changed; "
                                    f"lost={list(lost)[:5]} added={list(added)[:5]}")
        for job in page_jobs:
            got = " ".join(w[4] for w in a["words"] if _in_any(w, job.masks))
            if got != text:
                raise VerificationError(f"page {pno + 1}: edited cell reads {got!r}, expected {text!r}")

        diff = _pixel_diff(b["pix"], a["pix"], masks, [r for j in page_jobs for r in j.removed])
        if diff:
            raise VerificationError(f"page {pno + 1}: {diff} pixels changed outside the edited cells")


def _in_any(word, rects: list[pymupdf.Rect]) -> bool:
    cx, cy = (word[0] + word[2]) / 2, (word[1] + word[3]) / 2
    return any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in rects)


def _pixel_diff(pa: pymupdf.Pixmap, pb: pymupdf.Pixmap, cells: list[pymupdf.Rect],
                removed: list[pymupdf.Rect]) -> int:
    scale = VERIFY_DPI / 72
    inner = [r + (1.5, 1.5, -1.5, -1.5) for r in cells]  # keep cell borders in the comparison
    for r in inner + removed:
        irect = pymupdf.IRect((r * scale).round())
        for pix in (pa, pb):
            pix.set_rect(irect, (255,) * pix.n)
    if pa.samples == pb.samples:
        return 0
    n = pa.n
    sa, sb = pa.samples, pb.samples
    return sum(1 for i in range(0, len(sa), n) if sa[i:i + n] != sb[i:i + n])


# --- previews -----------------------------------------------------------------------------

def render_compare(before: Path, after: Path, out_prefix: Path) -> list[Path]:
    """Side-by-side BEFORE | AFTER PNG per page."""
    doc_before, doc_after = pymupdf.open(before), pymupdf.open(after)
    paths = []
    gap = 24
    for i in range(len(doc_before)):
        r = doc_before[i].rect
        sheet = pymupdf.open()
        page = sheet.new_page(width=2 * r.width + gap, height=r.height + 20)
        page.show_pdf_page(pymupdf.Rect(0, 20, r.width, r.height + 20), doc_before, i)
        page.show_pdf_page(pymupdf.Rect(r.width + gap, 20, 2 * r.width + gap, r.height + 20), doc_after, i)
        page.draw_line((r.width + gap / 2, 0), (r.width + gap / 2, r.height + 20), color=(0.6, 0.6, 0.6))
        page.insert_text((8, 14), "BEFORE", fontsize=10, color=(0.7, 0, 0))
        page.insert_text((r.width + gap + 8, 14), "AFTER", fontsize=10, color=(0, 0.5, 0))
        path = out_prefix.parent / f"{out_prefix.name}_p{i + 1}.png"
        page.get_pixmap(dpi=PREVIEW_DPI).save(path)
        paths.append(path)
    doc_before.close()
    doc_after.close()
    return paths
