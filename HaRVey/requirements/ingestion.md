# Vector Ingestion Pipeline

This document describes the LangGraph-based ingestion pipeline. It processes PDF documents, plus standalone image and video files, and turns them into embeddings stored in a vector database. The pipeline also handles table extraction, diagram detection, change tracking, and managing documents after ingestion.

## Overview

The ingestion pipeline reads documents from a local folder. It extracts text, tables and diagrams, splits the text into chunks, generates embeddings with automatic retries, and saves everything to Chroma for retrieval. Change detection skips files that haven't changed, and documents can be added, deleted and updated after ingestion.

Besides PDFs, the manual collection includes some standalone images (`.jpg`, `.jpeg`, `.png`) and videos (`.mp4`). These are ingested as one-page documents. With no text to read, they are found by their folder path and filename (see [Standalone images and videos](#standalone-images-and-videos)).

### Architecture Diagram

```
PDF, image and video files
    ↓
┌─────────────────────────┐
│  load_documents         │ Discover PDFs, images, videos;
│                         │ calculate hashes (skip unchanged)
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  parse_documents        │ Extract text, tables, diagrams
│                         │ (pypdf + pdfplumber + PyMuPDF)
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  extract_metadata       │ Layout analysis: headings, reading order,
│                         │ sections, diagram/table section paths
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  chunk_text             │ Split each section + create table & diagram
│                         │ chunks (every chunk has a "Section:" line)
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  generate_embeddings    │ Embed with retry logic (3 retries)
│                         │ (exponential backoff on failure)
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  store_vectors          │ Persist to Chroma database
└─────────────┬───────────┘
              ↓
        Pipeline Complete
              ↓
┌─────────────────────────┐
│  list_documents         │ Query ingested documents
├─────────────────────────┤
│  delete_document        │ Remove document + vectors
├─────────────────────────┤
│  update_document        │ Re-ingest changed document
└─────────────────────────┘
```

---

## Nodes

### 1. load_documents

**Purpose**: Discover and load PDF, image and video files from the configured input directory.

**Inputs**: None (reads from `DOCUMENT_INPUT_DIR`)

**Outputs**: List of Document objects with file metadata

**Responsibilities**:
- Scan `./documents/` and its subfolders for every file type the pipeline reads: `.pdf`, `.jpg`, `.jpeg`, `.png` and `.mp4`, in any letter case. Other files are ignored, including `.wmv` (most browsers can't play it)
- Extract document-level metadata (filename, page count, timestamps). Images and videos get `total_pages=1` and no PDF metadata
- Validate file readability
- Handle missing or empty directories gracefully (skip and continue pattern)

**Key Functions**:
- Uses `Path.rglob("*")` filtered by `media_kind()` (`src/ingestion/tools.py`), which returns `"pdf"`, `"image"`, `"video"` or `None` from the file extension
- Creates Document objects with metadata (`PDFLoader._load_single_document`, which handles images and videos too)
- Logs documents found and processing status

---

### 2. parse_documents

**Purpose**: Extract text content, tables, and diagrams from PDF files.

**Inputs**: List of Document objects with file hashes

**Outputs**: ParsedDocument objects with text, tables, and diagram metadata

**Responsibilities**:
- Extract text from each PDF page using pypdf. This text is a fallback: `extract_metadata` replaces it with layout-ordered text when layout analysis succeeds
- Extract structured table data and each table's position (bbox) using pdfplumber (if enabled)
- Extract diagrams as viewable image files into `extracted_diagrams/` (see Diagram Extraction below)
- Preserve page boundaries and text structure
- Extract optional metadata (author, creation date) from PDF properties
- Handle parsing errors gracefully without stopping the pipeline
- Track table and diagram counts in metadata

**Configuration**:
- `ENABLE_TABLE_EXTRACTION=true` - Extract tables as structured data

**Key Functions**:
- Uses `pypdf` library for fallback text extraction
- Uses `pdfplumber` for table detection and extraction
- Uses `PyMuPDF` for diagram extraction (`pypdf` for raster images only if PyMuPDF is missing)
- Maintains per-page text organization
- Handles encoding issues and malformed PDFs. Text stored in fonts whose character codes are shifted by 29 (e.g. `7+(50267$7` for `THERMOSTAT`) is detected per font and page, and decoded (`TextRepair` in `src/ingestion/layout.py`)

**Diagram Extraction**:
- Embedded raster images are extracted with PyMuPDF as `{stem}_p{page}_img{N}.{ext}`, together with their position on the page. Images under 50px on either side are dropped
- Vector drawings (which pypdf cannot see) are found with PyMuPDF `cluster_drawings()`, then each region is rendered at 150 dpi to `{stem}_p{page}_fig{N}.png`. Regions under 40pt are ignored
- Each `DiagramMetadata` records `page`, `image_path`, `type` (file format), `kind` (`raster` or `vector_figure`), `bbox`, and a `caption` built from the text inside the figure region (e.g. `FIG. 5 Roof Opening Dimensions Are Nominal`). Shifted-font text is decoded and overprinted (fake bold) text is read once. Captions that are still unreadable are left empty
- `heading_path`, `label` and `context` are filled in later by `extract_metadata`
- Figures found close together may merge into one crop
- Previously extracted files for a document are deleted before re-extracting, so re-ingesting never leaves stale images

#### Standalone images and videos

Image and video files skip the PDF parsing above. `_parse_media` (`src/ingestion/nodes.py`) handles them instead:

- **Image** (`.jpg`, `.jpeg`, `.png`): `DiagramExtractor.store_image_file` copies the file unchanged into `extracted_diagrams/` as `{stem}-{hash}_p0_img1.{ext}`. It uses the same naming scheme as extracted images, so re-ingesting replaces the file instead of adding another. Standalone images always get the path hash in the name, even at the top level, so `a.png` can never delete the diagrams of `a.pdf`. The document has no text and one `DiagramMetadata`:
  - `kind="image_file"`, `page=0`
  - `label`: the title built from the filename (e.g. `Americana measure for squareness`)
  - `context`: `Image in folder Dometic > Guides > Refrigerators > Americana`
- **Video** (`.mp4`): one page of text naming the video and its folder, chunked as ordinary text. There are no tables or diagrams:
  ```
  Video: Testing Non Zone Thermostat
  Folder: Airxcel > Airxcel Coleman Mach > Zone Control
  ```

Neither type is OCR'd, transcribed or described by a vision model. Only the folder path and filename are searchable, so this works because the collection's media files have descriptive names. On the query side:
- The LLM shows a standalone image in the diagram viewer like any other diagram, but only when its title clearly matches the topic.
- The LLM cites a matching video as a source. The web app links it through `/manuals/<path>`, which serves PDFs, images and mp4s and supports seeking within videos.

---

### 3. extract_metadata

**Purpose**: Attach enriched metadata including document structure and content analysis.

**Inputs**: ParsedDocument objects with content, tables, and diagrams

**Outputs**: ParsedDocument objects with comprehensive metadata

**Responsibilities**:
- Extract creation date and author from PDF metadata
- Run layout analysis (`analyze_document` in `src/ingestion/layout.py`) to get the section structure:
  - **Reading order**: text lines with font size, bold and position, ordered column-aware. Blocks are split at the page middle, and large headings and full-width blocks start a new band
  - **Headings** (`FontHeadingDetector`): lines larger than body text, or bold and heading-shaped (ALL CAPS, `A. ...` labels, short title case). Rejected: running headers/footers (same small text on 30%+ of pages), table-of-contents regions, text inside tables or figures, NOTE/WARNING/CAUTION callouts, bold paragraphs, part numbers. Levels come from ranking font styles by size
  - **Sections**: the text between one heading and the next, with its heading path (e.g. `MAINTENANCE > A. Air Filter`). A section title repeated at the top of a continuation page keeps the current sub-section
  - **Diagrams**: each gets its `heading_path`, a nearby `FIG. N` `label`, and `context` (up to 500 characters of text back to the previous heading, keeping the part nearest the figure)
  - **Tables**: each meaningful table gets the `heading_path` at its position
- Replace the pypdf text with the layout text (repeated headers/footers removed). pypdf text is kept only if layout analysis finds no text
- Add processing/extraction timestamp
- Ensure metadata completeness for downstream filtering
- Skip documents with missing optional metadata (don't fail the pipeline)
- Track page count and heading hierarchy

**Key Functions**:
- Extracts PDF properties via pypdf
- Detects section headings from font styling (PyMuPDF is required). If layout analysis fails, the document gets no headings, an error is recorded, and `chunk_text` chunks it page by page
- Generates processing timestamps
- Validates metadata availability
- `python tests/verify_ingestion.py --headings` prints every heading detected in `documents/` for review
- Images and videos skip layout analysis and PDF metadata. They get `page_count=1` and an extraction timestamp

---

### 4. chunk_text

**Purpose**: Split document text into chunks, with special handling for tables and diagrams.

**Inputs**: ParsedDocument objects with content, tables, and diagrams

**Outputs**: TextChunk objects with chunk-type and source metadata

**Responsibilities**:
- Split each section separately with LangChain's `RecursiveCharacterTextSplitter`, so no chunk mixes two sections
- Start every chunk with a `Section: A > B` line. It is embedded with the text and seen by the LLM
- Create separate chunks for table content (preserves structure)
- Create reference chunks for every diagram (links to extracted images)
- Apply configured chunk size (default: 1000 characters, including the section line) and overlap (default: 100 characters)
- Without layout sections (layout analysis failed or found no text), treat each page as one section with no heading path, so chunks keep correct page numbers
- Map chunks back to original pages and document sources
- Store the heading path in `section_headings` (Chroma: `section_path`, joined with ` > `)
- Label content before the first heading with the document title, built from the filename (e.g. `Dometic 457915 Installation & Operating Instructions Manual1`)
- Extract part numbers into `part_numbers` on every chunk (see Part numbers below)
- Skip documents that produce no valid chunks (with logging)

**Part numbers** (`TextChunker.chunk_all`, `src/utils/part_numbers.py`): every chunk, of all three types, gets `ChunkMetadata.part_numbers`, the normalized model / part / fault-code keys used by hybrid search for exact matching (see [hybrid_search.md](hybrid_search.md)):
- Keys from the chunk text (`part_keys`): each code (`457915.70X`, `8330-733`, `CCC2`, `E1`) normalized to uppercase without `.`/`-` (`45791570X`, `8330733`), plus its model family (`457915`). Decimals, units (`12VDC`), fractions, ranges and words like `9-Pin` are not extracted
- Plus the keys in the manual's filename (`filename_part_keys`: `CCC2-Operating-Instructions.pdf` → `CCC2`, `Dometic 457915 ... Manual1.pdf` → `457915`), so every chunk of a manual carries its model number
- Stored in Chroma as one delimited string, `part_numbers="|45791570X|457915|"` (metadata values must be scalars); `""` when there are none
- On the current corpus, 448 distinct codes; part-number heading candidates are still rejected by `FontHeadingDetector`, the codes only go to metadata

**Configuration**:
- Chunk size and overlap are tunable via `.env` variables
- Parameters: `CHUNK_SIZE=1000`, `CHUNK_OVERLAP=100`
- Changing them requires a re-ingest with `ENABLE_CHANGE_DETECTION=false`, because unchanged PDFs are otherwise skipped

**Chunk Types** (`chunk_sequence` runs continuously across all three, so chunk IDs stay unique):
- `text` - Section text, split to fit `CHUNK_SIZE`. Content is the `Section:` line followed by the text. Each video is one `text` chunk whose text starts with `Video:`
- `table` - One chunk per meaningful table, never split. Content is `Table from {file}, page N:`, the `Section:` line, then the markdown table. A strict filter drops junk tables (drawn boxes, empty grids, labels): a table needs at least 2 rows, 2 columns, 6 cells with text, and 60% of cells filled
- `diagram_reference` - One per diagram, captioned or not. Content lists the page, `Section:`, `Label:` (a nearby `FIG. N` line), `Text in figure:` (caption) and `Context:`, skipping empty parts. Metadata holds `diagram_path` and `diagram_kind` (`raster`, `vector_figure`, or `image_file` for a standalone image). A standalone image is the document's only chunk, and its first line is `Image: {filename}` instead of the page line

Pages in all chunk metadata are 1-indexed. Each chunk also stores `file_hash`, `ingested_at`, `total_chunks` (used by `store_vectors` as a completeness check) and `part_numbers`.

**Example chunks** (Dometic manual, PDF page 16, a two-column page):

`text`:
```
Section: MAINTENANCE > A. Air Filter
A. Air Filter
1. Periodically (a minimum of every 2 weeks of op-
eration) remove the return air filter located behind
the return air grille and wash it with soap and
warm water, let dry and then reinstall.
NEVER run unit without return air filter in
place. This will plug the unit evaporator coil
with dirt and may substantially degrade the
performance of the unit over time.
```
Metadata: `chunk_type="text"`, `page_number=16`, `section_path="MAINTENANCE > A. Air Filter"`

`table` (PDF page 7, shortened; cells keep their line breaks from the PDF):
```
Table from Dometic 457915 Installation & Operating Instructions Manual1.pdf, page 7:
Section: SPECIFICATIONS > A. Table - Unit Data
| Model No. | Nominal
Capacity
(BTU HR)
Cooling | Electrical
Rating
120 Vac
60 Hz 1 ph | Compressor ...
```
Metadata: `chunk_type="table"`, `page_number=7`, `section_path="SPECIFICATIONS > A. Table - Unit Data"`, `part_numbers="|45791570X|457915|64031280X|640312|..."`

`diagram_reference` (PDF page 18, wiring diagram whose labels were stored in a shifted font, shortened):
```
Figure on page 18 of Dometic 457915 Installation & Operating Instructions Manual1.pdf
Section: WIRING DIAGRAMS > B. Air Distribution Box Wiring Diagram
Text in figure: ELEC HEAT H,6 C,4 L,3 THERMOSTAT 314841.000 WHT BLK ... FACTORY WIRING ...
Context: 115 VAC FIELD WIRING
```
Metadata: `chunk_type="diagram_reference"`, `diagram_path=".../..._p17_fig1.png"`, `diagram_kind="vector_figure"`

`diagram_reference` for a standalone image (`Dometic/Guides/Refrigerators/Americana/Americana measure for squareness.jpg`):
```
Image: Americana measure for squareness.jpg
Section: Americana measure for squareness
Label: Americana measure for squareness
Context: Image in folder Dometic > Guides > Refrigerators > Americana
```
Metadata: `chunk_type="diagram_reference"`, `page_number=1`, `diagram_path=".../Americana measure for squareness-{hash}_p0_img1.jpg"`, `diagram_kind="image_file"`

`text` for a video (`Airxcel/Airxcel Coleman Mach/Zone Control/Testing Non Zone Thermostat.mp4`):
```
Section: Testing Non Zone Thermostat
Video: Testing Non Zone Thermostat
Folder: Airxcel > Airxcel Coleman Mach > Zone Control
```
Metadata: `chunk_type="text"`, `page_number=1`

**Key Functions**:
- Creates metadata for each chunk including page, type, and sequence
- Preserves document context through overlap
- Validates chunk content before inclusion
- Tracks section headings in chunk context

---

### 5. generate_embeddings

**Purpose**: Convert text chunks into vector embeddings with automatic retry logic.

**Inputs**: TextChunk objects

**Outputs**: TextChunk objects with embedding vectors attached

**Responsibilities**:
- Call embedding service API with batch requests (32 chunks per batch for efficiency)
- Implement exponential backoff retry logic (3 retries, 2^attempt seconds)
- Handle API errors and timeouts gracefully
- Skip chunks that fail embedding after all retries (with logging)
- Validate response format and vector dimensions
- Show progress bar with real-time processing updates

**Configuration**:
- Service URL: `EMBEDDING_BASE_URL`
- Model: `EMBEDDING_MODEL` (e.g., Qwen3-Embedding-8B)
- Authentication: API key from `EMBEDDING_API_KEY`
- Timeout: 30 seconds per request
- Retry settings: `EMBEDDING_MAX_RETRIES=3`, `EMBEDDING_RETRY_BACKOFF_BASE=2`
- Progress: `SHOW_PROGRESS_BAR=true`

**Key Functions**:
- Uses HTTP requests for API communication
- Implements exponential backoff retry (1s, 2s, 4s waits)
- Batch processing for efficiency
- Real-time progress tracking
- Error handling maintains pipeline continuity

---

### 6. store_vectors

**Purpose**: Persist embedding vectors and metadata to Chroma vector database.

**Inputs**: TextChunk objects with embeddings

**Outputs**: Confirmation of storage completion

**Responsibilities**:
- Initialize Chroma collection if it doesn't exist
- Create unique IDs for each chunk (`{filename}_{page}_{chunk_seq}`)
- Store vectors with full metadata (source, page, content, `chunk_type`, `section_path`, `diagram_path`, `diagram_kind`, `part_numbers`)
- Enable metadata-based filtering (by document, by page) and exact part-number lookup (the query side builds its BM25 / part-number index from the stored documents and `part_numbers`)
- Handle storage errors gracefully (skip and continue)

**Configuration**:
- Database: Chroma
- Persist directory: `CHROMA_PERSIST_DIRECTORY` (default: `./chroma_db/`)
- Collection name: `CHROMA_COLLECTION_NAME` (default: `vector_ingestion_collection`)
- Distance metric: Cosine similarity

**Key Functions**:
- Validates chunk embeddings before storage
- Batches storage operations for efficiency
- Maintains persistent storage across sessions

---

## State Management

The pipeline uses a central `IngestionState` object that flows through all nodes:

```python
@dataclass
class IngestionState:
    # Core data
    documents: list[Document]              # Original documents with hashes
    chunks: list[TextChunk]                # Text chunks with embeddings
    errors: list[dict]                     # Accumulated errors
    config: IngestionConfig                # Configuration reference
    status: str                            # Pipeline status
    
    # Progress counters
    documents_loaded: int
    documents_parsed: int
    chunks_created: int
    embeddings_generated: int
    vectors_stored: int
    
    # New: Enhanced tracking
    documents_skipped_duplicate: int       # Unchanged documents skipped
    tables_extracted: int                  # Tables found and extracted
    diagrams_found: int                    # Diagrams detected
    batches_re_embedded: int               # Batches processed with retry
```

Each node receives this state, processes its data, and returns updated state for the next node. This enables:
- Real-time progress tracking throughout the pipeline
- Change detection (skip re-processing unchanged documents)
- Error accumulation without stopping
- Configuration availability in all nodes
- Clear data flow and dependency management
- Metrics for table and diagram extraction

---

## Error Handling Pattern

All nodes implement a **skip and continue** error handling strategy:

1. Errors are caught at the node level
2. Failed items are logged with descriptive error messages
3. Errors are accumulated in `state.errors`
4. Processing continues with remaining items
5. Final summary includes error count and details

**Example**: If 10 chunks fail embedding but 90 succeed:
- 90 chunks are returned successfully embedded
- 10 failures are logged and added to errors list
- Pipeline continues to storage step with 90 vectors
- Final summary shows "10 embedding failures encountered"

---

## Configuration

All pipeline behavior is controlled via environment variables in `.env`:

```bash
# Embedding Service
EMBEDDING_BASE_URL=http://golem:4000/v1
EMBEDDING_MODEL=Qwen3-Embedding-8B
EMBEDDING_API_KEY=your_api_key

# Retry Logic
EMBEDDING_MAX_RETRIES=3                  # Max retry attempts
EMBEDDING_RETRY_BACKOFF_BASE=2           # Exponential backoff (2^attempt)

# Table Extraction
ENABLE_TABLE_EXTRACTION=true              # Extract tables as structured data

# Change Detection
ENABLE_CHANGE_DETECTION=true              # Skip unchanged documents
CHANGE_DETECTION_LOG_LEVEL=critical       # Log level for warnings

# Progress Tracking
SHOW_PROGRESS_BAR=true                    # Display progress bars

# LLM Service (optional, for query pipeline)
LLM_BASE_URL=http://golem:4000/v1
LLM_MODEL=Qwen3.8-Flash-Next
LLM_API_KEY=your_api_key

# Chunking
CHUNK_SIZE=1000
CHUNK_OVERLAP=100

# Vector Storage
CHROMA_PERSIST_DIRECTORY=./chroma_db
CHROMA_COLLECTION_NAME=vector_ingestion_collection

# Document Input
DOCUMENT_INPUT_DIR=./documents
```

---

## Running the Pipeline

### Standard Pipeline Execution

```bash
# Navigate to project
cd /home/michael24/4300-CS/HaRVey

# Activate virtual environment
source .venv/bin/activate

# Run ingestion pipeline
python ingestion_main.py
```

**Expected Output**:
- Configuration loads successfully
- All 6 nodes execute in sequence with progress bars
- Summary displays statistics:
  - Documents loaded (and number skipped as unchanged)
  - Documents parsed
  - Tables extracted
  - Diagrams found
  - Chunks created
  - Embeddings generated (with retry counts)
  - Vectors stored
  - Any errors encountered

**Change Detection**:
- Source of truth is Chroma: every chunk stores `file_hash`, `ingested_at` and `total_chunks` in its metadata (no separate hash file)
- On first run: All documents processed and stored with their file hash
- On subsequent runs: a document is skipped when its hash already in Chroma matches the file
- Documents are stored all-or-nothing in `store_vectors`: if any chunk fails embedding or storage, the document is not saved (or is rolled back), so it is retried on the next run
- Unchanged document log: `"Skipping unchanged document: example.pdf"`
- To disable: Set `ENABLE_CHANGE_DETECTION=false` in `.env`

---

## Key Design Decisions

✅ **Synchronous Execution** - All operations are synchronous (no async/await)

✅ **Skip & Continue** - Pipeline maximizes data processing despite individual failures

✅ **High-Level Logging** - Only logs node entry/exit and summary stats, no verbose noise

✅ **Full Type Hints** - All functions and data structures are fully type-annotated

✅ **Batch Processing** - Embeddings processed in batches of 32 for efficiency

✅ **Persistent Storage** - Vectors stored persistently for retrieval across sessions

✅ **Automatic Retry** - Exponential backoff retry (3 attempts) for embedding API calls

✅ **Change Detection** - File hash-based change tracking prevents re-processing unchanged documents

✅ **Table Awareness** - Tables extracted as structured data and embedded separately

✅ **Diagram Support** - Diagram detection and image extraction. Every diagram is searchable by its section path, label, figure text and context

✅ **Standalone Media** - Image files are stored as diagrams, and mp4 videos as text chunks the answer can link to. Both are found by folder path and filename only

✅ **Section Awareness** - Font-based heading detection, column-aware reading order, and a section path on every chunk

✅ **Part-Number Metadata** - Model, part and fault codes extracted from each chunk and its filename, for exact matching in hybrid search

✅ **Progress Visibility** - Per-node progress bars show real-time advancement

---

## Extending the Pipeline

To add a new processing step:

1. Create a new node function in `src/ingestion/nodes.py`
2. Create supporting tools in `src/ingestion/tools.py` if needed
3. Add the node to the graph in `src/ingestion/graph.py` with proper edge connections
4. Update `IngestionState` if new data fields are needed
5. Test the node with sample data

Example:

```python
# In src/ingestion/nodes.py
def validate_chunks(state: IngestionState) -> dict[str, Any]:
    """New node to validate chunk quality"""
    logger.info("Validating chunks...")
    
    valid_chunks = []
    for chunk in state.chunks:
        if len(chunk.content) > 50:  # Minimum content threshold
            valid_chunks.append(chunk)
    
    return {"chunks": valid_chunks}
```

---

## Files

| File | Purpose |
|------|---------|
| `src/config.py` | Configuration loading from .env (enhanced with retry, table, change-detection options) |
| `src/data_models.py` | Data structures (Document, TextChunk, IngestionState, TableData, DiagramMetadata) |
| `src/ingestion/nodes.py` | 9 node function implementations (6 pipeline + 3 document management) |
| `src/ingestion/tools.py` | Tool classes (PDFLoader, PDFParser, DocumentHasher, TableExtractor, DiagramExtractor, TextChunker, EmbeddingService, ChromaVectorStore), plus `media_kind` / `folder_path` and the `IMAGE_EXTS` / `VIDEO_EXTS` file types |
| `src/ingestion/layout.py` | Layout analysis: TextRepair (shifted fonts), DocumentLayout (reading order), FontHeadingDetector, `analyze_document` (sections, diagram/table section paths) |
| `src/ingestion/graph.py` | LangGraph pipeline definition with new nodes and edges |
| `src/utils/logging.py` | Logging configuration |
| `src/utils/part_numbers.py` | Part-number extraction / normalization for `part_numbers` metadata (shared with the query side) |
| `ingestion_main.py` | Entry point and pipeline execution |
| `manage_documents.py` | CLI for listing, deleting and updating ingested documents |

---

## Post-Ingestion Document Management

After documents are ingested into the vector database, use the `manage_documents.py` CLI tool to query, delete, or update them. This tool provides a user-friendly interface for document lifecycle management.

### Using the CLI Tool

```bash
# List all ingested documents
python manage_documents.py --list

# Delete a document and all its vectors
python manage_documents.py --delete report.pdf

# Prepare a document for re-ingestion
python manage_documents.py --update ./documents/report.pdf
```

---

### 7. list_documents - List Ingested Documents

**Purpose**: Query all documents currently stored in the vector database.

**Command**:
```bash
python manage_documents.py --list
```

**Output Example**:
```
====================================================================================================
ID         Document Name                                Chunks   Ingested At                   
====================================================================================================
a1b2c3d4   report.pdf                                   245      2026-11-12T10:30:45.123456
e5f6a7b8   whitepaper.pdf                               512      2026-11-12T10:35:12.789012
====================================================================================================

Total: 2 document(s) in vector database
Delete with: python manage_documents.py --delete <ID or filename>
```

**Information Returned**:
- Document ID (8 characters, derived from the filename, stable across re-ingestion)
- Document filename
- Number of chunks/vectors stored
- Ingestion timestamp
- File hash (for change detection)

**Use Cases**:
- Inventory of ingested documents
- Verify successful ingestion
- Check document status before update/delete
- Monitor storage usage

---

### 8. delete_document - Remove a Document

**Purpose**: Remove a document and all associated vectors from Chroma.

**Command** (by document ID from `--list`, or by filename):
```bash
python manage_documents.py --delete a1b2c3d4
python manage_documents.py --delete report.pdf
```

**Interaction Flow**:
```
Document: report.pdf
ID:       a1b2c3d4
Chunks:   245
Delete this document and all its vectors? This cannot be undone. (y/n): y

Success! Deleted 245 vectors for 'report.pdf'
```

**Behavior**:
- Resolves the argument as a filename first, then as a document ID (case-insensitive)
- Exits with an error if nothing matches
- Shows the document name, ID and chunk count, then asks for confirmation
- Deletes all chunks for that document from Chroma

**Use Cases**:
- Remove outdated or incorrect documents
- Clean up storage space
- Delete documents that have been superseded
- Manage document versions

**Important Notes**:
- Only removes vectors from the database
- The file in `./documents/` is NOT affected
- The document's images in `extracted_diagrams/` are not deleted yet (TODO item 10). They are replaced if the document is ingested again
- This action cannot be undone (deleted vectors are permanently removed)
- Confirmation is required before deletion

---

### 9. update_document - Prepare for Re-ingestion

**Purpose**: Delete old vectors and prepare a document for re-ingestion.

**Command**:
```bash
python manage_documents.py --update ./documents/report.pdf
```

**Interaction Flow**:
```
Preparing to update document: report.pdf
Delete old vectors and prepare for re-ingestion? (y/n): y

Success! Deleted 245 old vectors for 'report.pdf'

Next steps:
  1. Modify report.pdf if needed
  2. Ensure the file is in ./documents/
  3. Run: python ingestion_main.py

The document will be re-ingested with its latest version.
```

**Workflow**:

1. **Delete old vectors**:
   ```bash
   python manage_documents.py --update ./documents/report.pdf
   ```

2. **Modify the PDF** (optional):
   - Edit, correct, or update the PDF file
   - Save changes to the same location

3. **Re-ingest the document**:
   ```bash
   python ingestion_main.py
   ```
   - The change detection system automatically detects the updated file
   - Only the modified document is re-processed through the pipeline

**Behavior**:
- Validates that the file exists and is a type the pipeline reads (PDF, jpg/jpeg/png or mp4)
- Checks if document exists in database
- Asks for confirmation before deletion
- Deletes all existing vectors for the document
- Returns count of deleted vectors
- Does NOT automatically re-ingest (by design)
- Provides clear next steps

**Use Cases**:
- Update document with corrected information
- Fix incomplete ingestion (if error occurred first time)
- Refresh with new extraction methods (e.g., better table detection)
- Handle document version changes

**Alternative: Using Change Detection**:
Instead of manually calling `update_document()`, you can rely on automatic change detection:
1. Modify the PDF file (in-place or replace)
2. Run `python ingestion_main.py`
3. Change detection automatically re-processes the modified document (no manual deletion needed)

This is the recommended approach for most document updates.

---

## Document Management Quick Reference

| Operation | Command | Use Case |
|-----------|---------|----------|
| **List** | `python manage_documents.py --list` | View all documents, verify ingestion status |
| **Delete** | `python manage_documents.py --delete <ID or filename>` | Remove outdated/incorrect documents |
| **Update** | `python manage_documents.py --update <path>` | Delete old vectors, prepare for re-ingestion |

---

## Troubleshooting

**Error: ".env file not found"**
- Ensure `.env` exists in project root
- Check you're running from `/home/michael24/4300-CS/HaRVey`

**Error: "Missing required environment variables"**
- Verify all variables in `src/config.py` are set in `.env`
- Ensure no placeholder values remain (no `<>` brackets)

**Error: "No PDF, image or video files found"**
- Create `./documents/` directory if it doesn't exist
- Add PDF, image or mp4 files to the directory (`python tests/build_corpus.py --copy` copies the test corpus)
- Verify PDFs are readable

**Error: "Embedding API connection failed"**
- Verify `EMBEDDING_BASE_URL` is accessible
- Check API key is valid
- Ensure embedding service is running

**Error: "Chroma connection failed"**
- Verify `CHROMA_PERSIST_DIRECTORY` path exists or is writable
- Check disk space available
- Ensure no permission issues with database directory
