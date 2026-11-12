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
  - Embedding retry with backoff (fake service)
  - Storage: metadata persisted, all-or-nothing, rollback, upsert, replacement
  - Change detection (skip unchanged, re-ingest changed, failed docs retried)
  - Document management: IDs, list, delete by ID / filename, CLI resolution
  - Progress bars do not break the pipeline

Usage:
    python tests/verify_ingestion.py            # run all checks
    python tests/verify_ingestion.py --live     # also sanity-check your real Chroma DB
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
from src.ingestion.tools import (  # noqa: E402
    ChromaVectorStore,
    DiagramExtractor,
    DocumentHasher,
    HeadingExtractor,
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
              and cfg.show_progress_bar is True and cfg.chunk_size == 512 and cfg.chunk_overlap == 50)
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
    section("2. File hashing and heading extraction")
    h1 = DocumentHasher.calculate_file_hash(pdf)
    check("hash is a 64-char SHA256 hex string", len(h1) == 64 and all(c in "0123456789abcdef" for c in h1))
    check("hash is deterministic", h1 == DocumentHasher.calculate_file_hash(pdf))

    other = ws.root / "other.pdf"
    build_fixture_pdf(other, extra_text="changed")
    check("hash changes when file content changes", h1 != DocumentHasher.calculate_file_hash(other))
    check("hash store methods are gone (Chroma is the source of truth)",
          not hasattr(DocumentHasher, "store_hash") and not hasattr(DocumentHasher, "is_document_changed"))

    text = "# Overview\nbody\n1.1 Scope\nbody\nINSTALLATION STEPS\nbody\nTitle\n=====\nINSTALLATION STEPS\n"
    headings = HeadingExtractor.extract_headings(text)
    check("detects markdown, numbered, ALL CAPS and underlined headings",
          {"Overview", "1.1 Scope", "INSTALLATION STEPS", "Title"} <= set(headings), str(headings))
    check("numbered and ALL CAPS headings are found on lines that have a following line",
          "1.1 Scope" in headings and "INSTALLATION STEPS" in headings)
    check("duplicate headings are removed, order preserved",
          headings.count("INSTALLATION STEPS") == 1 and headings.index("Overview") < headings.index("Title"))


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

    table_chunk = next(c for c in chunks if c.metadata.chunk_type == "table")
    check("table chunk is whole (all rows present)", all(x in table_chunk.content for x in ("A100", "B200", "C300")))
    check("table chunk has context prefix and 1-indexed page",
          table_chunk.content.startswith("Table from ") and table_chunk.metadata.page_number == 1)
    check("table chunk records its shape",
          (table_chunk.metadata.table_rows, table_chunk.metadata.table_cols) == (4, 3))

    diagram_chunks = [c for c in chunks if c.metadata.chunk_type == "diagram_reference"]
    check("diagram chunks only exist for captioned figures",
          len(diagram_chunks) == sum(1 for d in doc.diagrams if d.caption.strip()))
    check("uncaptioned diagrams get no chunk (but stay in parsed metadata)",
          any(not d.caption.strip() for d in doc.diagrams)
          and len(diagram_chunks) < len(doc.diagrams))
    wiring = next((c for c in diagram_chunks if "Wiring Diagram" in c.content), None)
    check("diagram chunk text carries the caption", wiring is not None)
    if wiring:
        check("diagram chunk page is 1-indexed", wiring.metadata.page_number == 2)
        check("diagram chunk keeps image path and kind",
              Path(wiring.metadata.diagram_path).exists() and wiring.metadata.diagram_kind == "vector_figure")
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
        test_retry(ws)
        test_storage_and_change_detection(ws, pdf)
        test_document_management(ws)
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
