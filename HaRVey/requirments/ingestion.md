# Vector Ingestion Pipeline

This document describes the LangGraph-based ingestion pipeline that processes PDF documents and converts them into embeddings stored in a vector database.

## Overview

The ingestion pipeline reads PDF documents from a local directory, extracts text content, splits it into manageable chunks, generates embeddings, and persists them to Chroma for retrieval-based applications.

### Architecture Diagram

```
PDF Documents
    ↓
┌─────────────────────────┐
│  load_documents         │ Discover PDFs in input directory
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  parse_documents        │ Extract text content with pypdf
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  extract_metadata       │ Attach source information
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  chunk_text             │ Split into overlapping chunks
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  generate_embeddings    │ Call embedding service API
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  store_vectors          │ Persist to Chroma database
└─────────────┬───────────┘
              ↓
        Pipeline Complete
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

**Purpose**: Extract text content from PDF files using pypdf.

**Inputs**: List of Document objects (empty content)

**Outputs**: ParsedDocument objects with extracted text organized by page

**Responsibilities**:
- Extract all text from each PDF page
- Preserve page boundaries and text structure
- Extract optional metadata (author, creation date) from PDF properties
- Handle parsing errors gracefully without stopping the pipeline

**Key Functions**:
- Uses `pypdf` library for text extraction
- Maintains per-page text organization
- Handles encoding issues and malformed PDFs

---

### 3. extract_metadata

**Purpose**: Attach source and processing metadata to documents.

**Inputs**: ParsedDocument objects with content

**Outputs**: ParsedDocument objects with enriched metadata

**Responsibilities**:
- Extract creation date and author from PDF metadata
- Add processing timestamp
- Ensure metadata completeness for downstream filtering
- Skip documents with missing optional metadata (don't fail the pipeline)

**Key Functions**:
- Extracts PDF properties via pypdf
- Generates processing timestamps
- Validates metadata availability

---

### 4. chunk_text

**Purpose**: Split document text into fixed-size overlapping chunks suitable for embedding.

**Inputs**: ParsedDocument objects with content

**Outputs**: TextChunk objects with metadata

**Responsibilities**:
- Split text using LangChain's `RecursiveCharacterTextSplitter`
- Apply configured chunk size (default: 512 characters) and overlap (default: 50 characters)
- Map chunks back to original pages and document sources
- Skip documents that produce no valid chunks (with logging)

**Configuration**:
- Chunk size and overlap are tunable via `.env` variables
- Parameters: `CHUNK_SIZE=512`, `CHUNK_OVERLAP=50`

**Key Functions**:
- Creates metadata for each chunk including page number and sequence
- Preserves document context through overlap
- Validates chunk content before inclusion

---

### 5. generate_embeddings

**Purpose**: Convert text chunks into vector embeddings via a configured embedding service.

**Inputs**: TextChunk objects

**Outputs**: TextChunk objects with embedding vectors attached

**Responsibilities**:
- Call embedding service API with batch requests (32 chunks per batch for efficiency)
- Handle API errors and timeouts gracefully
- Skip chunks that fail embedding (with logging)
- Validate response format and vector dimensions

**Configuration**:
- Service URL: `EMBEDDING_BASE_URL`
- Model: `EMBEDDING_MODEL` (e.g., Qwen3-Embedding-8B)
- Authentication: API key from `EMBEDDING_API_KEY`
- Timeout: 30 seconds per request

**Key Functions**:
- Uses HTTP requests for API communication
- Implements batch processing for efficiency
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
    documents: list[Document]              # Original documents
    chunks: list[TextChunk]                # Text chunks with embeddings
    errors: list[dict]                     # Accumulated errors
    config: IngestionConfig                # Configuration reference
    status: str                            # Pipeline status
    documents_loaded: int                  # Progress counters
    documents_parsed: int
    chunks_created: int
    embeddings_generated: int
    vectors_stored: int
```

Each node receives this state, processes its data, and returns updated state for the next node. This enables:
- Progress tracking throughout the pipeline
- Error accumulation without stopping
- Configuration availability in all nodes
- Clear data flow and dependency management

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

```bash
# Navigate to project
cd /home/michael24/4300-CS/U0-HW-09

# Activate virtual environment
source .venv/bin/activate

# Run ingestion pipeline
python main.py
```

**Expected Output**:
- Configuration loads successfully
- All 6 nodes execute in sequence
- Summary displays statistics:
  - Documents loaded, parsed
  - Chunks created
  - Embeddings generated
  - Vectors stored
  - Any errors encountered

---

## Key Design Decisions

✅ **Synchronous Execution** - All operations are synchronous (no async/await)

✅ **Skip & Continue** - Pipeline maximizes data processing despite individual failures

✅ **High-Level Logging** - Only logs node entry/exit and summary stats, no verbose noise

✅ **Full Type Hints** - All functions and data structures are fully type-annotated

✅ **Batch Processing** - Embeddings processed in batches of 32 for efficiency

✅ **Persistent Storage** - Vectors stored persistently for retrieval across sessions

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
| `src/config.py` | Configuration loading from .env |
| `src/data_models.py` | Data structures (Document, TextChunk, IngestionState, etc.) |
| `src/ingestion/nodes.py` | 6 node function implementations |
| `src/ingestion/tools.py` | Tool classes (PDFParser, TextChunker, EmbeddingService, ChromaVectorStore) |
| `src/ingestion/graph.py` | LangGraph pipeline definition |
| `src/utils/logging.py` | Logging configuration |
| `main.py` | Entry point and pipeline execution |

---

## Troubleshooting

**Error: ".env file not found"**
- Ensure `.env` exists in project root
- Check you're running from `/home/michael24/4300-CS/U0-HW-09`

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
