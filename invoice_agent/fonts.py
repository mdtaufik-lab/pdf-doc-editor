"""Choose the font for the replacement text so it matches the text it replaces.

Order: font embedded in the PDF (if it has every glyph) → the real font installed on this
machine → a metric-compatible clone in fonts/ → base-14 Times/Helvetica.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pymupdf
from fontTools import subset
from fontTools.ttLib import TTFont

from .config import FONTS_DIR

FONT_DIRS = [
    FONTS_DIR,
    Path.home() / "Library/Fonts",
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/System/Library/Fonts"),
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype"),
]

# family -> style -> candidate file names, best first (real font before its clone).
FONT_FILES = {
    "timesnewroman": {
        "regular": ["Times New Roman.ttf", "times.ttf"],
        "bold": ["Times New Roman Bold.ttf", "timesbd.ttf"],
        "italic": ["Times New Roman Italic.ttf", "timesi.ttf"],
        "bolditalic": ["Times New Roman Bold Italic.ttf", "timesbi.ttf"],
    },
    "arial": {
        "regular": ["Arial.ttf", "arial.ttf"],
        "bold": ["Arial Bold.ttf", "arialbd.ttf"],
        "italic": ["Arial Italic.ttf", "ariali.ttf"],
        "bolditalic": ["Arial Bold Italic.ttf", "arialbi.ttf"],
    },
    "calibri": {
        "regular": ["Calibri.ttf", "calibri.ttf", "Carlito-Regular.ttf"],
        "bold": ["Calibri Bold.ttf", "calibrib.ttf", "Carlito-Bold.ttf"],
        "italic": ["Calibri Italic.ttf", "calibrii.ttf", "Carlito-Italic.ttf"],
        "bolditalic": ["Calibri Bold Italic.ttf", "calibriz.ttf", "Carlito-BoldItalic.ttf"],
    },
    "cambria": {
        "regular": ["Cambria.ttf", "Caladea-Regular.ttf"],
        "bold": ["Cambria Bold.ttf", "cambriab.ttf", "Caladea-Bold.ttf"],
        "italic": ["Cambria Italic.ttf", "cambriai.ttf", "Caladea-Italic.ttf"],
        "bolditalic": ["Cambria Bold Italic.ttf", "cambriaz.ttf", "Caladea-BoldItalic.ttf"],
    },
    "georgia": {
        "regular": ["Georgia.ttf"], "bold": ["Georgia Bold.ttf"],
        "italic": ["Georgia Italic.ttf"], "bolditalic": ["Georgia Bold Italic.ttf"],
    },
    "verdana": {
        "regular": ["Verdana.ttf"], "bold": ["Verdana Bold.ttf"],
        "italic": ["Verdana Italic.ttf"], "bolditalic": ["Verdana Bold Italic.ttf"],
    },
}
FAMILY_ALIASES = {"times": "timesnewroman", "carlito": "calibri", "caladea": "cambria",
                  "helvetica": "arial", "liberationsans": "arial", "liberationserif": "timesnewroman"}
SERIF_HINTS = ("times", "cambria", "georgia", "garamond", "book", "serif", "roman", "caladea")
BASE14 = {
    True: {"regular": "tiro", "bold": "tibo", "italic": "tiit", "bolditalic": "tibi"},
    False: {"regular": "helv", "bold": "hebo", "italic": "heit", "bolditalic": "hebi"},
}


@dataclass
class ResolvedFont:
    label: str
    buffer: bytes | None = None  # font program to embed; None means base-14
    base14: str | None = None

    @property
    def is_fallback(self) -> bool:
        return self.buffer is None

    def measure(self, text: str, size: float) -> float:
        font = pymupdf.Font(fontbuffer=self.buffer) if self.buffer else pymupdf.Font(self.base14)
        return font.text_length(text, fontsize=size)


def parse_font_name(name: str, flags: int) -> tuple[str, str]:
    """'TimesNewRomanPS-BoldMT' -> ('timesnewroman', 'bold')."""
    lower = name.split("+", 1)[-1].lower()
    bold = "bold" in lower or bool(flags & pymupdf.TEXT_FONT_BOLD)
    italic = "italic" in lower or "oblique" in lower or bool(flags & pymupdf.TEXT_FONT_ITALIC)
    family = re.split(r"[-,]", lower)[0].replace(" ", "")
    family = re.sub(r"(psmt|ps|mt)$", "", family)
    family = FAMILY_ALIASES.get(family, family)
    style = ("bold" if bold else "") + ("italic" if italic else "") or "regular"
    return family, style


def resolve(doc: pymupdf.Document, page: pymupdf.Page, span: dict, text: str) -> ResolvedFont:
    embedded = _embedded_font(doc, page, span["font"])
    if embedded and _covers(embedded, text):
        return ResolvedFont(label=f"embedded {span['font']}", buffer=embedded)

    family, style = parse_font_name(span["font"], span["flags"])
    path = _find_font_file(family, style)
    if path:
        return ResolvedFont(label=path.name, buffer=_subset(str(path), text))

    serif = any(h in family for h in SERIF_HINTS) or bool(span["flags"] & pymupdf.TEXT_FONT_SERIFED)
    name = BASE14[serif][style]
    return ResolvedFont(label=f"base-14 {name} (no match for {span['font']})", base14=name)


def _embedded_font(doc: pymupdf.Document, page: pymupdf.Page, font_name: str) -> bytes | None:
    for xref, ext, _type, basefont, *_ in page.get_fonts(full=True):
        if basefont.split("+", 1)[-1] == font_name and ext not in ("n/a", ""):
            content = doc.extract_font(xref)[3]
            return content or None
    return None


def _covers(buffer: bytes, text: str) -> bool:
    font = pymupdf.Font(fontbuffer=buffer)
    return all(font.has_glyph(ord(ch)) for ch in set(text))


def _find_font_file(family: str, style: str) -> Path | None:
    for filename in FONT_FILES.get(family, {}).get(style, []):
        for d in FONT_DIRS:
            if (d / filename).is_file():
                return d / filename
    return None


@lru_cache(maxsize=64)
def _subset(path: str, text: str) -> bytes:
    """Embed only the glyphs we need so the output file stays close to the original size."""
    font = TTFont(path)
    options = subset.Options()
    options.layout_features = []
    options.notdef_outline = True
    options.name_IDs = ["*"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()
