# Vector Ingestion Pipeline

This document describes the LangGraph-based ingestion pipeline that processes PDF documents and converts them into embeddings stored in a vector database. The pipeline includes advanced features for table extraction, diagram detection, change tracking, and post-ingestion document management.

## Overview

The ingestion pipeline reads PDF documents from a local directory, extracts text content (including tables and diagrams), splits it into manageable chunks, generates embeddings with automatic retry logic, and persists them to Chroma for retrieval-based applications. It supports change detection to skip re-processing unchanged files and provides document lifecycle management (add, delete, update).

### Architecture Diagram

```
PDF Documents
    ↓
┌─────────────────────────┐
│  load_documents         │ Discover PDFs, calculate hashes
│                         │ (skip unchanged with detection)
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  parse_documents        │ Extract text, tables, diagrams
│                         │ (using pdfplumber + pypdf)
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  extract_metadata       │ Attach structure & enriched metadata
│                         │ (headings, timestamps, hashes)
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  chunk_text             │ Split text + create table & diagram chunks
│                         │ (with section context)
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

**Purpose**: Discover and load PDF files from the configured input directory.

**Inputs**: None (reads from `DOCUMENT_INPUT_DIR`)

**Outputs**: List of Document objects with file metadata

**Responsibilities**:
- Scan `./documents/` directory for PDF files
- Extract document-level metadata (filename, page count, timestamps)
- Validate file readability
- Handle missing or empty directories gracefully (skip and continue pattern)

**Key Functions**:
- Uses `Path.glob()` to discover PDFs
- Creates Document objects with metadata
- Logs documents found and processing status

---

### 2. parse_documents

**Purpose**: Extract text content, tables, and diagrams from PDF files.

**Inputs**: List of Document objects with file hashes

**Outputs**: ParsedDocument objects with text, tables, and diagram metadata

**Responsibilities**:
- Extract all text from each PDF page using pypdf
- Extract structured table data using pdfplumber (if enabled)
- Extract diagrams as viewable image files into `extracted_diagrams/` (see Diagram Extraction below)
- Preserve page boundaries and text structure
- Extract optional metadata (author, creation date) from PDF properties
- Handle parsing errors gracefully without stopping the pipeline
- Track table and diagram counts in metadata

**Configuration**:
- `ENABLE_TABLE_EXTRACTION=true` - Extract tables as structured data

**Key Functions**:
- Uses `pypdf` library for text extraction
- Uses `pdfplumber` for table detection and extraction
- Uses `pypdf` + `PyMuPDF` for diagram extraction
- Maintains per-page text organization
- Handles encoding issues and malformed PDFs

**Diagram Extraction**:
- Embedded raster images are extracted with `pypdf` as `{stem}_p{page}_img{N}.{ext}`; images under 50px on either side are dropped
- Vector drawings (which pypdf cannot see) are found with PyMuPDF `cluster_drawings()`, then each region is rendered at 150 dpi to `{stem}_p{page}_fig{N}.png`; regions under 40pt are ignored
- Each `DiagramMetadata` records `page`, `image_path`, `type` (file format), `kind` (`raster` or `vector_figure`) and a `caption` built from the text inside the figure region (e.g. `FIG. 5 Roof Opening Dimensions Are Nominal`)
- Figures found close together may merge into one crop
- Previously extracted files for a document are deleted before re-extracting, so re-ingesting never leaves stale images

---

### 3. extract_metadata

**Purpose**: Attach enriched metadata including document structure and content analysis.

**Inputs**: ParsedDocument objects with content, tables, and diagrams

**Outputs**: ParsedDocument objects with comprehensive metadata

**Responsibilities**:
- Extract creation date and author from PDF metadata
- Extract section heading structure from document text
- Add processing/extraction timestamp
- Ensure metadata completeness for downstream filtering
- Skip documents with missing optional metadata (don't fail the pipeline)
- Track page count and heading hierarchy

**Key Functions**:
- Extracts PDF properties via pypdf
- Detects section headings using regex patterns
- Generates processing timestamps
- Validates metadata availability

---

### 4. chunk_text

**Purpose**: Split document text into chunks, with special handling for tables and diagrams.

**Inputs**: ParsedDocument objects with content, tables, and diagrams

**Outputs**: TextChunk objects with chunk-type and source metadata

**Responsibilities**:
- Split text using LangChain's `RecursiveCharacterTextSplitter`
- Create separate chunks for table content (preserves structure)
- Create reference chunks for diagrams (links to extracted images)
- Apply configured chunk size (default: 512 characters) and overlap (default: 50 characters)
- Map chunks back to original pages and document sources
- Preserve section heading context in chunk metadata
- Skip documents that produce no valid chunks (with logging)

**Configuration**:
- Chunk size and overlap are tunable via `.env` variables
- Parameters: `CHUNK_SIZE=512`, `CHUNK_OVERLAP=50`

**Chunk Types** (`chunk_sequence` runs continuously across all three, so chunk IDs stay unique):
- `text` - Regular text chunks
- `table` - One chunk per meaningful table, never split. Content is `Table from {file}, page N:` followed by the markdown table. A strict filter drops junk tables (drawn boxes, empty grids, labels): a table needs at least 2 rows, 2 columns, 6 cells with text, and 60% of cells filled
- `diagram_reference` - `Figure on page N of {file}: {caption}`. Metadata holds `diagram_path` and `diagram_kind`. Diagrams with no caption get no chunk (see `flaws.md`)

Pages in all chunk metadata are 1-indexed. Each chunk also stores `file_hash`, `ingested_at` and `total_chunks` (used by `store_vectors` as a completeness check).

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
- Store vectors with full metadata (source, page, content)
- Enable metadata-based filtering (by document, by page)
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
CHUNK_SIZE=512
CHUNK_OVERLAP=50

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

✅ **Diagram Support** - Diagram detection and image extraction for future agent access

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
| `src/ingestion/tools.py` | Tool classes (DocumentHasher, TableExtractor, DiagramExtractor, HeadingExtractor, EmbeddingService with retry, ChromaVectorStore) |
| `src/ingestion/graph.py` | LangGraph pipeline definition with new nodes and edges |
| `src/utils/logging.py` | Logging configuration |
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
- The PDF file in `./documents/` is NOT affected
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
- Validates that the file exists and is a PDF
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

**Error: "No documents found"**
- Create `./documents/` directory if it doesn't exist
- Add PDF files to the directory
- Verify PDFs are readable

**Error: "Embedding API connection failed"**
- Verify `EMBEDDING_BASE_URL` is accessible
- Check API key is valid
- Ensure embedding service is running

**Error: "Chroma connection failed"**
- Verify `CHROMA_PERSIST_DIRECTORY` path exists or is writable
- Check disk space available
- Ensure no permission issues with database directory
