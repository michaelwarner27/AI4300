"""
Page layout analysis for PDF ingestion (PyMuPDF based).

Three pieces:

- TextRepair: some PDFs store text in fonts whose character codes are shifted
  by a fixed offset, so extractors return e.g. "7+(50267$7" for "THERMOSTAT".
  Each font on each page is tested; shifted ones are decoded.
- DocumentLayout: styled text lines (size, bold, position) in column-aware
  reading order, with diagram positions inserted as marker lines. This is what
  lets us ask "which heading comes before this figure?".
- FontHeadingDetector: finds section headings from font size, bold, position
  and repetition, and assigns heading levels by style.

analyze_document() ties them together and annotates diagrams with their
heading path, nearby figure label and preceding context text.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from src.data_models import DiagramMetadata, DocumentSection, SectionHeading, TableData

Rect = tuple[float, float, float, float]

# --------------------------------------------------------------------------
# Shifted-font text repair
# --------------------------------------------------------------------------
SHIFT = 29                       # observed offset: "(" (0x28) -> "E" (0x45)
_SHIFTABLE_MAX = 0x7E - SHIFT    # highest code that still decodes to printable ASCII
_MIN_SAMPLE_CHARS = 12           # need this much text to judge a font on one page
_MIN_SAMPLE_TOKENS = 3           # ...and this many word-ish tokens

_VOWELS = set("aeiouyAEIOUY")
_WORD = re.compile(r"^[A-Za-z]+(?:[-'/&.][A-Za-z]+)*\.?$")
_NUMBER = re.compile(r"^[0-9][0-9.,:/%x-]*$")
_ALNUM = re.compile(r"^[A-Za-z0-9]+(?:[-/.][A-Za-z0-9]+)*$")
_STRIP = ".,;:!?()[]{}\"'"


def shift_decode(text: str) -> str:
    """Undo a +29 character-code shift. Whitespace is left untouched."""
    return "".join(
        chr(ord(c) + SHIFT) if 3 <= ord(c) <= _SHIFTABLE_MAX and not c.isspace() else c
        for c in text
    )


def _counted_tokens(text: str) -> int:
    """Tokens that text_quality() scores (not punctuation-only)."""
    return sum(
        1 for token in text.split()
        if any(ch.isalnum() or ord(ch) < 32 for ch in token.strip(_STRIP))
    )


def text_quality(text: str) -> float:
    """
    Share of tokens that look like real words, numbers or model codes (0..1).

    Punctuation-only tokens are ignored. Tokens with control characters count
    as bad. A word needs a vowel unless it is short (abbreviations like "BLK").
    """
    good = counted = 0
    for token in text.split():
        core = token.strip(_STRIP)
        if not core or not any(ch.isalnum() or ord(ch) < 32 for ch in core):
            continue
        counted += 1
        if any(ord(ch) < 32 for ch in core):
            continue
        if _WORD.match(core):
            letters = [ch for ch in core if ch.isalpha()]
            if len(letters) <= 4 or any(ch in _VOWELS for ch in letters):
                good += 1
        elif _NUMBER.match(core):
            good += 1
        elif _ALNUM.match(core) and any(ch.isalpha() for ch in core) and any(ch.isdigit() for ch in core):
            good += 1
    return good / counted if counted else 0.0


def looks_shifted(text: str) -> bool:
    """True when decoding the text makes it clearly more word-like."""
    if sum(1 for ch in text if not ch.isspace()) < _MIN_SAMPLE_CHARS:
        return False
    if _counted_tokens(text) < _MIN_SAMPLE_TOKENS:
        return False
    decoded = shift_decode(text)
    if decoded == text:
        return False
    before, after = text_quality(text), text_quality(decoded)
    return before <= 0.5 and after >= 0.7 and after - before >= 0.3


def looks_garbled(text: str) -> bool:
    """True for text that is mostly not word-like (used to drop bad captions)."""
    return bool(text.strip()) and text_quality(text) < 0.4


def _iter_spans(page_dict: dict) -> Iterable[dict]:
    for block in page_dict.get("blocks", []):
        for line in block.get("lines", []):
            yield from line.get("spans", [])


class OverprintFilter:
    """
    Spots characters drawn again at (almost) the same spot.

    Some PDFs fake bold text by drawing it several times with tiny offsets;
    extractors then return every copy ("WARNING ARNING ARNING").
    """

    TOLERANCE = 1.2  # pt

    def __init__(self) -> None:
        self._seen: dict[tuple[int, int], list[tuple[str, float, float]]] = defaultdict(list)

    def is_copy(self, c: str, box: Rect) -> bool:
        """True if c was already drawn here; otherwise remember it and return False."""
        gx, gy = int(box[0]), int(box[1])
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for c2, x2, y2 in self._seen.get((gx + dx, gy + dy), ()):
                    if c2 == c and abs(x2 - box[0]) < self.TOLERANCE and abs(y2 - box[1]) < self.TOLERANCE:
                        return True
        self._seen[(gx, gy)].append((c, box[0], box[1]))
        return False


def _span_raw_text(span: dict) -> str:
    if "text" in span:
        return span["text"]
    return "".join(ch["c"] for ch in span.get("chars", []))


class TextRepair:
    """
    Detects and decodes fonts whose text is shifted by a fixed code offset.

    Decisions are made per (page, font): a font name can be fine on one page
    and shifted on another. Pages with too little text in a font fall back to
    the decision for that font across the whole document.
    """

    def __init__(self, mu_doc: Any) -> None:
        self.shifted: set[tuple[int, str]] = set()
        self._page_tokens: dict[int, set[str]] = defaultdict(set)

        per_font: dict[str, list[str]] = defaultdict(list)
        per_page_font: dict[tuple[int, str], list[str]] = defaultdict(list)
        for pno, page in enumerate(mu_doc):
            for span in _iter_spans(page.get_text("dict")):
                text = _span_raw_text(span)
                per_font[span["font"]].append(text)
                per_page_font[(pno, span["font"])].append(text)

        doc_level = {font for font, texts in per_font.items() if looks_shifted(" ".join(texts))}
        for (pno, font), texts in per_page_font.items():
            joined = " ".join(texts)
            enough = (
                sum(1 for ch in joined if not ch.isspace()) >= _MIN_SAMPLE_CHARS
                and _counted_tokens(joined) >= _MIN_SAMPLE_TOKENS
            )
            if looks_shifted(joined) or (not enough and font in doc_level):
                self.shifted.add((pno, font))
                self._page_tokens[pno].update(joined.split())

    @property
    def active(self) -> bool:
        return bool(self.shifted)

    def is_shifted(self, page: int, font: str) -> bool:
        return (page, font) in self.shifted

    def span_text(self, page: int, span: dict) -> str:
        text = _span_raw_text(span)
        return shift_decode(text) if self.is_shifted(page, span["font"]) else text

    def clip_text(self, page_obj: Any, page: int, clip: Any = None) -> str:
        """
        Text inside a region: shifted fonts decoded, overprint copies dropped,
        whitespace normalized.
        """
        overprint = OverprintFilter()
        lines: list[str] = []
        for block in page_obj.get_text("rawdict", clip=clip).get("blocks", []):
            for line in block.get("lines", []):
                chars: list[str] = []
                for span in line.get("spans", []):
                    shifted = self.is_shifted(page, span["font"])
                    for ch in span.get("chars", []):
                        c = shift_decode(ch["c"]) if shifted else ch["c"]
                        if not c.isspace() and overprint.is_copy(c, tuple(ch["bbox"])):
                            continue
                        chars.append(c)
                text = "".join(chars).strip()
                if text:
                    lines.append(text)
        return " ".join(" ".join(lines).split())

    def fix_plain_text(self, page: int, text: str) -> str:
        """
        Decode shifted lines inside plain text from another extractor (pypdf).

        Plain text has no font information, so a line is decoded when every
        token in it was seen in a shifted font on this page.
        """
        tokens = self._page_tokens.get(page)
        if not tokens:
            return text
        fixed = []
        for line in text.split("\n"):
            line_tokens = line.split()
            if line_tokens and all(tok in tokens for tok in line_tokens):
                fixed.append(shift_decode(line))
            else:
                fixed.append(line)
        return "\n".join(fixed)


# --------------------------------------------------------------------------
# Layout lines and reading order
# --------------------------------------------------------------------------
@dataclass
class LayoutLine:
    """One visual line of text (or a marker for a diagram position)."""

    page: int  # 0-indexed
    text: str
    size: float = 0.0
    bold: bool = False
    bbox: Rect = (0.0, 0.0, 0.0, 0.0)
    block: int = -1  # block id, unique within the document
    style_run: int = 1  # consecutive lines in the block sharing this line's style
    in_table: bool = False
    in_figure: int | None = None  # index of the diagram whose region holds this line
    figure_index: int | None = None  # set on marker entries that stand for a diagram
    table_index: int | None = None  # set on marker entries that stand for a table
    heading_level: int = 0  # 0 = not a heading
    heading_part: bool = False  # continuation line of a heading that wraps
    bullet: bool = False  # the line is only a bullet glyph (its item is a separate line)

    @property
    def style(self) -> tuple[float, bool]:
        return (round(self.size * 2) / 2, self.bold)


@dataclass
class _Block:
    bbox: Rect
    lines: list[LayoutLine] = field(default_factory=list)
    is_figure: bool = False

    @property
    def max_size(self) -> float:
        return max((ln.size for ln in self.lines), default=0.0)


_LABEL_ONLY = re.compile(r"^(?:[A-Z]|\d{1,2}|[IVX]{1,4})[.)]$")
_FIGURE_LABEL = re.compile(r"^(?:fig(?:ure)?\.?\s*\d+|diagram\b|table\s*\d+)", re.IGNORECASE)
_BULLET_GLYPHS = set("•▪▫◦‣∙●○■□►▸➢➤")
BULLET = "-"  # what every bullet glyph is written as


def _is_bullet_char(c: str) -> bool:
    """A list bullet: a common bullet glyph, or a private-use font symbol (e.g. Wingdings U+F097)."""
    return c in _BULLET_GLYPHS or 0xE000 <= ord(c) <= 0xF8FF


def _normalize_bullets(text: str) -> tuple[str, bool]:
    """
    Write a leading bullet glyph as "- ".

    Returns (text, bullet_only); bullet_only is True when the line is nothing
    but bullet glyphs (its item text is a separate line to the right).
    """
    stripped = text.strip()
    if stripped and all(_is_bullet_char(c) or c.isspace() for c in stripped):
        return BULLET, True
    i = 0
    while i < len(stripped) and (_is_bullet_char(stripped[i]) or stripped[i].isspace()):
        i += 1
    if i and any(_is_bullet_char(c) for c in stripped[:i]):
        return f"{BULLET} {stripped[i:]}", False
    return text, False


def _center_in(bbox: Rect, rect: Rect) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3]


def _union(a: Rect, b: Rect) -> Rect:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _respace_letter_spaced(chars: list[tuple[str, Rect]], size: float) -> str | None:
    """
    Rebuild word breaks for letter-spaced text ("S E R V I C E  M A N U A L").

    Inter-letter gaps are roughly constant; a word break is a gap clearly
    larger than the typical one. Returns None when the line isn't letter-spaced.
    """
    visible = [(c, b) for c, b in chars if not c.isspace()]
    if len(visible) < 4:
        return None
    gaps = [visible[i + 1][1][0] - visible[i][1][2] for i in range(len(visible) - 1)]
    typical = statistics.median(gaps)
    threshold = typical + 0.15 * size
    out = [visible[0][0]]
    for (c, _), gap in zip(visible[1:], gaps):
        if gap > threshold:
            out.append(" ")
        out.append(c)
    return "".join(out)


def _is_letter_spaced(text: str) -> bool:
    tokens = text.split()
    return len(tokens) >= 4 and sum(1 for t in tokens if len(t) <= 2) / len(tokens) >= 0.6


class DocumentLayout:
    """
    All text lines of a document in reading order, with diagram markers.

    Reading order per page: blocks are sorted top to bottom and split into
    bands. A band ends at any block that spans both columns, and at any
    large-font block (a section heading), so a heading always comes before
    the text and figures below it. Inside a band, the left column is read
    before the right column.
    """

    COLUMN_TOLERANCE = 0.02   # share of page width a column block may cross the middle
    SECTION_BREAK_RATIO = 1.3  # blocks this much larger than body text split bands

    def __init__(
        self,
        mu_doc: Any,
        repair: TextRepair | None = None,
        figure_rects: dict[int, list[tuple[int, Rect | None]]] | None = None,
        table_rects: dict[int, list[tuple[int, Rect]]] | None = None,
    ) -> None:
        """
        Args:
            mu_doc: open PyMuPDF document
            repair: shared TextRepair (built from mu_doc when omitted)
            figure_rects: page -> [(diagram index, rect or None)]; each gets a marker line
            table_rects: page -> [(table index, rect)]; each gets a marker line, and
                lines inside the rect are flagged in_table
        """
        self.repair = repair if repair is not None else TextRepair(mu_doc)
        self.page_count = len(mu_doc)
        self.lines: list[LayoutLine] = []
        figure_rects = figure_rects or {}
        table_rects = table_rects or {}

        pages: list[tuple[Any, list[_Block]]] = []
        block_id = 0
        for pno, page in enumerate(mu_doc):
            blocks, block_id = self._page_blocks(
                page, pno, figure_rects.get(pno, []), table_rects.get(pno, []), block_id
            )
            pages.append((page.rect, blocks))

        self.body_size = body_font_size(ln for _, blocks in pages for b in blocks for ln in b.lines)
        for page_rect, blocks in pages:
            for block in self._reading_order(blocks, page_rect):
                self.lines.extend(block.lines)

    # -- building blocks ---------------------------------------------------
    def _page_blocks(
        self,
        page: Any,
        pno: int,
        figures: list[tuple[int, Rect | None]],
        tables: list[tuple[int, Rect]],
        block_id: int,
    ) -> tuple[list[_Block], int]:
        blocks: list[_Block] = []
        overprint = OverprintFilter()
        for raw in page.get_text("rawdict").get("blocks", []):
            if raw.get("type", 0) != 0:
                continue
            lines = [
                ln for ln in (self._make_line(pno, rl, overprint) for rl in raw.get("lines", [])) if ln
            ]
            if not lines:
                continue
            lines = self._merge_same_baseline(lines)
            for ln in lines:
                ln.block = block_id
            blocks.append(_Block(bbox=tuple(raw["bbox"]), lines=lines))
            block_id += 1

        self._merge_labels(blocks)
        blocks = [b for b in blocks if b.lines]
        for block in blocks:
            block.bbox = self._lines_bbox(block.lines)
            self._set_style_runs(block.lines)
            for ln in block.lines:
                ln.in_table = any(_center_in(ln.bbox, rect) for _, rect in tables)
                for index, rect in figures:
                    if (
                        rect is not None
                        and not self._is_frame(rect, page.rect)
                        and _center_in(ln.bbox, rect)
                    ):
                        ln.in_figure = index
                        break

        height, width = page.rect.height, page.rect.width
        for index, rect in figures:
            # Diagrams without a known position, and page-sized regions, are placed
            # at the bottom of the page so they pick up every heading on the page
            if rect is None or self._is_frame(rect, page.rect):
                box = (0.0, height, width, height)
            else:
                box = rect
            marker = LayoutLine(page=pno, text="", bbox=box, block=block_id, figure_index=index)
            blocks.append(_Block(bbox=box, lines=[marker], is_figure=True))
            block_id += 1
        for index, rect in tables:
            # Marker at the table's top edge (the table's own text lines are kept as text)
            box = (rect[0], rect[1], rect[2], rect[1])
            marker = LayoutLine(page=pno, text="", bbox=box, block=block_id, table_index=index)
            blocks.append(_Block(bbox=box, lines=[marker], is_figure=True))
            block_id += 1
        return blocks, block_id

    FRAME_AREA_RATIO = 0.5     # figure regions larger than this share of the page are frames

    def _is_frame(self, rect: Rect, page_rect: Any) -> bool:
        """
        Page borders, backgrounds and full-page drawings. Their text is treated
        as normal page text, not as labels inside a figure.
        """
        area = max(rect[2] - rect[0], 0) * max(rect[3] - rect[1], 0)
        page_area = page_rect.width * page_rect.height
        return page_area > 0 and area / page_area > self.FRAME_AREA_RATIO

    def _make_line(self, pno: int, raw_line: dict, overprint: OverprintFilter) -> LayoutLine | None:
        """Build a line from its characters (decoding shifted fonts, dropping overprint copies)."""
        chars: list[tuple[str, Rect]] = []
        bold_chars = total_chars = 0
        size = 0.0
        for span in raw_line.get("spans", []):
            shifted = self.repair.is_shifted(pno, span["font"])
            font = span["font"].lower()
            is_bold = bool(span["flags"] & 16) or "bold" in font or "black" in font
            for ch in span.get("chars", []):
                c = shift_decode(ch["c"]) if shifted else ch["c"]
                box = tuple(ch["bbox"])
                if not c.isspace():
                    if overprint.is_copy(c, box):
                        continue
                    total_chars += 1
                    bold_chars += is_bold
                    size = max(size, span["size"])
                chars.append((c, box))
        text = " ".join("".join(c for c, _ in chars).split())
        if not text:
            return None
        if _is_letter_spaced(text):
            text = _respace_letter_spaced(chars, size) or text
        text, bullet_only = _normalize_bullets(text)
        visible = [box for c, box in chars if not c.isspace()]
        bbox = visible[0]
        for box in visible[1:]:
            bbox = _union(bbox, box)
        return LayoutLine(
            page=pno,
            text=text,
            size=size,
            bold=total_chars > 0 and bold_chars / total_chars >= 0.5,
            bbox=bbox,
            bullet=bullet_only,
        )

    @staticmethod
    def _merge_same_baseline(lines: list[LayoutLine]) -> list[LayoutLine]:
        """
        Join pieces of one visual line within a block ("A." + "Air Filter",
        or fragments left over after overprint copies were removed).
        """
        merged: list[LayoutLine] = []
        for ln in sorted(lines, key=lambda l: (round(l.bbox[1]), l.bbox[0])):
            prev = merged[-1] if merged else None
            size = max(prev.size if prev else 0.0, ln.size, 1.0)
            if (
                prev is not None
                and abs(prev.bbox[1] - ln.bbox[1]) < 0.4 * size
                and ln.bbox[0] >= prev.bbox[2] - 0.3 * size
            ):
                gap = ln.bbox[0] - prev.bbox[2]
                joiner = " " if gap > 0.15 * size else ""
                if prev.bullet and not ln.bullet:
                    joiner = " "  # "- " + item text
                prev.text = f"{prev.text}{joiner}{ln.text}"
                prev.bbox = _union(prev.bbox, ln.bbox)
                prev.size = max(prev.size, ln.size)
                prev.bold = prev.bold and ln.bold
                prev.bullet = prev.bullet and ln.bullet
            else:
                merged.append(ln)
        return merged

    @staticmethod
    def _merge_labels(blocks: list[_Block]) -> None:
        """
        Attach a lone label ("A.", "3)", "IV.") or a lone bullet to the text
        right of it on the same line.

        Bullets are compared by vertical centre, since a bullet glyph often has
        a different size than its text. A bullet is only a list bullet when
        nothing is to its left on the same line; dots between table cells
        (e.g. availability marks in a parts list) are left as they are.
        """
        all_lines = [ln for b in blocks for ln in b.lines]

        def center(ln: LayoutLine) -> float:
            return (ln.bbox[1] + ln.bbox[3]) / 2

        def same_row(a: LayoutLine, b: LayoutLine) -> bool:
            return abs(center(a) - center(b)) < 0.5 * max(a.size, b.size, 1.0)

        for block in blocks:
            for label in list(block.lines):
                if label.bullet:
                    if any(
                        ln is not label and ln.text and same_row(ln, label) and ln.bbox[2] <= label.bbox[0] + 1
                        for ln in all_lines
                    ):
                        continue  # text to its left: a marker in a table row, not a list bullet
                    target = next(
                        (
                            ln for ln in all_lines
                            if ln is not label and ln.text and not ln.bullet
                            and same_row(ln, label)
                            and -1 <= ln.bbox[0] - label.bbox[2] <= 40
                        ),
                        None,
                    )
                    if target is None:
                        continue  # nothing beside it: leave the line as it is
                elif _LABEL_ONLY.match(label.text):
                    target = next(
                        (
                            ln for ln in all_lines
                            if ln is not label and ln.text
                            and abs(ln.bbox[1] - label.bbox[1]) < 0.4 * max(ln.size, 1.0)
                            and 0 <= ln.bbox[0] - label.bbox[2] <= 40
                        ),
                        None,
                    )
                else:
                    continue
                if target is None:
                    continue  # a label with nothing beside it stays as it is
                if not (label.bullet and target.text.startswith(BULLET + " ")):
                    target.text = f"{label.text} {target.text}"
                    target.bbox = _union(target.bbox, label.bbox)
                block.lines.remove(label)  # an extra bullet before "- item" is a duplicate
                label.text = ""

    @staticmethod
    def _set_style_runs(lines: list[LayoutLine]) -> None:
        i = 0
        while i < len(lines):
            j = i
            while j + 1 < len(lines) and lines[j + 1].style == lines[i].style:
                j += 1
            for k in range(i, j + 1):
                lines[k].style_run = j - i + 1
            i = j + 1

    @staticmethod
    def _lines_bbox(lines: list[LayoutLine]) -> Rect:
        box = lines[0].bbox
        for ln in lines[1:]:
            box = _union(box, ln.bbox)
        return box

    # -- ordering ------------------------------------------------------------
    def _reading_order(self, blocks: list[_Block], page_rect: Any) -> list[_Block]:
        width = page_rect.width
        mid = page_rect.x0 + width / 2
        tol = width * self.COLUMN_TOLERANCE
        break_size = self.body_size * self.SECTION_BREAK_RATIO if self.body_size else math.inf

        def column(b: _Block) -> int:
            if b.bbox[2] <= mid + tol:
                return 0
            if b.bbox[0] >= mid - tol:
                return 1
            return -1  # spans both columns

        ordered: list[_Block] = []
        band: list[_Block] = []

        def flush() -> None:
            band.sort(key=lambda b: (column(b), b.bbox[1], b.bbox[0]))
            ordered.extend(band)
            band.clear()

        for block in sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0])):
            is_break = column(block) == -1 or (not block.is_figure and block.max_size >= break_size)
            if is_break:
                flush()
                ordered.append(block)
            else:
                band.append(block)
        flush()
        return ordered


def body_font_size(lines: Iterable[LayoutLine]) -> float:
    """Font size covering the most characters (rounded to 0.5pt)."""
    sizes: Counter[float] = Counter()
    for ln in lines:
        if ln.text:
            sizes[round(ln.size * 2) / 2] += len(ln.text)
    return sizes.most_common(1)[0][0] if sizes else 0.0


# --------------------------------------------------------------------------
# Heading detection
# --------------------------------------------------------------------------
_TOC_TITLE = re.compile(r"^(?:table of )?contents$", re.IGNORECASE)
_DOT_LEADER = re.compile(r"(?:\.\s*){4,}\d*\s*$|(?:\s\.){3,}")
_NUMBERED = re.compile(r"^\d+(?:\.\d+)+\s+[A-Z]")
_LABEL_PREFIX = re.compile(r"^(?:[A-Z]|\d{1,2}(?:\.\d+)*|[IVX]{1,4})[.)]?\s+\S")
_STRIP_LABEL = re.compile(r"^(?:[A-Z]|\d{1,2}(?:\.\d+)*|[IVX]{1,4})[.)]\s+")
_ADMONITION = re.compile(
    r"^(?:notes?|warning|caution|danger|important|notice|attention)\W*$", re.IGNORECASE
)
# "IMPORTANT SAFETY INSTRUCTIONS" is a real section, so "important" is not a prefix here
_ADMONITION_PREFIX = re.compile(r"^\W*(?:notes?|warning|caution|danger|notice)\b", re.IGNORECASE)
_SENTENCE_BREAK = re.compile(r"[a-z]{2,}\.\s+[A-Z]")
_TRAILING_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "of", "on", "or", "that", "the", "to", "with",
}


def _normalize(text: str) -> str:
    return re.sub(r"[\d\W_]+", " ", text.lower()).strip()


def _toc_key(text: str) -> str:
    """Comparable form of a heading: "C. Condensation" and "Condensation" match."""
    return _normalize(_STRIP_LABEL.sub("", text.strip()))


class FontHeadingDetector:
    """
    Find section headings from font styling.

    A line is a candidate when it is larger than body text, or bold and
    heading-shaped (ALL CAPS, labelled like "A. ..." or short title case).
    Candidates are rejected when they repeat across many pages (running
    headers, banners), sit on a table-of-contents page, inside a table or
    figure, are long bold paragraphs, or are mostly digits (part numbers).
    Surviving styles are ranked by size to give heading levels. Styles that
    only appear on the first page are treated as the cover/title.
    """

    SIZE_RATIO = 1.15          # "larger than body text"
    MAX_CHARS = 80
    MAX_WORDS = 12
    MAX_STYLE_RUN = 3          # longer same-style runs are paragraphs, not headings
    REPEAT_PAGE_SHARE = 0.3    # text on more pages than this is a header/footer
    EMPHASIS_PER_PAGE = 4      # body-size bold styles used more often are emphasis
    TOC_MIN_ENTRIES = 3        # headings repeated later in the document that mark a TOC page
    TITLE_MIN_PAGES = 5        # documents this long get single-page title styles removed

    def detect(self, layout: DocumentLayout) -> list[SectionHeading]:
        lines = [ln for ln in layout.lines if ln.figure_index is None and ln.text]
        if not lines:
            return []
        body = layout.body_size or body_font_size(lines)
        self._body = body
        self._index = {id(ln): i for i, ln in enumerate(lines)}
        self.repeated = self._repeated_texts(lines, layout.page_count)
        self.toc_ranges: dict[int, tuple[int, int]] = {}

        candidates = self._heading_groups(lines, lambda ln: self._styled(ln, body))
        self.toc_ranges = self._toc_ranges(lines, candidates)
        candidates = [c for c in candidates if not self.in_toc(c)]
        candidates = self._drop_title_and_emphasis(candidates, body, layout.page_count)
        if candidates:
            levels = self._levels(candidates)
            level_of = lambda ln: levels[ln.style]  # noqa: E731
        else:
            # Nothing stands out by font: ALL CAPS lines are level 1, "1.2 X" is level 2, ...
            candidates = [
                c for c in self._heading_groups(lines, self._fallback) if not self.in_toc(c)
            ]
            level_of = lambda ln: (  # noqa: E731
                ln.text.split()[0].count(".") + 1 if _NUMBERED.match(ln.text) else 1
            )

        # Only continuation lines of headings that survived every filter stay marked
        for ln in lines:
            ln.heading_part = False
        for head in candidates:
            for part in self._parts.get(id(head), []):
                part.heading_part = True
        headings: list[SectionHeading] = []
        for ln in candidates:
            ln.heading_level = level_of(ln)
            headings.append(SectionHeading(ln.text, ln.heading_level, ln.page, ln.bbox))
        return headings

    @staticmethod
    def _continues(prev: LayoutLine, ln: LayoutLine) -> bool:
        """ln sits directly below prev in the same style (a wrapped line)."""
        size = max(ln.size, 1.0)
        return (
            ln.style == prev.style
            and ln.page == prev.page
            and -0.5 * size <= ln.bbox[1] - prev.bbox[3] < 0.6 * size
            and min(ln.bbox[2], prev.bbox[2]) > max(ln.bbox[0], prev.bbox[0])
        )

    def _heading_groups(self, lines: list[LayoutLine], qualifies: Any) -> list[LayoutLine]:
        """
        Group adjacent same-style lines into runs and keep the runs that are headings.

        A run is a heading only if every line in it qualifies, so one
        heading-like line inside a bold paragraph is not picked out. A line
        after one ending in "-" is a hyphenated continuation and is exempt.
        The first line of a kept run carries the joined text; the rest are
        marked heading_part.
        """
        self._parts: dict[int, list[LayoutLine]] = {}
        runs: list[list[LayoutLine]] = []
        for ln in lines:
            if runs and self._continues(runs[-1][-1], ln):
                runs[-1].append(ln)
            else:
                runs.append([ln])

        heads: list[LayoutLine] = []
        for run in runs:
            if len(run) > self.MAX_STYLE_RUN:
                continue
            if not all(
                (i > 0 and run[i - 1].text.endswith("-"))  # hyphenated continuation
                or (len(run) > 1 and _LABEL_ONLY.match(ln.text))  # "I." above a centred title
                or (self._passes_filters(ln) and qualifies(ln))
                for i, ln in enumerate(run)
            ):
                continue
            if all(_LABEL_ONLY.match(ln.text) for ln in run):
                continue
            text = run[0].text
            for ln in run[1:]:
                if text.endswith("-") and ln.text[:1].islower():
                    text = text[:-1] + ln.text  # re-join a hyphenated word
                else:
                    text = f"{text} {ln.text}"
            if not self._heading_text_ok(text):
                continue
            head = run[0]
            for ln in run[1:]:
                head.bbox = _union(head.bbox, ln.bbox)
            head.text = text
            self._parts[id(head)] = run[1:]
            heads.append(head)
        return heads

    def _heading_text_ok(self, text: str) -> bool:
        """Checks on the full (possibly joined) heading text."""
        words = text.split()
        if len(text) > self.MAX_CHARS * 1.5 or len(words) > self.MAX_WORDS * 1.5:
            return False
        if _ADMONITION.match(text) or _ADMONITION_PREFIX.match(text):
            return False  # NOTE / WARNING / CAUTION callouts are not sections
        if words[-1].lower().strip(_STRIP) in _TRAILING_STOPWORDS:
            return False  # "This manual has been provided courtesy of"
        if _SENTENCE_BREAK.search(text):
            return False  # "Read these instructions carefully. These"
        return True

    # -- filters -------------------------------------------------------------
    @staticmethod
    def _repeat_key(ln: LayoutLine) -> tuple[str, tuple[float, bool]]:
        # Text + style: a running header "Installation Instructions" in 8pt must
        # not cancel the 18pt section heading with the same words.
        return (_normalize(ln.text), ln.style)

    def _repeated_texts(
        self, lines: list[LayoutLine], page_count: int
    ) -> set[tuple[str, tuple[float, bool]]]:
        """Running headers, footers and banners: same text and style on many pages."""
        if page_count < 3:
            return set()
        pages_per_text: dict[tuple[str, tuple[float, bool]], set[int]] = defaultdict(set)
        for ln in lines:
            if self._body and ln.size >= self._body * self.SIZE_RATIO:
                continue  # section titles repeated at the top of continuation pages are kept
            key = self._repeat_key(ln)
            if key[0]:
                pages_per_text[key].add(ln.page)
        limit = max(2, math.ceil(page_count * self.REPEAT_PAGE_SHARE))
        return {key for key, pages in pages_per_text.items() if len(pages) >= limit}

    def in_toc(self, ln: LayoutLine) -> bool:
        """Line sits in the table-of-contents part of a page."""
        span = self.toc_ranges.get(ln.page)
        index = self._index.get(id(ln))
        return span is not None and index is not None and span[0] <= index <= span[1]

    @property
    def toc_pages(self) -> set[int]:
        return set(self.toc_ranges)

    def is_repeated(self, ln: LayoutLine) -> bool:
        """Running header, footer or banner (same small text on many pages)."""
        return self._repeat_key(ln) in self.repeated

    def is_noise(self, ln: LayoutLine) -> bool:
        """Lines that are never headings and never useful context."""
        return self.is_repeated(ln) or self.in_toc(ln)

    def _passes_filters(self, ln: LayoutLine) -> bool:
        text = ln.text.strip()
        if ln.in_table or ln.in_figure is not None or self.is_noise(ln):
            return False
        if not (2 <= len(text) <= self.MAX_CHARS) or ln.style_run > self.MAX_STYLE_RUN:
            return False
        if len(text.split()) > self.MAX_WORDS:
            return False
        letters = sum(1 for ch in text if ch.isalpha())
        visible = sum(1 for ch in text if not ch.isspace())
        if letters < 2 or letters / visible < 0.5:
            return False
        if text[0].islower() or text.endswith((",", ";")) or _DOT_LEADER.search(text):
            return False
        if text.endswith(".") and len(text.split()) > 3:
            return False
        if looks_garbled(text):
            return False
        return True

    def _styled(self, ln: LayoutLine, body: float) -> bool:
        ratio = ln.size / body if body else 1.0
        if ratio >= self.SIZE_RATIO:
            return True
        if ln.bold and ratio >= 0.95:
            text = ln.text
            words = text.split()
            return text.isupper() or bool(_LABEL_PREFIX.match(text)) or (
                len(words) <= 8 and text[0].isupper()
            )
        return False

    @staticmethod
    def _fallback(ln: LayoutLine) -> bool:
        """Used only when no line stands out by font: ALL CAPS or 1.1-style numbering."""
        return (ln.text.isupper() and len(ln.text) <= 60) or bool(_NUMBERED.match(ln.text))

    def _toc_ranges(
        self, lines: list[LayoutLine], candidates: list[LayoutLine]
    ) -> dict[int, tuple[int, int]]:
        """
        Table-of-contents regions: page -> (first, last) index in reading order.

        A TOC page is titled "Contents"/"Table of Contents", or has several
        lines whose text shows up again as a heading on a later page (TOC
        continuation pages, "quick links" pages). Only the stretch from the
        TOC title (or first entry) to the last entry is TOC; sections before or
        after it on the same page are kept.
        """
        titles: dict[int, int] = {}
        for i, ln in enumerate(lines):
            if _TOC_TITLE.match(ln.text.strip(" :")):
                titles.setdefault(ln.page, i)
        last_seen: dict[str, int] = {}
        for c in candidates:
            key = _toc_key(c.text)
            last_seen[key] = max(last_seen.get(key, -1), c.page)
        # TOC entries are often consecutive same-style lines, so they are not
        # heading candidates themselves; compare every line on the page instead.
        entries: dict[int, set[str]] = defaultdict(set)
        first_entry: dict[int, int] = {}
        last_entry: dict[int, int] = {}
        for i, ln in enumerate(lines):
            if ln.in_figure is not None or ln.in_table:
                continue  # callout labels like "Air Filter" are not TOC entries
            key = _toc_key(ln.text)
            if key and last_seen.get(key, -1) > ln.page:
                entries[ln.page].add(key)
                first_entry.setdefault(ln.page, i)
                last_entry[ln.page] = i

        ranges: dict[int, tuple[int, int]] = {}
        for page in set(titles) | set(entries):
            if page not in titles and len(entries[page]) < self.TOC_MIN_ENTRIES:
                continue
            if page in titles:
                start = min(titles[page], first_entry.get(page, titles[page]))
            else:
                # No "Contents" title: the line right before the first entry is
                # the list's own title ("Quick Links")
                start = first_entry[page]
                if start > 0 and lines[start - 1].page == page:
                    start -= 1
            end = max(titles.get(page, -1), last_entry.get(page, -1))
            ranges[page] = (start, end)
        return ranges

    def _drop_title_and_emphasis(
        self, candidates: list[LayoutLine], body: float, page_count: int
    ) -> list[LayoutLine]:
        by_style: dict[tuple[float, bool], list[LayoutLine]] = defaultdict(list)
        for c in candidates:
            by_style[c.style].append(c)
        later_sizes = [s[0] for s, members in by_style.items() if any(m.page > 0 for m in members)]
        keep: set[tuple[float, bool]] = set()
        for style, members in by_style.items():
            size, bold = style
            if (
                page_count > 1
                and all(m.page == 0 for m in members)
                and (not later_sizes or size > max(later_sizes))
            ):
                continue  # cover / title page styling, larger than any later heading
            if size < body * self.SIZE_RATIO and len(members) > self.EMPHASIS_PER_PAGE * page_count:
                continue  # bold used for emphasis all over the document
            keep.add(style)

        # In longer documents, the largest styles that appear on a single page
        # only (above every style used on several pages) are titles, not sections.
        if page_count >= self.TITLE_MIN_PAGES:
            multi_page = [s for s in keep if len({m.page for m in by_style[s]}) > 1]
            if multi_page:
                top = max(s[0] for s in multi_page)
                keep = {
                    s for s in keep
                    if not (len({m.page for m in by_style[s]}) == 1 and s[0] > top)
                }
        return [c for c in candidates if c.style in keep]

    def _levels(self, candidates: list[LayoutLine]) -> dict[tuple[float, bool], int]:
        """
        Rank heading styles by size (bold first on ties) to get levels.

        Only styles used at least twice get their own level. A one-off style
        (a banner, a single big title) takes the level of the nearest ranked
        style at or below its size, so it doesn't push real sections down.
        """
        def order(style: tuple[float, bool]) -> tuple[float, bool]:
            return (-style[0], not style[1])

        counts = Counter(c.style for c in candidates)
        ranked = sorted((s for s, n in counts.items() if n >= 2), key=order)
        if not ranked:
            ranked = sorted(counts, key=order)
        levels = {style: rank + 1 for rank, style in enumerate(ranked)}
        for style in counts:
            if style not in levels:
                below = [s for s in ranked if order(s) >= order(style)]
                levels[style] = levels[below[0]] if below else len(ranked)
        return levels


# --------------------------------------------------------------------------
# Putting it together
# --------------------------------------------------------------------------
@dataclass
class LayoutAnalysis:
    headings: list[SectionHeading]
    layout: DocumentLayout
    sections: list[DocumentSection] = field(default_factory=list)  # text split at headings
    page_texts: list[str] = field(default_factory=list)  # per-page text in reading order


class _SectionBuilder:
    """Collects lines into DocumentSections, one per heading."""

    def __init__(self) -> None:
        self.sections: list[DocumentSection] = []
        self._reset([])

    def _reset(self, path: list[str]) -> None:
        self.path = path
        self.parts: list[str] = []
        self.offsets: list[tuple[int, int]] = []
        self.length = 0
        self.last: LayoutLine | None = None
        self.has_heading = bool(path)

    def start(self, path: list[str]) -> None:
        self.close()
        self._reset(path)

    def _separator(self, ln: LayoutLine) -> str:
        """Blank line at paragraph breaks, so the splitter prefers to cut there."""
        last = self.last
        if last is None:
            return ""
        size = max(ln.size, 1.0)
        gap = ln.bbox[1] - last.bbox[3]
        new_paragraph = (
            ln.page != last.page
            or gap > 0.5 * size                    # vertical space between paragraphs
            or ln.bbox[1] < last.bbox[1] - 2 * size  # jumped up: next column
        )
        return "\n\n" if new_paragraph else "\n"

    def add(self, ln: LayoutLine) -> None:
        sep = self._separator(ln)
        offset = self.length + len(sep)
        if self.last is None or ln.page != self.last.page:
            self.offsets.append((offset, ln.page))
        self.parts.append(sep + ln.text)
        self.length = offset + len(ln.text)
        self.last = ln

    def close(self) -> None:
        text = "".join(self.parts)
        if not text.strip():
            return
        if self.has_heading and len(self.parts) == 1:
            return  # heading directly followed by a sub-heading: nothing of its own
        self.sections.append(DocumentSection(list(self.path), text, list(self.offsets)))


def _tail(parts: list[str], max_chars: int) -> str:
    """Keep the text closest to the end (nearest the figure), cut at a word boundary."""
    text = " ".join(parts).strip()
    if len(text) <= max_chars:
        return text
    cut = text[-max_chars:]
    space = cut.find(" ")
    return "... " + (cut[space + 1:] if 0 <= space < 40 else cut)


def _figure_label(layout: DocumentLayout, page: int, rect: Rect | None, index: int) -> str:
    """A "FIG. N ..." style line just above or below the figure."""
    if rect is None:
        return ""
    best: tuple[float, str] | None = None
    for ln in layout.lines:
        # Only free-standing text: a "FIG. N" inside a figure belongs to that figure
        if ln.page != page or ln.figure_index is not None or ln.in_figure is not None:
            continue
        if not _FIGURE_LABEL.match(ln.text):
            continue
        overlaps = min(ln.bbox[2], rect[2]) - max(ln.bbox[0], rect[0]) > 0
        distance = max(rect[1] - ln.bbox[3], ln.bbox[1] - rect[3], 0.0)
        if overlaps and distance <= 36 and (best is None or distance < best[0]):
            best = (distance, ln.text)
    return best[1] if best else ""


def analyze_document(
    mu_doc: Any,
    diagrams: list[DiagramMetadata] | None = None,
    tables: list[TableData] | None = None,
    repair: TextRepair | None = None,
    max_context_chars: int = 500,
) -> LayoutAnalysis:
    """
    Detect headings, split the text into sections, and annotate diagrams and tables.

    Diagrams get heading_path, label and context; tables get heading_path.
    Section text keeps every line except repeated headers/footers and section
    titles repeated at the top of continuation pages.

    Args:
        mu_doc: open PyMuPDF document
        diagrams: diagrams to annotate in place (page is 0-indexed, bbox optional)
        tables: tables to annotate in place; their regions don't produce headings
        repair: shared TextRepair (built from mu_doc when omitted)
        max_context_chars: cap for the text kept between heading and figure
    """
    diagrams = diagrams or []
    tables = tables or []
    figure_rects: dict[int, list[tuple[int, Rect | None]]] = defaultdict(list)
    for index, diagram in enumerate(diagrams):
        figure_rects[diagram.page].append((index, diagram.bbox))
    table_rects: dict[int, list[tuple[int, Rect]]] = defaultdict(list)
    for index, table in enumerate(tables):
        if table.bbox is not None:
            table_rects[table.page].append((index, table.bbox))

    layout = DocumentLayout(mu_doc, repair=repair, figure_rects=figure_rects, table_rects=table_rects)
    detector = FontHeadingDetector()
    headings = detector.detect(layout)

    stack: list[tuple[int, str]] = []
    buffer: list[str] = []
    builder = _SectionBuilder()
    page_lines: list[list[str]] = [[] for _ in range(layout.page_count)]
    for ln in layout.lines:
        if ln.heading_part:
            continue
        if ln.heading_level:
            # A section title repeated at the top of a continuation page re-states
            # the current section; keep its sub-sections and the context so far.
            if any(level == ln.heading_level and text == ln.text for level, text in stack):
                continue
            while stack and stack[-1][0] >= ln.heading_level:
                stack.pop()
            stack.append((ln.heading_level, ln.text))
            buffer = []
            builder.start([text for _, text in stack])
            builder.add(ln)
            page_lines[ln.page].append(ln.text)
        elif ln.figure_index is not None:
            diagram = diagrams[ln.figure_index]
            diagram.heading_path = [text for _, text in stack]
            diagram.context = _tail(buffer, max_context_chars)
            diagram.label = _figure_label(layout, diagram.page, diagram.bbox, ln.figure_index)
        elif ln.table_index is not None:
            tables[ln.table_index].heading_path = [text for _, text in stack]
        elif ln.text and not detector.is_repeated(ln):
            builder.add(ln)
            page_lines[ln.page].append(ln.text)
            if ln.in_figure is None and not detector.in_toc(ln):
                buffer.append(ln.text)
    builder.close()

    return LayoutAnalysis(
        headings=headings,
        layout=layout,
        sections=builder.sections,
        page_texts=["\n".join(lines) for lines in page_lines],
    )
