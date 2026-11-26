"""
Verification script for the vector ingestion pipeline.

Self-contained: builds its own fixture PDF, uses a temporary Chroma database,
a temporary diagram folder and fake embeddings, so it needs no embedding server
and never touches your real data.

Covers:
  - Config: new options, defaults, parsing, removed TABLE_MIN_CONFIDENCE
  - File hashing and heading extraction
  - Table extraction and the strict junk-table filter
  - Diagram extraction (raster filter, vector figures, captions, stale cleanup)
  - Chunking: text / table / diagram_reference chunks, sequence, pages, totals
  - Layout analysis: font-based headings and levels, TOC/footer/callout
    filtering, two-column reading order, diagram heading path / label /
    context, shifted-font text repair, overprint removal
  - Heading checks on the real PDFs in documents/ (skipped if none)
  - Embedding retry with backoff (fake service)
  - Storage: metadata persisted, all-or-nothing, rollback, upsert, replacement
  - Change detection (skip unchanged, re-ingest changed, failed docs retried)
  - Document management: IDs, list, delete by ID / filename, CLI resolution
  - Progress bars do not break the pipeline

Usage:
    python tests/verify_ingestion.py            # run all checks
    python tests/verify_ingestion.py --live     # also sanity-check your real Chroma DB
    python tests/verify_ingestion.py --headings # also print every heading found in documents/
"""

import logging
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from src.config import IngestionConfig, load_config  # noqa: E402
from src.data_models import (  # noqa: E402
    ChunkMetadata,
    DiagramMetadata,
    DocumentMetadata,
    IngestionState,
    ParsedDocument,
    TableData,
    TextChunk,
)
from src.ingestion import nodes, tools  # noqa: E402
from src.ingestion.layout import (  # noqa: E402
    DocumentLayout,
    LayoutLine,
    TextRepair,
    _Block,
    _normalize_bullets,
    _respace_letter_spaced,
    analyze_document,
    looks_garbled,
    looks_shifted,
    shift_decode,
    text_quality,
)
from src.ingestion.tools import (  # noqa: E402
    ChromaVectorStore,
    DiagramExtractor,
    DocumentHasher,
    PDFParser,
    TableExtractor,
    TextChunker,
)

logging.disable(logging.CRITICAL)

FAKE_VECTOR = [0.1, 0.2, 0.3]

# --------------------------------------------------------------------------
# Tiny check harness
# --------------------------------------------------------------------------
_results: list[tuple[str, bool, str]] = []


def section(title: str) -> None:
    print(f"\n{title}")


def check(name: str, condition: bool, detail: str = "") -> bool:
    _results.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    suffix = f"  ({detail})" if detail and not condition else ""
    print(f"  [{mark}] {name}{suffix}")
    return bool(condition)


# --------------------------------------------------------------------------
# Fixture PDF
# --------------------------------------------------------------------------
def build_fixture_pdf(path: Path, extra_text: str = "") -> None:
    """
    Create a 2-page PDF containing:
      page 1: headings, body text, a real 4x3 table, a tiny (1x1) image
      page 2: a vector figure labelled "FIG. 1 Wiring Diagram", a large raster
              image, and a vector box with no text (uncaptioned figure)
    """
    doc = pymupdf.open()

    # ---- page 1
    page = doc.new_page()
    page.insert_text((72, 60), "1. INTRODUCTION", fontsize=14)
    page.insert_text((72, 90), "This manual describes the installation of the roof unit.", fontsize=10)
    page.insert_text((72, 105), "Read all instructions carefully before starting. " + extra_text, fontsize=10)
    page.insert_text((72, 140), "2. SPECIFICATIONS", fontsize=14)

    rows = [
        ["Model", "Voltage", "Capacity"],
        ["A100", "115V", "13500 BTU"],
        ["B200", "115V", "15000 BTU"],
        ["C300", "230V", "17000 BTU"],
    ]
    x0, y0, cw, rh = 72, 160, 110, 24
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cell = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            page.draw_rect(cell, color=(0, 0, 0), width=0.8)
            page.insert_text((cell.x0 + 6, cell.y0 + 16), text, fontsize=10)

    tiny = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1, 1))
    tiny.clear_with(200)
    page.insert_image(pymupdf.Rect(450, 700, 452, 702), pixmap=tiny)

    # ---- page 2
    page = doc.new_page()
    page.insert_text((72, 60), "3. WIRING", fontsize=14)
    page.insert_text((72, 80), "See the figure below for wiring details.", fontsize=10)

    fig = pymupdf.Rect(72, 120, 372, 320)
    page.draw_rect(fig, color=(0, 0, 0), width=1.5)
    page.draw_circle(pymupdf.Point(150, 220), 40, color=(0, 0, 0), width=1.5)
    page.draw_line(pymupdf.Point(200, 150), pymupdf.Point(340, 290), color=(0, 0, 0), width=1.5)
    page.insert_text((90, 145), "FIG. 1 Wiring Diagram", fontsize=11)

    big = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 90))
    big.clear_with(120)
    page.insert_image(pymupdf.Rect(400, 120, 520, 210), pixmap=big)

    # vector shape with no text inside it -> figure without caption
    blank = pymupdf.Rect(72, 450, 272, 580)
    page.draw_rect(blank, color=(0, 0, 0), width=1.5)
    page.draw_circle(pymupdf.Point(170, 515), 30, color=(0, 0, 0), width=1.5)

    doc.save(str(path))
    doc.close()


def shift_encode(text: str) -> str:
    """Produce the garbled form some PDFs use (each character code 29 lower)."""
    return "".join(c if c.isspace() else chr(ord(c) - 29) for c in text)


GARBLED_LABEL = "THERMOSTAT WIRING DIAGRAM FIELD CONNECTIONS"
FOOTER = "Downloaded from example-manuals.com"


def build_layout_pdf(path: Path) -> None:
    """
    Create a 3-page PDF that exercises layout analysis:
      page 1: overprinted cover title, a CONTENTS page listing later headings
      page 2: two columns under MAINTENANCE; right column has B. Fan Motor and
              a figure with a "FIG. 3" label below it
      page 3: MAINTENANCE repeated at the top (continuation), a figure, then
              WIRING DIAGRAMS with shifted-font text in the body and inside a
              figure, a wrapped heading, a bold paragraph and a WARNING callout
    Every page has the same small footer.
    """
    doc = pymupdf.open()

    def text(page, x, y, s, size=10, font="helv"):
        page.insert_text((x, y), s, fontsize=size, fontname=font)

    def figure(page, rect):
        page.draw_rect(rect, color=(0, 0, 0), width=1.5)
        page.draw_line(pymupdf.Point(rect.x0 + 10, rect.y0 + 10), pymupdf.Point(rect.x1 - 10, rect.y1 - 10),
                       color=(0, 0, 0), width=1.5)

    # ---- page 1: cover + contents
    page = doc.new_page()
    for dx, dy in ((0, 0), (0.4, 0), (0, 0.4)):  # fake bold: drawn three times
        text(page, 50 + dx, 60 + dy, "SERVICE GUIDE", 24, "hebo")
    text(page, 50, 110, "CONTENTS", 14, "hebo")
    for i, entry in enumerate(["MAINTENANCE", "A. Air Filter", "B. Fan Motor", "WIRING DIAGRAMS"]):
        text(page, 50, 130 + 12 * i, f"{entry} ........................ {2 + (i == 3)}")
    text(page, 200, 780, FOOTER, 8)

    # ---- page 2: two columns
    page = doc.new_page()
    text(page, 50, 60, "MAINTENANCE", 18, "hebo")
    text(page, 50, 90, "A. Air Filter", 12, "hebo")
    for i, s in enumerate(["Clean the air filter every two weeks.", "Replace a torn filter right away.",
                           "Left column filler text about filters."]):
        text(page, 50, 110 + 12 * i, s)
    text(page, 330, 90, "B. Fan Motor", 12, "hebo")
    text(page, 330, 110, "The fan motor is permanently lubricated.")
    text(page, 330, 122, "Check the fan blades for dirt.")
    figure(page, pymupdf.Rect(330, 150, 550, 300))
    text(page, 330, 318, "FIG. 3 Fan Motor Location", 9)
    text(page, 200, 780, FOOTER, 8)

    # ---- page 3: continuation header, garbled text, wrapped heading
    page = doc.new_page()
    text(page, 50, 60, "MAINTENANCE", 18, "hebo")
    text(page, 50, 90, "Continue checking the fan motor wiring.")
    figure(page, pymupdf.Rect(50, 110, 250, 230))
    text(page, 80, 170, "BRACKET", 8)
    text(page, 50, 280, "WIRING DIAGRAMS", 18, "hebo")
    text(page, 50, 305, shift_encode(GARBLED_LABEL), 10, "cour")
    figure(page, pymupdf.Rect(50, 320, 300, 450))
    text(page, 70, 360, shift_encode("THERMOSTAT"), 8, "cour")
    text(page, 70, 380, shift_encode("FIELD WIRING"), 8, "cour")
    text(page, 50, 480, "C. Installing The Air Distribution", 12, "hebo")
    text(page, 50, 494, "Box Assembly", 12, "hebo")
    text(page, 50, 512, "Fasten the box with the supplied screws.")
    for i in range(4):
        text(page, 50, 540 + 12 * i, f"BOLD PARAGRAPH LINE {i + 1} STAYS BODY TEXT", 10, "hebo")
    text(page, 50, 620, "WARNING", 12, "hebo")
    text(page, 50, 636, "Disconnect power before servicing.")
    text(page, 200, 780, FOOTER, 8)

    doc.save(str(path))
    doc.close()


