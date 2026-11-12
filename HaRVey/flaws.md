# Known Flaws

Issues found in the ingestion pipeline while testing against `Dometic 457915 Installation & Operating Instructions Manual1.pdf`. Page numbers are 1-indexed (as shown in the PDF).

## Diagrams parsed without captions (no chunk created)

A diagram with no caption has nothing to search on, so no `diagram_reference` chunk is created for it. The image file is still saved to `extracted_diagrams/` and kept in the document's parsed metadata.

**Embedded raster images (no text available to caption them):**
- Page 1: `..._p0_img0.jpg` (560x560 cover photo)
- Page 11: `..._p10_img0.png` (232x135 image)

**Vector figures with no text inside the region:**
- Page 17: two figures (`..._p16_fig*.png`)
- Page 18: one figure (`..._p17_fig*.png`)

## Diagram extraction

- **Non-diagrams detected as figures.** PyMuPDF groups any cluster of vector drawing as a figure. Seen on this manual:
  - Page 4: info/title boxes ("RECORD THIS INFORMATION FOR FUTURE REFERENCE", service office address).
  - Page 11: a table region ("table below. See (FIG. 11)...").
  - Page 19: the "Other ManualsLib Projects" banner.
  These get chunks because they have captions.
- **Merged figures.** Figures that sit close together may be cropped as one image. Accepted for now.
- **Caption is raw text from the region,** truncated to 300 characters. It can include stray labels and run-on text.

## Tables

- **`TABLE_MIN_CONFIDENCE` does nothing.** pdfplumber has no confidence score. The setting is read from `.env` but never used. Table quality is decided by the junk filter in `TextChunker.is_meaningful_table` instead.
- **Table text is duplicated.** A kept table is embedded as a `table` chunk and its text also appears inside the regular text chunks. Intentional, but it can return two hits for one table.
- **Table filter thresholds are tuned to one manual.** It requires at least 2 rows, 2 columns, 6 cells with text and 60% of cells filled. 28 tables were found and 5 kept. Other PDFs may need different thresholds.
- **Whole tables are never split.** The largest table here is about 2,150 characters, far above `CHUNK_SIZE` (512). Very large tables could exceed the embedding model's input limit.

## Query side

- `src/query/nodes.py` reads only `source_filename`, `page_number` and `chunk_sequence` from Chroma. `chunk_type`, `diagram_path` and `diagram_kind` are stored but not passed through, so diagram chunks come back as ordinary results. Needed for the diagram-lookup tool (see `TODO`).

## Data and cleanup

- **Existing Chroma data is out of date.** Chunks ingested before the hash/metadata change have no `file_hash`, `ingested_at` or `chunk_type`. Delete and re-ingest those documents.
- **Stale diagram files.** `extracted_diagrams/` still contains files from the old extractor (for example `..._Manual_p18_img0.png`, which are unreadable). Cleanup only removes files matching the current document's name, so these stay until deleted by hand.
- **`.document_hashes.json` is unused.** Change detection now reads the hash from Chroma. The file can be deleted.
- **Exploration clutter in the repo root.** `INDEX.md`, `EXPLORATION_SUMMARY.md`, `ingestion_architecture_summary.txt` and `ingestion_exploration_report.md` were generated during the initial exploration and are not part of the project.

## Code and docs

- **Page index mismatch between models.** `TableData.page` and `DiagramMetadata.page` are 0-indexed while `ChunkMetadata.page_number` is 1-indexed. `TextChunker` converts them, but anything else reading the parsed models must do the same.
- **`update_document` does not update.** It deletes the old vectors and tells you to re-run ingestion. The name suggests it re-ingests.
- **README install and config instructions are wrong.** It says `pip install -r src/requirements.txt` (the file is `requirements.txt` in the project root) and `cp .env.example .env` (the template is `example.env.example`).
- **`requirments/ingestion.md` still references `/home/michael24/4300-CS/U0-HW-09`** in its troubleshooting section.
- **The `tests/` scripts were not updated** for the new chunk metadata or chunk types.