# --------------------------------------------------------------------------
# Pipeline helpers
# --------------------------------------------------------------------------
class Workspace:
    """Temporary docs folder, Chroma database and diagram folder."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="verify_ingestion_"))
        self.docs = self.root / "docs"
        self.docs.mkdir()
        self.diagrams = self.root / "diagrams"
        self.config = IngestionConfig(
            embedding_base_url="http://fake",
            embedding_model="fake",
            embedding_api_key="fake",
            chroma_persist_directory=str(self.root / "chroma"),
            chroma_collection_name="verify_collection",
            document_input_dir=str(self.docs),
        )
        self._orig_defaults = DiagramExtractor.__init__.__defaults__
        DiagramExtractor.__init__.__defaults__ = (self.diagrams,)

    def close(self) -> None:
        DiagramExtractor.__init__.__defaults__ = self._orig_defaults
        shutil.rmtree(self.root, ignore_errors=True)

    def store(self) -> ChromaVectorStore:
        store = ChromaVectorStore(self.config.chroma_persist_directory, self.config.chroma_collection_name)
        store.initialize_collection()
        return store

    def run_pipeline(self, drop_chunks: int = 0, progress: bool = False) -> IngestionState:
        """Run load -> parse -> metadata -> chunk -> (fake embed) -> store."""
        state = IngestionState(config=self.config, show_progress_bar=progress)

        def apply(result: dict) -> None:
            for key, value in result.items():
                setattr(state, key, value)

        apply(nodes.load_documents(state))
        apply(nodes.parse_documents(state))
        apply(nodes.extract_metadata(state))
        apply(nodes.chunk_text(state))
        for chunk in state.chunks:
            chunk.embedding = list(FAKE_VECTOR)
        if drop_chunks:  # simulate embedding batches that failed after all retries
            state.chunks = state.chunks[:-drop_chunks]
        apply(nodes.store_vectors(state))
        return state

    def listing(self) -> list[dict]:
        return nodes.list_documents(IngestionState(config=self.config))["documents_in_store"]


# --------------------------------------------------------------------------
# Test sections
# --------------------------------------------------------------------------
def test_config() -> None:
    section("1. Configuration")
    managed = [
        "EMBEDDING_BASE_URL", "EMBEDDING_MODEL", "EMBEDDING_API_KEY", "EMBEDDING_MAX_RETRIES",
        "EMBEDDING_RETRY_BACKOFF_BASE", "ENABLE_TABLE_EXTRACTION", "ENABLE_CHANGE_DETECTION",
        "CHANGE_DETECTION_LOG_LEVEL", "SHOW_PROGRESS_BAR", "CHUNK_SIZE", "CHUNK_OVERLAP",
        "CHROMA_PERSIST_DIRECTORY", "CHROMA_COLLECTION_NAME", "DOCUMENT_INPUT_DIR",
        "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "TABLE_MIN_CONFIDENCE",
    ]
    saved = {k: os.environ.pop(k, None) for k in managed}
    tmp = Path(tempfile.mkdtemp())
    try:
        minimal = tmp / "minimal.env"
        minimal.write_text("EMBEDDING_BASE_URL=http://x\nEMBEDDING_MODEL=m\nEMBEDDING_API_KEY=k\n")
        cfg = load_config(str(minimal))
        check("optional settings fall back to defaults",
              cfg.embedding_max_retries == 3 and cfg.embedding_retry_backoff_base == 2.0
              and cfg.enable_table_extraction is True and cfg.enable_change_detection is True
              and cfg.show_progress_bar is True and cfg.chunk_size == 1000 and cfg.chunk_overlap == 100)
        check("TABLE_MIN_CONFIDENCE has been removed", not hasattr(cfg, "table_min_confidence"))

        for key in managed:
            os.environ.pop(key, None)
        custom = tmp / "custom.env"
        custom.write_text(
            "EMBEDDING_BASE_URL=http://x\nEMBEDDING_MODEL=m\nEMBEDDING_API_KEY=k\n"
            "EMBEDDING_MAX_RETRIES=5\nEMBEDDING_RETRY_BACKOFF_BASE=1.5\n"
            "ENABLE_TABLE_EXTRACTION=false\nENABLE_CHANGE_DETECTION=false\n"
            "SHOW_PROGRESS_BAR=false\nCHANGE_DETECTION_LOG_LEVEL=info\n"
        )
        cfg = load_config(str(custom))
        check("numeric options are parsed", cfg.embedding_max_retries == 5 and cfg.embedding_retry_backoff_base == 1.5)
        check("boolean options are parsed",
              cfg.enable_table_extraction is False and cfg.enable_change_detection is False
              and cfg.show_progress_bar is False)
        check("log level option is read", cfg.change_detection_log_level == "info")

        missing = tmp / "missing.env"
        missing.write_text("EMBEDDING_MODEL=m\n")
        for key in managed:
            os.environ.pop(key, None)
        try:
            load_config(str(missing))
            check("missing required variables raise ValueError", False)
        except ValueError:
            check("missing required variables raise ValueError", True)
    finally:
        for key in managed:
            os.environ.pop(key, None)
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
        shutil.rmtree(tmp, ignore_errors=True)


def test_hashing_and_headings(ws: Workspace, pdf: Path) -> None:
    section("2. File hashing and page-by-page fallback chunking")
    h1 = DocumentHasher.calculate_file_hash(pdf)
    check("hash is a 64-char SHA256 hex string", len(h1) == 64 and all(c in "0123456789abcdef" for c in h1))
    check("hash is deterministic", h1 == DocumentHasher.calculate_file_hash(pdf))

    other = ws.root / "other.pdf"
    build_fixture_pdf(other, extra_text="changed")
    check("hash changes when file content changes", h1 != DocumentHasher.calculate_file_hash(other))
    check("hash store methods are gone (Chroma is the source of truth)",
          not hasattr(DocumentHasher, "store_hash") and not hasattr(DocumentHasher, "is_document_changed"))

    check("plain-text heading extractor is gone (layout analysis is required)",
          not hasattr(tools, "HeadingExtractor"))

    # Without layout sections, text is chunked page by page through the section chunker
    pages = ["First page about the air filter.", "", "Third page: " + "wiring detail " * 60]
    doc = ParsedDocument(
        content="\n\n".join(pages),
        metadata=DocumentMetadata(filename="No_Layout-Manual.pdf", total_pages=3, processing_timestamp="t"),
        pages=pages,
    )
    chunks = TextChunker(chunk_size=ws.config.chunk_size, overlap=ws.config.chunk_overlap).chunk_document(doc)
    check("page fallback produces chunks", len(chunks) >= 2, str(len(chunks)))
    check("page fallback assigns each chunk its own page (1-indexed, empty page skipped)",
          chunks[0].metadata.page_number == 1 and all(c.metadata.page_number == 3 for c in chunks[1:]),
          str([c.metadata.page_number for c in chunks]))
    check("page fallback chunks carry the document-title section line",
          all(c.content.startswith("Section: No Layout Manual\n") for c in chunks), chunks[0].content[:60])
    check("page fallback chunks fit in CHUNK_SIZE", all(len(c.content) <= ws.config.chunk_size for c in chunks))
    check("page fallback char offsets point into document.content",
          all(doc.content[c.metadata.start_char_index:c.metadata.end_char_index] in c.content for c in chunks))
    empty = ParsedDocument(content="", metadata=DocumentMetadata(filename="e.pdf", total_pages=1,
                           processing_timestamp="t"), pages=["  "])
    check("document without text produces no chunks", TextChunker(1000, 100).chunk_document(empty) == [])


def test_tables(pdf: Path) -> None:
    section("3. Table extraction and junk filter")
    tables = TableExtractor().extract_tables_from_pdf(pdf)
    check("fixture table is extracted", any(t.row_count == 4 and t.col_count == 3 for t in tables),
          f"found {[(t.row_count, t.col_count) for t in tables]}")
    real = next((t for t in tables if t.row_count == 4 and t.col_count == 3), None)
    if real:
        check("table is rendered as markdown", real.content.startswith("| Model") and "|---|" in real.content)
        check("table page is 0-indexed", real.page == 0)
        check("table content keeps cell text", "13500 BTU" in real.content and "230V" in real.content)

    def table(rows: list[list[str]]) -> TableData:
        return TableData(content="", page=0, page_position=0, row_count=len(rows),
                         col_count=len(rows[0]) if rows else 0, raw_data=rows)

    keep = table([["Model", "Volts", "BTU"], ["A", "115", "13500"], ["B", "115", "15000"], ["C", "230", "17000"]])
    check("filter keeps a full, real table", TextChunker.is_meaningful_table(keep))
    check("filter drops an all-empty grid", not TextChunker.is_meaningful_table(table([[""] * 8] * 7)))
    check("filter drops a single-row label table", not TextChunker.is_meaningful_table(table([["FIG.", "10"]])))
    check("filter drops a single-column table", not TextChunker.is_meaningful_table(table([["a"], ["b"], ["c"], ["d"], ["e"], ["f"]])))
    check("filter drops a mostly-empty table",
          not TextChunker.is_meaningful_table(table([["x", "", "", ""], ["", "", "", ""], ["", "y", "", ""]])))
    check("filter drops None cells and punctuation-only cells",
          not TextChunker.is_meaningful_table(table([[None, "-", "|"], ["", "...", None], ["-", "-", "-"]])))


def test_diagrams(ws: Workspace, pdf: Path) -> list[DiagramMetadata]:
    section("4. Diagram extraction")
    extractor = DiagramExtractor(ws.diagrams)

    stale = ws.diagrams / f"{pdf.stem}_p99_img0.png"
    stale.write_bytes(b"stale")
    unrelated = ws.diagrams / "someotherdoc_p0_img0.png"
    unrelated.write_bytes(b"keep")

    diagrams = extractor.extract_all_diagrams(pdf)
    kinds = Counter(d.kind for d in diagrams)
    check("embedded raster image is extracted", kinds["raster"] == 1, str(dict(kinds)))
    check("tiny (1x1) embedded image is filtered out",
          all(not (d.kind == "raster" and d.page == 0) for d in diagrams))
    check("vector figures are detected", kinds["vector_figure"] >= 2, str(dict(kinds)))

    check("every image file exists and has a real format", all(
        Path(d.image_path).exists() and Path(d.image_path).read_bytes()[:4] in
        (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xdb", b"\xff\xd8\xff\xee") for d in diagrams))
    check("type field holds the real file format", all(d.type == Path(d.image_path).suffix.lstrip(".") for d in diagrams))
    check("vector figures are rendered as PNG named *_fig*", all(
        d.type == "png" and "_fig" in d.image_path for d in diagrams if d.kind == "vector_figure"))

    captions = [d.caption for d in diagrams if d.kind == "vector_figure"]
    check("caption is built from text inside the figure region",
          any("FIG. 1" in c and "Wiring Diagram" in c for c in captions), str(captions))
    check("a figure with no text gets an empty caption", any(c == "" for c in captions), str(captions))
    check("raster images have no caption", all(d.caption == "" for d in diagrams if d.kind == "raster"))
    check("diagram pages are 0-indexed and in range", all(d.page in (0, 1) for d in diagrams))
    check("position_index restarts on each page", all(
        sorted(d.position_index for d in diagrams if d.page == p) == list(range(sum(1 for x in diagrams if x.page == p)))
        for p in (0, 1)))

    check("stale files for this document are removed on re-extraction", not stale.exists())
    check("files belonging to other documents are left alone", unrelated.exists())
    count_before = len(list(ws.diagrams.glob(f"{pdf.stem}_*")))
    extractor.extract_all_diagrams(pdf)
    check("re-extraction does not accumulate files", len(list(ws.diagrams.glob(f"{pdf.stem}_*"))) == count_before)
    unrelated.unlink()
    return diagrams


def test_chunking(ws: Workspace, pdf: Path) -> None:
    section("5. Chunking (text, table, diagram_reference)")
    shutil.copy(pdf, ws.docs / "manual.pdf")
    state = IngestionState(config=ws.config, show_progress_bar=False)
    for result in (nodes.load_documents(state),):
        for k, v in result.items():
            setattr(state, k, v)
    for fn in (nodes.parse_documents, nodes.extract_metadata):
        for k, v in fn(state).items():
            setattr(state, k, v)
    doc = state.documents[0]

    check("parse_documents reports tables and diagrams", state.tables_extracted >= 1 and state.diagrams_found >= 3,
          f"tables={state.tables_extracted} diagrams={state.diagrams_found}")
    check("metadata counts are populated",
          doc.metadata.table_count == len(doc.tables) and doc.metadata.diagram_count == len(doc.diagrams))
    check("file hash, size and timestamps are on the document",
          len(doc.metadata.file_hash) == 64 and doc.metadata.file_size_bytes > 0
          and doc.metadata.loaded_at and doc.metadata.extraction_timestamp)
    check("section headings are extracted",
          any("INTRODUCTION" in h for h in doc.metadata.section_headings), str(doc.metadata.section_headings))
    check("heading_count matches", doc.metadata.heading_count == len(doc.metadata.section_headings))

    chunker = TextChunker(chunk_size=ws.config.chunk_size, overlap=ws.config.chunk_overlap)
    chunks = chunker.chunk_all(doc)
    by_type = Counter(c.metadata.chunk_type for c in chunks)
    check("all three chunk types are produced",
          by_type["text"] >= 1 and by_type["table"] == 1 and by_type["diagram_reference"] >= 1, str(dict(by_type)))

    sequences = [c.metadata.chunk_sequence for c in chunks]
    check("chunk_sequence is continuous across all types", sequences == list(range(len(chunks))))
    ids = [f"{c.metadata.source_filename}_{c.metadata.page_number}_{c.metadata.chunk_sequence}" for c in chunks]
    check("chunk IDs are unique", len(set(ids)) == len(ids))
    check("total_chunks is set once on the combined list", {c.metadata.total_chunks for c in chunks} == {len(chunks)})
    check("file_hash and ingested_at are on every chunk",
          all(c.metadata.file_hash == doc.metadata.file_hash and c.metadata.ingested_at for c in chunks))

    check("document is split into sections at its headings",
          [s.path for s in doc.sections] == [["1. INTRODUCTION"], ["2. SPECIFICATIONS"], ["3. WIRING"]],
          str([s.path for s in doc.sections]))
    text_chunks = [c for c in chunks if c.metadata.chunk_type == "text"]
    check("every text chunk starts with its section line, matching its metadata",
          all(c.content.startswith(TextChunker.section_line(c.metadata.section_headings) + "\n")
              and c.metadata.section_headings for c in text_chunks),
          str([c.content[:40] for c in text_chunks]))
    intro = next((c for c in text_chunks if "installation of the roof unit" in c.content), None)
    wiring_text = next((c for c in text_chunks if "See the figure below" in c.content), None)
    check("text chunks carry the section they come from",
          intro is not None and intro.metadata.section_headings == ["1. INTRODUCTION"]
          and wiring_text is not None and wiring_text.metadata.section_headings == ["3. WIRING"])
    check("no text chunk mixes two sections",
          not any("INTRODUCTION" in c.content and "WIRING" in c.content for c in text_chunks))
    check("section chunks keep the right page", wiring_text is not None and wiring_text.metadata.page_number == 2)
    check("text chunks (with their section line) fit in CHUNK_SIZE",
          all(len(c.content) <= ws.config.chunk_size for c in text_chunks),
          str(max(len(c.content) for c in text_chunks)))

    table_chunk = next(c for c in chunks if c.metadata.chunk_type == "table")
    check("table chunk carries its section (text and metadata)",
          "\nSection: 2. SPECIFICATIONS\n" in table_chunk.content
          and table_chunk.metadata.section_headings == ["2. SPECIFICATIONS"], table_chunk.content[:120])
    check("table chunk is whole (all rows present)", all(x in table_chunk.content for x in ("A100", "B200", "C300")))
    check("table chunk has context prefix and 1-indexed page",
          table_chunk.content.startswith("Table from ") and table_chunk.metadata.page_number == 1)
    check("table chunk records its shape",
          (table_chunk.metadata.table_rows, table_chunk.metadata.table_cols) == (4, 3))

    diagram_chunks = [c for c in chunks if c.metadata.chunk_type == "diagram_reference"]
    check("every diagram gets a chunk, captioned or not", len(diagram_chunks) == len(doc.diagrams),
          f"{len(diagram_chunks)} chunks for {len(doc.diagrams)} diagrams")
    uncaptioned = [c for c, d in zip(diagram_chunks, doc.diagrams) if not d.caption.strip()]
    check("an uncaptioned diagram is still findable by its section heading",
          bool(uncaptioned) and all("Section: 3. WIRING" in c.content for c in uncaptioned),
          str([c.content for c in uncaptioned]))
    check("diagram chunk records its heading path in metadata",
          all(c.metadata.section_headings == ["3. WIRING"] for c in diagram_chunks if c.metadata.page_number == 2),
          str([c.metadata.section_headings for c in diagram_chunks]))
    wiring = next((c for c in diagram_chunks if "Wiring Diagram" in c.content), None)
    check("diagram chunk text carries the caption", wiring is not None)
    if wiring:
        check("diagram chunk page is 1-indexed", wiring.metadata.page_number == 2)
        check("diagram chunk keeps image path and kind",
              Path(wiring.metadata.diagram_path).exists() and wiring.metadata.diagram_kind == "vector_figure")
        check("diagram chunk includes the text leading up to it",
              "Context: See the figure below for wiring details." in wiring.content, wiring.content)
    raster = next((d for d in doc.diagrams if d.kind == "raster"), None)
    check("raster images record their position on the page", raster is not None and raster.bbox is not None)
    check("text chunk pages are 1-indexed",
          all(c.metadata.page_number >= 1 for c in chunks if c.metadata.chunk_type == "text"))

    # disabling table extraction produces no tables and no table chunks
    cfg_off = IngestionConfig(**{**ws.config.__dict__, "enable_table_extraction": False})
    state_off = IngestionState(config=cfg_off, show_progress_bar=False)
    for fn in (nodes.load_documents, nodes.parse_documents):
        for k, v in fn(state_off).items():
            setattr(state_off, k, v)
    doc_off = state_off.documents[0]
    off_chunks = chunker.chunk_all(doc_off)
    check("ENABLE_TABLE_EXTRACTION=false extracts no tables",
          state_off.tables_extracted == 0 and doc_off.tables == [])
    check("ENABLE_TABLE_EXTRACTION=false creates no table chunks",
          not any(c.metadata.chunk_type == "table" for c in off_chunks))


def test_layout(ws: Workspace) -> None:
    section("5b. Layout analysis (headings, reading order, diagram context, text repair)")

    # --- text repair and scoring units
    check("shift_decode undoes the +29 shift", shift_decode("7+(50267$7") == "THERMOSTAT")
    check("shift_decode leaves whitespace alone", shift_decode(shift_encode("FAN MOTOR")) == "FAN MOTOR")
    check("garbled text scores low, real text scores high",
          text_quality(shift_encode(GARBLED_LABEL)) < 0.4 and text_quality(GARBLED_LABEL) > 0.9)
    check("shifted text is detected", looks_shifted(shift_encode(GARBLED_LABEL)))
    check("normal text is not detected as shifted", not looks_shifted("Check the fan blades for dirt and dust."))
    check("tiny symbol samples are not detected as shifted",
          not looks_shifted("*** ** *") and not looks_shifted("Knobs pk/ 2"))
    check("looks_garbled flags unreadable captions only",
          looks_garbled("(/(&+($7 + & / 7+(50267$7") and not looks_garbled("FIG. 5 Roof Opening"))
    spaced = [(c, (x, 0.0, x + 6.0, 10.0)) for c, x in
              zip("SERVICEMANUAL", [0, 10, 20, 30, 40, 50, 60, 85, 95, 105, 115, 125, 135])]
    check("letter-spaced text gets its word breaks back",
          _respace_letter_spaced(spaced, 10.0) == "SERVICE MANUAL", str(_respace_letter_spaced(spaced, 10.0)))

    # --- bullets (private-use font glyphs such as Wingdings U+F097)
    check("a leading bullet glyph is written as '- '",
          _normalize_bullets(" 12 Volt DC not clean") == ("- 12 Volt DC not clean", False)
          and _normalize_bullets("• Communication cable.") == ("- Communication cable.", False))
    check("a bullet-only line is flagged", _normalize_bullets("  ") == ("-", True))
    check("normal text is left alone",
          _normalize_bullets("E1 – Loss of communications") == ("E1 – Loss of communications", False)
          and _normalize_bullets("- already a dash") == ("- already a dash", False))

    def line(text: str, x0: float, y0: float, size: float = 10.0, bullet: bool = False) -> LayoutLine:
        return LayoutLine(page=0, text=text, size=size, bbox=(x0, y0, x0 + 6 * len(text), y0 + size), bullet=bullet)

    item = line("Two power module DIP switches set as same zone.", 62, 100)
    lone = line("-", 50, 101, size=8.0, bullet=True)  # smaller glyph, slightly lower, in its own block
    orphan = line("-", 50, 300, bullet=True)  # nothing beside it
    cell = line("Thermostat (24V)", 50, 500)  # parts table: item, then an availability dot, then a value
    dot = line("-", 160, 500, bullet=True)
    value = line("S/O 9330-345", 175, 500)
    blocks = [_Block(bbox=b.bbox, lines=[b]) for b in (lone, item, orphan, cell, dot, value)]
    DocumentLayout._merge_labels(blocks)
    check("a bullet in its own block is attached to the item beside it",
          item.text == "- Two power module DIP switches set as same zone." and not blocks[0].lines, item.text)
    check("a bullet with no item beside it is left as it is", blocks[2].lines == [orphan])
    check("a dot with text to its left (table marker) is not treated as a list bullet",
          blocks[4].lines == [dot] and value.text == "S/O 9330-345", value.text)

    # --- layout fixture
    pdf = ws.root / "layout.pdf"
    build_layout_pdf(pdf)
    diagrams = DiagramExtractor(ws.diagrams).extract_all_diagrams(pdf)
    with pymupdf.open(pdf) as mu_doc:
        repair = TextRepair(mu_doc)
        analysis = analyze_document(mu_doc, diagrams=diagrams)
        layout: DocumentLayout = analysis.layout
    headings = [(h.page, h.level, h.text) for h in analysis.headings]
    texts = [h.text for h in analysis.headings]

    check("shifted font is found on the right page only",
          {page for page, _ in repair.shifted} == {2}, str(repair.shifted))
    check("section headings are found with levels",
          (1, 1, "MAINTENANCE") in headings and (1, 2, "A. Air Filter") in headings
          and (1, 2, "B. Fan Motor") in headings and (2, 1, "WIRING DIAGRAMS") in headings, str(headings))
    check("table-of-contents entries are not headings", all(page != 0 for page, _, _ in headings), str(headings))
    check("cover title is not a section heading", "SERVICE GUIDE" not in texts)
    check("overprinted (fake bold) text is read once",
          [ln.text for ln in layout.lines if ln.page == 0 and "SERVICE" in ln.text] == ["SERVICE GUIDE"],
          str([ln.text for ln in layout.lines if ln.page == 0 and "SERVICE" in ln.text]))
    check("repeated small footer is not a heading", FOOTER not in texts)
    check("wrapped heading is joined into one",
          "C. Installing The Air Distribution Box Assembly" in texts, str(texts))
    check("bold paragraph lines are not headings", not any("BOLD PARAGRAPH" in t for t in texts))
    check("WARNING callout is not a heading", "WARNING" not in texts)

    order = [ln.text for ln in layout.lines if ln.page == 1 and ln.text]
    check("two-column page reads the left column before the right",
          order.index("Left column filler text about filters.") < order.index("B. Fan Motor"), str(order))

    def diagram_on(page: int, contains: str) -> object:
        return next((d for d in diagrams if d.page == page and contains in d.caption + d.label), None)

    fan = next((d for d in diagrams if d.page == 1 and d.kind == "vector_figure"), None)
    check("right-column figure gets the full heading path",
          fan is not None and fan.heading_path == ["MAINTENANCE", "B. Fan Motor"],
          str(fan.heading_path if fan else None))
    if fan:
        check("figure label below the figure is captured", fan.label == "FIG. 3 Fan Motor Location", fan.label)
        check("context is the text since the previous heading (same column)",
              "fan motor is permanently lubricated" in fan.context and "air filter" not in fan.context.lower(),
              fan.context)
        check("footer text is not part of the context", FOOTER not in fan.context)

    bracket = diagram_on(2, "BRACKET")
    check("a repeated section title keeps the current sub-section",
          bracket is not None and bracket.heading_path == ["MAINTENANCE", "B. Fan Motor"],
          str(bracket.heading_path if bracket else None))
    garbled = diagram_on(2, "THERMOSTAT")
    check("shifted text inside a figure is decoded in its caption", garbled is not None,
          str([d.caption for d in diagrams if d.page == 2]))
    if garbled:
        check("decoded figure sits under WIRING DIAGRAMS", garbled.heading_path == ["WIRING DIAGRAMS"],
              str(garbled.heading_path))
        check("decoded body text is in the figure's context", GARBLED_LABEL in garbled.context, garbled.context)
        chunk_text = TextChunker.diagram_chunk_text(garbled, "layout.pdf")
        check("diagram chunk text lists section, figure text and context",
              "Section: WIRING DIAGRAMS" in chunk_text and "Text in figure: THERMOSTAT" in chunk_text
              and "Context: " in chunk_text, chunk_text)

    # --- sections (text split at headings)
    sections = {tuple(s.path): s for s in analysis.sections}
    fan_section = sections.get(("MAINTENANCE", "B. Fan Motor"))
    check("section text follows the column order (right column is its own section)",
          fan_section is not None and fan_section.text.startswith("B. Fan Motor")
          and "permanently lubricated" in fan_section.text and "air filter" not in fan_section.text.lower(),
          fan_section.text[:120] if fan_section else str(list(sections)))
    check("text continuing on the next page stays in its section",
          fan_section is not None and "Continue checking the fan motor wiring." in fan_section.text
          and fan_section.page_at(fan_section.text.index("Continue checking")) == 2)
    check("a heading followed directly by a sub-heading gets no section of its own",
          ("MAINTENANCE",) not in sections, str(list(sections)))
    check("repeated footers are left out of the section text",
          not any(FOOTER in s.text for s in analysis.sections))
    check("repeated section titles are left out of the section text",
          not any("MAINTENANCE" in s.text.split("\n") for s in analysis.sections),
          str([s.path for s in analysis.sections if "MAINTENANCE" in s.text.split("\n")]))
    check("decoded shifted text is in the section text",
          any(GARBLED_LABEL in s.text for s in analysis.sections if s.path == ["WIRING DIAGRAMS"]))
    front = analysis.sections[0]
    check("text before the first heading is its own section without a path",
          front.path == [] and "SERVICE GUIDE" in front.text, str(front.path))
    check("content before the first heading is labelled with the document title",
          TextChunker.section_path([], "My-Service_Manual-v2.pdf") == ["My Service Manual v2"]
          and TextChunker.section_path(["A"], "x.pdf") == ["A"])
    table = TableData(content="| a | b |", page=1, page_position=0, row_count=2, col_count=2,
                      raw_data=[["a", "b"], ["1", "2"]], bbox=(330.0, 105.0, 550.0, 128.0))
    with pymupdf.open(pdf) as mu_doc:
        analyze_document(mu_doc, tables=[table])
    check("tables get the heading path at their position", table.heading_path == ["MAINTENANCE", "B. Fan Motor"],
          str(table.heading_path))

    _, pages = PDFParser().parse_document(str(pdf))
    check("shifted text is decoded in the plain page text too", GARBLED_LABEL in pages[2], pages[2][:200])
    check("normal page text is untouched by text repair", "Clean the air filter every two weeks." in pages[1])

    # Same file again with a tiny context limit
    capped = DiagramExtractor(ws.diagrams).extract_all_diagrams(pdf)
    with pymupdf.open(pdf) as mu_doc:
        analyze_document(mu_doc, diagrams=capped, max_context_chars=30)
    fan_capped = next((d for d in capped if d.page == 1 and d.kind == "vector_figure"), None)
    check("context is capped at a word boundary, keeping the text nearest the figure",
          fan_capped is not None and len(fan_capped.context) <= 34
          and fan_capped.context.startswith("... ")
          and fan_capped.context.endswith("fan blades for dirt."),
          repr(fan_capped.context if fan_capped else None))


REAL_DOCS = ROOT / "documents"
DOMETIC = "Dometic 457915 Installation & Operating Instructions Manual1.pdf"


def test_real_headings(ws: Workspace, show: bool) -> None:
    section("5c. Heading checks on the PDFs in documents/")
    pdfs = sorted(REAL_DOCS.glob("*.pdf")) if REAL_DOCS.exists() else []
    if not pdfs:
        print("  [SKIP] no PDFs in documents/")
        return

    out_dir = ws.root / "real_diagrams"
    results = {}
    for pdf in pdfs:
        tables = [t for t in TableExtractor().extract_tables_from_pdf(pdf) if TextChunker.is_meaningful_table(t)]
        diagrams = DiagramExtractor(out_dir).extract_all_diagrams(pdf)
        with pymupdf.open(pdf) as mu_doc:
            analysis = analyze_document(mu_doc, diagrams=diagrams, tables=tables)
        results[pdf.name] = (analysis.headings, diagrams)
        if show:
            print(f"\n    --- {pdf.name}: {len(analysis.headings)} headings")
            for h in analysis.headings:
                print(f"      p{h.page + 1:<3} L{h.level} {'  ' * (h.level - 1)}{h.text}")

    for name, (headings, diagrams) in results.items():
        texts = [h.text for h in headings]
        check(f"{name}: headings found", len(headings) > 0)
        check(f"{name}: no heading is a paragraph (<= 120 chars, <= 18 words)",
              all(len(t) <= 120 and len(t.split()) <= 18 for t in texts),
              str([t for t in texts if len(t) > 120 or len(t.split()) > 18][:3]))
        check(f"{name}: no NOTE/WARNING/CAUTION callouts as headings",
              not any(t.split()[0].strip("!:").upper() in {"NOTE", "WARNING", "CAUTION", "DANGER"} for t in texts),
              str([t for t in texts if t.split()[0].strip("!:").upper() in {"NOTE", "WARNING", "CAUTION", "DANGER"}]))
        check(f"{name}: every diagram has a heading path, label, caption or context",
              all(d.heading_path or d.label or d.caption or d.context for d in diagrams if d.page > 0),
              str([Path(d.image_path).name for d in diagrams
                   if d.page > 0 and not (d.heading_path or d.label or d.caption or d.context)][:5]))

    if DOMETIC in results:
        headings, diagrams = results[DOMETIC]
        level = {h.text: h.level for h in headings}
        check("Dometic: MAINTENANCE and WIRING DIAGRAMS are top-level sections",
              level.get("MAINTENANCE") == 1 and level.get("WIRING DIAGRAMS") == 1, str(level))
        check("Dometic: sub-sections sit one level below",
              level.get("A. Air Filter") == 2 and level.get("B. Air Distribution Box Wiring Diagram") == 2)
        check("Dometic: spec values and part labels are not headings",
              not any(t in level for t in ("120 Vac", "8 Wood Screws", "HGFE")))
        check("Dometic: INSTALLATION INSTRUCTIONS is found despite repeating on every page",
              level.get("INSTALLATION INSTRUCTIONS") == 1)
        p18 = [d for d in diagrams if d.page == 17]
        check("Dometic: page 18 wiring diagram is decoded and under its section",
              any("THERMOSTAT" in d.caption and d.heading_path[-1:] == ["B. Air Distribution Box Wiring Diagram"]
                  for d in p18), str([(d.heading_path, d.caption[:40]) for d in p18]))
        p17 = [d for d in diagrams if d.page == 16]
        check("Dometic: caption-less page 17 figures still get the WIRING DIAGRAMS path",
              p17 and all(d.heading_path[:1] == ["WIRING DIAGRAMS"] for d in p17),
              str([d.heading_path for d in p17]))
        p16 = [d for d in diagrams if d.page == 15]
        check("Dometic: maintenance-page figures (if any) are under MAINTENANCE or GENERAL INFORMATION",
              all(d.heading_path[:1] in (["MAINTENANCE"], ["GENERAL INFORMATION"]) for d in p16),
              str([d.heading_path for d in p16]))


def test_retry(ws: Workspace) -> None:
    section("6. Embedding retry with backoff")
    import time

    sleeps: list[float] = []
    real_sleep = time.sleep
    time.sleep = lambda seconds: sleeps.append(seconds)

    class FlakyService:
        calls = 0
        fail_first = 2

        def __init__(self, **_: object) -> None:
            pass

        def embed_batch(self, texts: list[str]) -> list[list[float]]:
            FlakyService.calls += 1
            if FlakyService.calls <= FlakyService.fail_first:
                raise ConnectionError("simulated outage")
            return [list(FAKE_VECTOR) for _ in texts]

    def make_chunks(n: int) -> list[TextChunk]:
        return [TextChunk(content=f"c{i}", metadata=ChunkMetadata(source_filename="f.pdf", page_number=1, chunk_sequence=i))
                for i in range(n)]

    original = nodes.EmbeddingService
    nodes.EmbeddingService = FlakyService
    try:
        FlakyService.calls, FlakyService.fail_first = 0, 2
        state = IngestionState(config=ws.config, chunks=make_chunks(5), show_progress_bar=False)
        result = nodes.generate_embeddings(state)
        check("batch succeeds after 2 failures (3 attempts total)",
              result["embeddings_generated"] == 5 and FlakyService.calls == 3, f"calls={FlakyService.calls}")
        check("exponential backoff waits 1s then 2s", sleeps == [1.0, 2.0], str(sleeps))
        check("no errors recorded when a retry succeeds", result["errors"] == [])

        sleeps.clear()
        FlakyService.calls, FlakyService.fail_first = 0, 99
        state = IngestionState(config=ws.config, chunks=make_chunks(5), show_progress_bar=False)
        result = nodes.generate_embeddings(state)
        check("gives up after exactly 3 attempts", FlakyService.calls == 3, f"calls={FlakyService.calls}")
        check("failed batch is skipped, not fatal", result["embeddings_generated"] == 0 and result["chunks"] == [])
        check("failure is recorded as an embedding error",
              any(e.get("stage") == "embedding" for e in result["errors"]))

        sleeps.clear()
        FlakyService.calls, FlakyService.fail_first = 0, 1
        state = IngestionState(config=ws.config, chunks=make_chunks(70), show_progress_bar=False)
        result = nodes.generate_embeddings(state)
        check("70 chunks are sent in batches of 32 (3 batches)",
              result["embeddings_generated"] == 70 and FlakyService.calls == 4, f"calls={FlakyService.calls}")
    finally:
        nodes.EmbeddingService = original
        time.sleep = real_sleep


def test_storage_and_change_detection(ws: Workspace, pdf: Path) -> None:
    section("7. Storage, all-or-nothing, and change detection")
    shutil.copy(pdf, ws.docs / "manual.pdf")

    # --- partial embedding failure: nothing stored, no hash left behind
    state = ws.run_pipeline(drop_chunks=1)
    check("loaded the document", state.documents_loaded == 1)
    check("incomplete document stores nothing", state.vectors_stored == 0)
    check("incomplete document is reported as a store error",
          any(e.get("stage") == "store" and e.get("file") == "manual.pdf" for e in state.errors))
    check("nothing is left in Chroma after a failed document", ws.store().collection.count() == 0)

    # --- retry succeeds
    state = ws.run_pipeline()
    total = len(state.chunks)
    check("failed document is retried on the next run (not skipped)", state.vectors_stored == total > 0)
    check("no store errors on a clean run", not [e for e in state.errors if e.get("stage") == "store"])

    store = ws.store()
    check("every chunk is in Chroma", store.collection.count() == total)
    by_type = {t: len(store.collection.get(where={"chunk_type": t}, include=[])["ids"])
               for t in ("text", "table", "diagram_reference")}
    check("chunk_type is persisted (text/table/diagram_reference)", all(v > 0 for v in by_type.values()), str(by_type))

    diagram_meta = store.collection.get(where={"chunk_type": "diagram_reference"}, limit=1, include=["metadatas"])["metadatas"][0]
    check("diagram_path and diagram_kind are persisted",
          Path(diagram_meta["diagram_path"]).exists() and diagram_meta["diagram_kind"] in ("raster", "vector_figure"))
    page2 = store.collection.get(
        where={"$and": [{"chunk_type": "diagram_reference"}, {"page_number": 2}]}, include=["metadatas"]
    )["metadatas"]
    check("diagram section path is persisted as a string",
          bool(page2) and all(m.get("section_path") == "3. WIRING" for m in page2),
          str([m.get("section_path") for m in page2]))
    text_paths = {m.get("section_path") for m in
                  store.collection.get(where={"chunk_type": "text"}, include=["metadatas"])["metadatas"]}
    check("text chunk section paths are persisted",
          {"1. INTRODUCTION", "3. WIRING"} <= text_paths, str(text_paths))
    sample = store.collection.get(limit=1, include=["metadatas"])["metadatas"][0]
    check("file_hash, ingested_at, total_chunks are persisted",
          len(sample["file_hash"]) == 64 and sample["ingested_at"] and sample["total_chunks"] == total)
    check("Chroma returns the hash for a stored document",
          store.get_stored_hash("manual.pdf") == DocumentHasher.calculate_file_hash(ws.docs / "manual.pdf"))
    check("Chroma returns None for an unknown document", store.get_stored_hash("nope.pdf") is None)

    # --- unchanged -> skipped
    state = ws.run_pipeline()
    check("unchanged document is skipped", state.documents_loaded == 0 and state.documents_skipped_duplicate == 1)
    check("skipping leaves stored data untouched", ws.store().collection.count() == total)

    # --- changed -> re-ingested and replaced
    build_fixture_pdf(ws.docs / "manual.pdf", extra_text="This sentence was added in a new revision.")
    state = ws.run_pipeline()
    check("changed document is re-ingested", state.documents_loaded == 1 and state.vectors_stored > 0)
    new_total = len(state.chunks)
    check("changed document replaces the old version (no leftovers)", ws.store().collection.count() == new_total)
    new_hash = ws.store().get_stored_hash("manual.pdf")
    check("stored hash is updated to the new file's hash",
          new_hash == DocumentHasher.calculate_file_hash(ws.docs / "manual.pdf"))

    # --- stale chunks from a longer old version are removed (duplicate ID fix)
    store = ws.store()
    store.collection.add(ids=[f"manual.pdf_99_{i}" for i in range(5)], embeddings=[list(FAKE_VECTOR)] * 5,
                         documents=["old"] * 5, metadatas=[{"source_filename": "manual.pdf"}] * 5)
    check("(setup) 5 stale chunks added", store.collection.count() == new_total + 5)
    ws.config.enable_change_detection = False
    state = ws.run_pipeline()
    check("re-ingesting an identical file does not error on duplicate IDs",
          state.vectors_stored == new_total and not [e for e in state.errors if e.get("stage") == "store"])
    check("stale chunks from an older version are removed", ws.store().collection.count() == new_total)
    ws.config.enable_change_detection = True

    # --- upsert: same IDs, new content
    store = ws.store()
    chunk = TextChunk(content="first", embedding=list(FAKE_VECTOR),
                      metadata=ChunkMetadata(source_filename="up.pdf", page_number=1, chunk_sequence=0, total_chunks=1))
    store.store_chunks([chunk])
    chunk.content = "second"
    check("storing the same ID twice does not raise", store.store_chunks([chunk]) == 1)
    check("same ID keeps the newest content (upsert)",
          store.collection.get(ids=["up.pdf_1_0"])["documents"] == ["second"])
    store.delete_document("up.pdf")

    # --- progress bars enabled must not break anything
    ws.config.enable_change_detection = False
    try:
        state = ws.run_pipeline(progress=True)
        check("pipeline runs with progress bars enabled", state.vectors_stored == new_total)
    except Exception as exc:  # pragma: no cover
        check("pipeline runs with progress bars enabled", False, repr(exc))
    ws.config.enable_change_detection = True


def test_document_management(ws: Workspace) -> None:
    section("8. Document management (list / delete by ID or filename)")
    import manage_documents as cli

    docs = ws.listing()
    check("list shows the stored document", len(docs) == 1 and docs[0]["name"] == "manual.pdf")
    entry = docs[0]
    check("list entry has id, chunk_count, ingested_at, file_hash",
          all(k in entry for k in ("id", "name", "chunk_count", "ingested_at", "file_hash")))
    check("list shows real timestamp and hash (not 'unknown')",
          entry["ingested_at"] != "unknown" and entry["file_hash"] != "unknown")
    check("list chunk_count matches Chroma", entry["chunk_count"] == ws.store().collection.count())

    check("document ID is 8 hex characters",
          len(entry["id"]) == 8 and all(c in "0123456789abcdef" for c in entry["id"]))
    check("document ID is stable for a filename", nodes.document_id("manual.pdf") == entry["id"])
    check("different filenames get different IDs", nodes.document_id("a.pdf") != nodes.document_id("b.pdf"))

    # list ordering with several documents
    store = ws.store()
    for name in ("zebra.pdf", "Alpha.pdf"):
        store.collection.add(ids=[f"{name}_1_0"], embeddings=[list(FAKE_VECTOR)], documents=["x"],
                             metadatas=[{"source_filename": name, "ingested_at": "2026-01-01T00:00:00", "file_hash": "h"}])
    names = [d["name"] for d in ws.listing()]
    check("list is sorted alphabetically (case-insensitive)", names == sorted(names, key=str.lower), str(names))

    # resolve_document: filename wins, then ID (case-insensitive)
    docs = ws.listing()
    zebra = next(d for d in docs if d["name"] == "zebra.pdf")
    check("resolve by exact filename", cli.resolve_document("zebra.pdf", docs)["name"] == "zebra.pdf")
    check("resolve by ID", cli.resolve_document(zebra["id"], docs)["name"] == "zebra.pdf")
    check("resolve by uppercase ID", cli.resolve_document(zebra["id"].upper(), docs)["name"] == "zebra.pdf")
    check("unknown identifier resolves to None", cli.resolve_document("deadbeef", docs) is None)

    # delete by filename through the node
    state = IngestionState(config=ws.config)
    result = nodes.delete_document(state, "zebra.pdf")
    check("delete by filename removes its chunks", result["deleted_vectors"] == 1 and not result["errors"])
    check("deleted document no longer listed", "zebra.pdf" not in [d["name"] for d in ws.listing()])
    result = nodes.delete_document(state, "ghost.pdf")
    check("deleting a missing document removes nothing and does not crash", result["deleted_vectors"] == 0)

    # CLI end to end (stdin answers the confirmation prompt)
    import io
    def run_cli(args: list[str], answer: str) -> tuple[int, str]:
        old_stdin, old_stdout, old_argv = sys.stdin, sys.stdout, sys.argv
        buf = io.StringIO()
        sys.stdin, sys.stdout, sys.argv = io.StringIO(answer + "\n"), buf, ["manage_documents.py", *args]
        old_env = {k: os.environ.get(k) for k in ("CHROMA_PERSIST_DIRECTORY", "CHROMA_COLLECTION_NAME")}
        os.environ["CHROMA_PERSIST_DIRECTORY"] = ws.config.chroma_persist_directory
        os.environ["CHROMA_COLLECTION_NAME"] = ws.config.chroma_collection_name
        try:
            code = cli.main()
        finally:
            sys.stdin, sys.stdout, sys.argv = old_stdin, old_stdout, old_argv
            for k, v in old_env.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return code, buf.getvalue()

    env_file_exists = (ROOT / ".env").exists()
    if env_file_exists:
        code, out = run_cli(["--list"], "")
        check("CLI --list prints an ID column", code == 0 and "ID" in out and entry["id"] in out, out[:200])
        code, out = run_cli(["--delete", "deadbeef"], "y")
        check("CLI --delete with an unknown ID exits 1", code == 1 and "no document" in out.lower())
        code, out = run_cli(["--delete", "Alpha.pdf"], "n")
        check("CLI --delete cancelled keeps the document",
              "cancelled" in out.lower() and "Alpha.pdf" in [d["name"] for d in ws.listing()])
        alpha_id = next(d["id"] for d in ws.listing() if d["name"] == "Alpha.pdf")
        code, out = run_cli(["--delete", alpha_id], "y")
        check("CLI --delete by ID removes the document",
              code == 0 and "Alpha.pdf" not in [d["name"] for d in ws.listing()], out[-200:])
        code, out = run_cli(["--delete", "manual.pdf"], "y")
        check("CLI --delete by filename removes the document", code == 0 and ws.listing() == [], out[-200:])
    else:
        print("  [SKIP] CLI end-to-end checks need a .env file in the project root")
        nodes.delete_document(state, "Alpha.pdf")
        nodes.delete_document(state, "manual.pdf")

    # after delete, the same file is ingested again (no stale hash blocks it)
    state = ws.run_pipeline()
    check("a deleted document is re-ingested on the next run", state.vectors_stored > 0 and state.documents_loaded == 1)


def test_live_database() -> None:
    section("9. Live database sanity check (--live)")
    try:
        config = load_config()
    except Exception as exc:
        check("configuration loads", False, str(exc))
        return
    store = ChromaVectorStore(config.chroma_persist_directory, config.chroma_collection_name)
    try:
        store.initialize_collection()
    except Exception as exc:
        check("connects to Chroma", False, str(exc))
        return
    total = store.collection.count()
    check("collection has chunks", total > 0, f"{total} chunks")
    if not total:
        return
    data = store.collection.get(include=["embeddings", "metadatas"], limit=500)
    check("embeddings are valid vectors",
          all(e is not None and len(e) > 0 for e in data["embeddings"]))
    required = ("source_filename", "page_number", "chunk_sequence", "file_hash", "ingested_at", "total_chunks", "chunk_type")
    missing = sum(1 for m in data["metadatas"] if any(k not in m for k in required))
    check("all chunks carry the current metadata", missing == 0,
          f"{missing} chunks are missing fields; delete and re-ingest those documents")
    docs = nodes.list_documents(IngestionState(config=config))["documents_in_store"]
    for d in docs:
        print(f"    {d['id']}  {d['name']}  ({d['chunk_count']} chunks)")


def test_nested_folders(pdf: Path) -> None:
    section("10. Subfolders and duplicate filenames")
    ws = Workspace()  # own database, so earlier documents don't interfere
    try:
        names = ["manual.pdf", "brand_a/manual.pdf", "brand_b/models/manual.pdf"]
        for name in names:
            (ws.docs / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(pdf, ws.docs / name)

        state = ws.run_pipeline()
        check("PDFs in subfolders load and parse", state.documents_loaded == 3 and not state.errors,
              str(state.errors))
        listed = {d["name"]: d["chunk_count"] for d in ws.listing()}
        check("each is stored under its relative path", sorted(listed) == sorted(names), str(listed))
        check("same-named files do not collide", len(set(listed.values())) == 1
              and ws.store().collection.count() == sum(listed.values()), str(listed))

        images = {}
        for name in names:
            metas = ws.store().collection.get(
                where={"$and": [{"source_filename": name}, {"chunk_type": "diagram_reference"}]},
                include=["metadatas"])["metadatas"]
            images[name] = {m["diagram_path"] for m in metas}
        check("each document keeps its own diagram files",
              all(images.values()) and all(Path(p).exists() for s in images.values() for p in s)
              and len(set().union(*images.values())) == sum(len(s) for s in images.values()), str(images))

        state = ws.run_pipeline()
        check("unchanged nested documents are skipped",
              state.documents_loaded == 0 and state.documents_skipped_duplicate == 3)

        nested = str(ws.docs / "brand_a" / "manual.pdf")
        check("a file path maps to its stored name",
              nodes.stored_document_name(nested, ws.config.document_input_dir) == "brand_a/manual.pdf")
        result = nodes.update_document(IngestionState(config=ws.config), nested)
        check("updating one file removes only its chunks",
              result["deleted_vectors"] == listed["brand_a/manual.pdf"]
              and sorted(d["name"] for d in ws.listing()) == sorted(n for n in names if n != "brand_a/manual.pdf"))
    finally:
        ws.close()


def test_media_files(pdf: Path) -> None:
    section("11. Standalone images and videos")
    ws = Workspace()  # own database and diagram folder
    try:
        png = ws.root / "photo.png"
        pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 60), False).save(png)
        image_name = "Dometic/Fridges/Americana measure for squareness.PNG"
        video_name = "Airxcel/Zone Control/Testing Non Zone Thermostat.mp4"
        for name, data in [(image_name, png.read_bytes()), (video_name, b"not really a video"),
                           ("Airxcel/old video.wmv", b"skipped"), ("manual.png", png.read_bytes())]:
            (ws.docs / name).parent.mkdir(parents=True, exist_ok=True)
            (ws.docs / name).write_bytes(data)
        shutil.copy(pdf, ws.docs / "manual.pdf")  # same stem as manual.png

        state = ws.run_pipeline()
        listed = {d["name"] for d in ws.listing()}
        check("images and mp4 load, .wmv is ignored",
              listed == {image_name, video_name, "manual.png", "manual.pdf"} and not state.errors,
              f"{sorted(listed)} {state.errors}")

        def chunks_of(name: str) -> list[tuple[str, dict]]:
            got = ws.store().collection.get(where={"source_filename": name}, include=["documents", "metadatas"])
            return list(zip(got["documents"], got["metadatas"]))

        image = chunks_of(image_name)
        text, meta = image[0] if image else ("", {})
        check("an image is one diagram_reference chunk of kind image_file",
              len(image) == 1 and meta.get("chunk_type") == "diagram_reference"
              and meta.get("diagram_kind") == "image_file" and meta.get("page_number") == 1, str(image))
        check("the image chunk names the file and its folder",
              text.startswith("Image: Americana measure for squareness.PNG")
              and "Dometic > Fridges" in text, text)
        stored = Path(meta.get("diagram_path", ""))
        check("the image is copied unchanged into the diagram folder",
              stored.parent == ws.diagrams and stored.is_file() and stored.read_bytes() == png.read_bytes()
              and stored.suffix == ".png", str(stored))

        video = chunks_of(video_name)
        text, meta = video[0] if video else ("", {})
        check("a video is one text chunk naming it and its folder",
              len(video) == 1 and meta.get("chunk_type") == "text" and meta.get("page_number") == 1
              and "Video: Testing Non Zone Thermostat" in text and "Folder: Airxcel > Zone Control" in text,
              str(video))

        pdf_diagrams = {m["diagram_path"] for _, m in chunks_of("manual.pdf") if m["chunk_type"] == "diagram_reference"}
        check("manual.png does not remove manual.pdf's diagrams",
              pdf_diagrams and all(Path(p).is_file() for p in pdf_diagrams), str(pdf_diagrams))

        before = sorted(p.name for p in ws.diagrams.iterdir())
        nodes.parse_documents(IngestionState(config=ws.config, documents=state.documents))
        check("parsing an image again replaces its file instead of adding one",
              sorted(p.name for p in ws.diagrams.iterdir()) == before)

        state = ws.run_pipeline()
        check("unchanged images and videos are skipped",
              state.documents_loaded == 0 and state.documents_skipped_duplicate == 4)

        # Web links: only PDFs open at a page; other types are not linked or served
        from flask import Flask
        from web.app import _source_with_url
        app = Flask(__name__)
        app.add_url_rule("/manuals/<path:filename>", "serve_manual", lambda filename: "")
        with app.test_request_context():
            v = _source_with_url({"filename": video_name, "page": 1}, ws.docs)
            p = _source_with_url({"filename": "manual.pdf", "page": 2}, ws.docs)
            w = _source_with_url({"filename": "Airxcel/old video.wmv", "page": 1}, ws.docs)
        check("a video source links to the file with no page",
              v.get("media") == "video" and v.get("url", "").endswith(".mp4"), str(v))
        check("a PDF source still opens at its page",
              p.get("media") == "pdf" and p.get("url", "").endswith("#page=2"), str(p))
        check("a .wmv source gets no link", "url" not in w, str(w))
    finally:
        ws.close()


# --------------------------------------------------------------------------
def main() -> int:
    print("=" * 70)
    print("INGESTION PIPELINE VERIFICATION")
    print("=" * 70)

    os.chdir(ROOT)  # load_config() and the CLI resolve .env relative to the working directory
    ws = Workspace()
    try:
        pdf = ws.root / "fixture.pdf"
        build_fixture_pdf(pdf)

        test_config()
        test_hashing_and_headings(ws, pdf)
        test_tables(pdf)
        test_diagrams(ws, pdf)
        test_chunking(ws, pdf)
        test_layout(ws)
        test_real_headings(ws, show="--headings" in sys.argv)
        test_retry(ws)
        test_storage_and_change_detection(ws, pdf)
        test_document_management(ws)
        test_nested_folders(pdf)
        test_media_files(pdf)
        if "--live" in sys.argv:
            test_live_database()
    finally:
        ws.close()

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = [(n, d) for n, ok, d in _results if not ok]
    print("\n" + "=" * 70)
    if failed:
        print(f"VERIFICATION FAILED: {passed}/{len(_results)} checks passed")
        for name, detail in failed:
            print(f"  - {name}" + (f" ({detail})" if detail else ""))
    else:
        print(f"VERIFICATION PASSED: {passed}/{len(_results)} checks passed")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
